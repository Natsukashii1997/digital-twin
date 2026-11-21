#!/usr/bin/env python3
"""常浩点中台卡片按钮后的处理（批 0B-4 第 4a 批，#195；规格 docs/秘书代批-规格.md §6、§7「本人点击锁」，0B-3 实测）。

merge-buttons 插件把 {"autoloop": 令牌, "o": 选项号, "v": 按钮所在那一版的版本号} 的点击转来：
  autoloop_answer.py <令牌> <选项号> --version <版本> --chat <网关给的聊天号> --operator <网关给的点击人>
  autoloop_answer.py reconcile [--card <卡片号>]        对账（桥接常驻时每轮也跑）
  autoloop_answer.py release --card <卡片号> --confirm  放开一张「结果不明」的卡，只在常浩明确同意后跑
核对（缺一样就不答）：点击人 = 常浩 open_id、聊天号 = 常浩私聊（网关报的聊天类型不可信，不看）、本机配了这两样——
这些不对就**不回话**，只记日志（防止别处的人反复打 /card 文本骚扰私聊）；令牌登记过、登记的私聊和现在的一致、选项号在范围。

本人每次点击是一次「尝试」，有一个不复用的尝试号；claims 表 who = owner 那一行的状态：
  queued    已记下选择，等发送：卡片还没拿到消息号、或中台还没确认送达，都先排队
  inflight  某个执行者已领到这次尝试唯一的发送资格（只能从 queued 原子改过来，改成功的才发请求），记着进程号
  answered  中台受理了，回读 state、choice、by 都对上（只说明中台最终结果和这次选择一致，中台不记尝试号）
  settled   这张卡已经有了别的结果（秘书或别处先到、卡已结束）
  unknown   结果不明：连不上、超时、HTTP 5xx、回应读不懂、回读对不上、发送中途进程没了。**不重答、不自动放开**：
            请求可能已进中台、晚些才被处理；只有回读看到「已决定 / 已结束」才能自动结算，卡还开着就交常浩决定（release）
  （释放）  只在能证明中台没受理时自动删掉这一行（claim_log 留底），常浩可以再点：请求根本没发出去，或中台明确拒绝
            （400/401/403/404/409）。409 是中台说「卡片已不是你点的那一版」（见下面「版本」）：释放后请常浩在新版卡片上重点
            （中台的版本反而更旧时不说会有新版，请人工核查，见 stale_note）
所有改状态的写入都带「尝试号 + 预期状态」做条件更新，改到 0 行就说明状态已变，放弃这次改动，不覆盖新的尝试。
发送只有一个入口 submit()：本人点击、桥接送达后的 flush、对账，都走它，所以一次尝试最多发一次。
对账（reconcile）：queued 的卡已有消息号但本地没记「中台确认送达」→ 按登记原样重报送达（中台幂等），再领发送资格；
卡已不开着就把排队的结算掉（从没发过）；inflight 超过 2 分钟且进程不在（或超过 10 分钟，防进程号复用）、unknown →
只读窗口回读：已决定且回答人是常浩、选项一致 → answered；已决定 / 已结束但不是这一答 → settled；仍开着 → 保持 unknown。
回读只认中台的四种卡片状态；缺字段或认不出的状态一律当结果不明，不结算、不放开（PR #202 审查 P1-08）。
恢复闸（批 0D，autoloop_recovery）：submit 先过 ensure_open()，开机、睡醒、中台重启或换版本、出现结果不明之后，对账走完才交中台，
关着时回答留在排队里（退出码 3）；改成 unknown 时把闸关上；恢复闸对账只调 reconcile(queued=False)，不提交排队的。
版本（#218 第 3 轮 P1-03）：按钮带着它所在那一版的版本号，回调版本 ≠ 登记的当前版本（旧版本按钮的迟到点击、或桥接还没登记
完的新版本）直接拒、不排队，请常浩在最新的卡上重点；没带版本的旧样式按钮一律拒（0B-4a 起发的卡没有版本号）。登记时在写锁里
再核一次，排队的那一行记的是回调携带的版本。
（#218 第 2 轮 P1-03）排队的选择记着点击时卡片在飞书上的版本；提交前卡片已改版（feishu_cards.version 变了）就作废、
请常浩在新版上重点，不转到新版本；中台确认送达也按版本记（autoloop_feishu）。
（#278，引擎批 C）交给中台的回答带上点的那一版（version）。中台（agent-autoloop `30f5a8e` 起，docs/protocol.md）核对：卡片还
开着、版本对不上 → 409，不记决定、卡片照旧开着；卡片已答过或结案的照旧回 changed=false。领到发送资格到 POST 之间中台改版的
口子由它堵住（规格第 9 节验收 12 的本人这一半）。更老的中台不认识 version，会当没带一样照答：要靠它把关，中台不能比这一版旧。
秘书：本人一侧从不改秘书的行；秘书已获准送达时本人照样提交，以中台先受理的为准（契约见 autoloop_feishu 头注释）。
秘书先答成功、常浩的点击迟到（中台回 changed=false，或对账看到别的结果）时，回话里补一句「秘书替你选了「X」，不同意可以推翻」
（secretary_note；推翻入口在代批汇报卡上，0B-5 做）。

已知局限（PR #202 审查 P1-01）：点击人、聊天号是网关传进来的命令行参数；和猛鬼兽同一个 macOS 账号的进程
（包括有终端工具的猛鬼兽模型）既能伪造这些参数，也能直接读 feishu 通道钥匙文件，甚至自己跑 issue-channel-key 签钥匙
（--confirm 只是命令行开关，证明不了是常浩本人）。同账号内没有软件办法堵住。常浩 2026-11-17 选甲：本批代码合并但
**不签 feishu 通道钥匙、不装桥接**，这是常浩接受的操作约定，不是技术隔离；隔离在 ADR-015 定（方向：独立账号 dt-gate +
单独的飞书机器人直接收点击，通道钥匙和签发、回答能力只在那个账号里），0C 前定，定好、重新验收后才启用。

不调用 AI。退出码：0 已答上；1 没答；2 结果不明；3 已排队等发送。
"""
import argparse
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_feishu as F  # noqa: E402

DEFINITE = (400, 401, 403, 404, 409)     # 中台明确没受理
STALE = 120                              # 秒：在途超过这么久、发送进程已不在 → 对账
HARD_STALE = 600                         # 秒：不管进程号还在不在都对账（进程号可能被复用）
SECRETARY_LIVE = ("approved", "delivered", "unknown")


class Quiet(F.Fail):
    """来源不对（不是常浩、不是私聊、令牌格式不对、本机没配身份）：不回话，只记日志。"""


def identities():
    import twin_state as T
    try:
        owner, home = T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise Quiet(str(e))
    if not owner or not home:                    # 没配置就一律不认，不拿空值去比
        raise Quiet("本机没配置常浩的 open_id 或私聊号（twin_identity.py setup / setup-chat）")
    return owner, home


def check(token, option, chat, operator, version=None):
    owner, home = identities()
    if not operator or operator != owner:
        raise Quiet("点按钮的不是常浩本人")
    if not chat or chat != home:
        raise Quiet("不是在常浩和猛鬼兽的私聊里点的")
    if not F.TOKEN_RE.match(token or ""):
        raise Quiet("按钮里的令牌格式不对")
    card = F.card_by_token(token)
    if not card:
        raise F.Fail("这张卡没有登记过（不是飞书桥接发的）")
    if card["chat_id"] != home:
        raise F.Fail("这张卡登记的私聊和现在的对不上")
    if not (0 <= option < card["options"]):
        raise F.Fail("选项号不在这张卡的选项里")
    version_ok(card, version)
    return card


def version_ok(card, version):
    """回调携带的版本必须等于登记的当前版本（#218 第 3 轮 P1-03）。"""
    if version is None:
        raise F.Fail("这个按钮是旧样式，没带版本号，确认不了你点的是哪一版；请在最新的卡片上点")
    if version != card["version"]:
        raise F.Fail(f"你点的是第 {version} 版卡片上的按钮，现在登记的是第 {card['version']} 版，这次不算；请在最新的卡片上重点")


# ---------- 状态（一律按尝试号 + 预期状态条件更新） ----------
def _age(at):
    try:
        return (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(at)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owner_row(con, card_id):
    r = con.execute("SELECT * FROM claims WHERE card_id = ? AND who = 'owner'", (card_id,)).fetchone()
    return dict(r) if r else None


def move(card_id, attempt, expect, state, note=None, pid=None):
    """把这次尝试从 expect（一个或几个状态）改成 state；不是这次尝试或状态已变就不改，返回是否改成。"""
    expect = (expect,) if isinstance(expect, str) else tuple(expect)
    if state == "unknown":                       # 结果不明：先关恢复闸、再记（同 L3 第 3 轮 P1-08），两步之间别的写入者
        import autoloop_exits as E               # 过不了闸；没改成（不是这次尝试）多关一次闸无害，对账照常开
        with E.db() as econ:
            E.mark_dirty(econ, f"卡片 {card_id} 的本人回答结果不明")
    with F.db(write=True) as con:
        cur = con.execute(f"UPDATE claims SET state = ?, at = ?, pid = ?, note = COALESCE(?, note) WHERE card_id = ? "
                          f"AND who = 'owner' AND attempt = ? AND state IN ({','.join('?' * len(expect))})",
                          (state, F.now(), pid, note, card_id, attempt, *expect))
        if cur.rowcount != 1:
            return False
        r = owner_row(con, card_id)
        F.log_claim(con, card_id, "owner", state, r["choice"], note, attempt)
    return True


def release(card_id, attempt, expect, note):
    """删掉这次尝试（能证明中台没受理，或常浩明确同意放开）；不是这次尝试或状态已变就不删。"""
    expect = (expect,) if isinstance(expect, str) else tuple(expect)
    with F.db(write=True) as con:
        r = owner_row(con, card_id)
        if not r or r["attempt"] != attempt or r["state"] not in expect:
            return False
        con.execute("DELETE FROM claims WHERE card_id = ? AND who = 'owner' AND attempt = ?", (card_id, attempt))
        F.log_claim(con, card_id, "owner", "released", r["choice"], note, attempt)
        return True


def claim(card, option, version):
    """登记本人这一答（排队），返回 (尝试号, 附言)。已有本人的行就不再登记。写锁里再核一次版本（check 之后桥接可能刚改了版）。"""
    with F.db(write=True) as con:
        cur = con.execute("SELECT version FROM feishu_cards WHERE card_id = ?", (card["card_id"],)).fetchone()
        version_ok({"version": cur["version"] if cur else None}, version)
        r = owner_row(con, card["card_id"])
        if r and r["state"] == "answered":
            raise F.Fail("这张卡你已经点过了，以第一次为准")
        if r and r["state"] == "settled":
            raise F.Fail("这张卡已经有了回答")
        if r and r["state"] == "unknown":
            raise F.Fail("上一次回答结果不明，我不会让你重复回答；对账有结果会告诉你，要放开请明确说")
        if r:
            raise F.Fail("上一次点击还在处理，先别重复点")
        sec = con.execute("SELECT state FROM claims WHERE card_id = ? AND who = 'secretary'", (card["card_id"],)).fetchone()
        note = "秘书已获准替你回答，以中台先受理的为准" if sec and sec["state"] in SECRETARY_LIVE else None
        attempt = secrets.token_hex(6)                  # 记下点的是第几版：回调携带的版本（按钮在那一版的消息上）
        con.execute("INSERT INTO claims (card_id, who, state, choice, at, pid, note, attempt, version) "
                    "VALUES (?, 'owner', 'queued', ?, ?, NULL, ?, ?, ?)",
                    (card["card_id"], option, F.now(), note, attempt, version))
        F.log_claim(con, card["card_id"], "owner", "queued", option, note, attempt)
    return attempt, note


# ---------- 提交与回读 ----------
def read_card(card_id):
    """用只读窗口看中台里这张卡；读不到返回 None。"""
    import autoloop_readonly as R
    try:
        snap = R.read()
    except Exception:                            # 只读窗口自己的错误类型不止一种：一律算读不到
        return None
    for c in snap.get("cards", []) + snap.get("recentCards", []):
        if c.get("id") == card_id:
            return c
    return None


def judge(c, choice, owner):
    """按中台里的样子判：answered / settled / open / None。只认中台的四种卡片状态（open、decided、done、closed，
    见中台 host/channels.ts）；读不到、缺 state、认不出的状态、已决定却缺 choice 或 by → None（当结果不明：
    不放开、不结算、不重答）。answered 只说明中台的最终结果和这次选择一致；中台不记尝试号，证明不了是哪一次尝试。"""
    if not c:
        return None
    state = c.get("state")
    if state == "open":
        return "open"
    if state == "closed":
        return "settled"
    if state in ("decided", "done"):
        if c.get("choice") is None or not c.get("by"):
            return None
        return "answered" if c.get("choice") == choice and c.get("by") == owner else "settled"
    return None


def ended(c):
    """中台明确说这张卡已结束（decided / done 且字段齐全，或 closed）。"""
    return judge(c, None, None) == "settled"


def describe(c):
    if not c:
        return "读不到"
    who = "秘书" if c.get("by") == "秘书" else ("你" if c.get("by") else "无人")
    return f"{c.get('state')}，{who}选了第 {c.get('choice')} 项" if c.get("choice") is not None else str(c.get("state"))


def secretary_note(c):
    """秘书先答成功、常浩的点击迟到时补的一句（规格第 7 节「你迟到的点击会显示已选的结果，并给推翻入口」）。
    推翻入口在代批汇报卡上（0B-5 做）；这里只把秘书选的那一项说清楚。不是秘书答的返回空串。"""
    if not c or c.get("by") != "秘书" or c.get("choice") is None:
        return ""
    opts = c.get("options")
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except ValueError:
            opts = None
    label = opts[c["choice"]] if isinstance(opts, list) and 0 <= c["choice"] < len(opts) else f"第 {c['choice'] + 1} 项"  # 中台下标从 0 起，给人看从 1 起
    return f"秘书替你选了「{label}」，不同意可以推翻（入口在秘书代批汇报卡上）。"


def stale_note(seen, cur, err):
    """中台按版本拒了这次回答（409）时给常浩的话。seen：点的那一版；cur：只读窗口现在读到的中台版本（读不到是 None）。"""
    if type(cur) is not int:
        return (f"中台说这张卡已经不是你点的第 {seen} 版（现在是第几版没读到），你的选择没有算数；"
                "卡片更新后请在新版卡片上重新点一次。")
    if cur > seen:
        return (f"这张卡在你点之后改版了（你点的是第 {seen} 版，中台已经是第 {cur} 版），你的选择没有算数；"
                "新版卡片送到后请在新版上重新点一次。")
    if cur < seen:                                   # 中台比飞书上的还旧：中台库可能被恢复成旧快照，不会有新版送来
        return (f"中台里这张卡是第 {cur} 版，比你点的第 {seen} 版还旧（中台库可能被恢复过），你的选择没有算数；"
                "先别重复点，这张卡要人工核查。")
    return f"这次没交上：{err}。中台没有受理，你可以再点一次。"     # 版本一样却被拒：不是版本的事，照一般的没受理说


def submit(card_id):
    """唯一的发送入口。原子领取这次尝试的发送资格（queued → inflight，且卡已有消息号、中台已确认送达），
    领到的才发请求；请求带上点的那一版，中台对不上就拒绝（409）。返回 (退出码, 给常浩的话)；没有可发的、或别人已领走，
    返回 None；还不能发返回 (3, …)。"""
    owner = identities()[0]
    import autoloop_exits as E
    import autoloop_recovery as R
    try:                                         # 恢复闸（批 0D）：对账走完才交中台；领发送资格的同一个写事务里再核开闸资格
        epoch = R.ensure_open()
        with F.db(write=True) as con, R.admission(epoch):   # 锁顺序：卡片库 → 出口账本（审查 P1-03）
            r = owner_row(con, card_id)
            card = con.execute("SELECT * FROM feishu_cards WHERE card_id = ?", (card_id,)).fetchone()
            if not r or r["state"] != "queued" or not card:
                return None
            if r["version"] != card["version"]:      # 点之后卡片改版了：旧版本的选择不转到新版本（#218 第 2 轮 P1-03）
                note = f"你点的是第 {r['version']} 版，卡片已经改成第 {card['version']} 版"
                con.execute("DELETE FROM claims WHERE card_id = ? AND who = 'owner' AND attempt = ? AND state = 'queued'",
                            (card_id, r["attempt"]))
                F.log_claim(con, card_id, "owner", "released", r["choice"], note, r["attempt"])
                return 1, f"这张卡在你点之后改版了，你的选择没有交出去，请在新版卡片上重新点一次。（{note}）"
            if not card["message_id"] or not card["hub_delivered_at"]:
                return 3, "已记下你的选择，卡片送达确认后马上交给中台。"
            cur = con.execute("UPDATE claims SET state = 'inflight', at = ?, pid = ? WHERE card_id = ? AND who = 'owner' "
                              "AND attempt = ? AND state = 'queued'", (F.now(), os.getpid(), card_id, r["attempt"]))
            if cur.rowcount != 1:
                return None
            F.log_claim(con, card_id, "owner", "inflight", r["choice"], None, r["attempt"])
            attempt, choice, ref, version = r["attempt"], r["choice"], card["message_id"], r["version"]
    except E.Refused as e:                       # 关着：回答留在排队里
        with F.db() as con:
            r = owner_row(con, card_id)
        return (3, f"已记下你的选择；{e}，对完账马上交给中台。") if r and r["state"] == "queued" else None
    try:
        out = F.hub("POST", f"/v1/cards/{card_id}/answer", {"choice": choice, "by": owner, "ref": ref, "version": version})
    except F.HubError as e:
        if not e.sent or e.status in DEFINITE:
            release(card_id, attempt, "inflight", str(e))        # 先释放，再去读版本拼话（读中台慢，中途进程没了也不留在途）
            if e.sent and e.status == 409:           # 带了版本：中台说卡片已不是点的那一版，这次不算数（#278）
                return 1, stale_note(version, (read_card(card_id) or {}).get("version"), e)
            return 1, f"这次没交上：{e}。中台没有受理，你可以再点一次。"
        move(card_id, attempt, "inflight", "unknown", str(e))
        return 2, f"这次回答结果不明（{e}），我不会重答，会对账后告诉你。"
    except Exception as e:                       # 请求可能已发出：按结果不明处理
        move(card_id, attempt, "inflight", "unknown", type(e).__name__)
        return 2, "这次回答结果不明，我不会重答，会对账后告诉你。"
    c = read_card(card_id)
    if not out.get("changed"):
        move(card_id, attempt, "inflight", "settled", f"中台说已有回答：{describe(c)}" + secretary_note(c))
        return 1, f"这张卡已经有了回答（{describe(c)}），中台只认第一次，你这次没有改变结果。" + secretary_note(c)
    if judge(c, choice, owner) == "answered":
        move(card_id, attempt, "inflight", "answered")
        return 0, "收到，已交给中台。"
    move(card_id, attempt, "inflight", "unknown", f"回读没对上：{describe(c)}")
    return 2, f"已交给中台，但回读没对上（{describe(c)}），会对账后告诉你。"


def flush(card_id):
    """桥接报送达成功后调用：有排着队的本人回答就提交（领不到发送资格就什么也不做），并在私聊回一句。"""
    try:
        got = submit(card_id)
    except F.Fail:
        return None
    if got and got[0] != 3:
        tell(identities()[1], got[1])
    return got


def reconcile(card_id=None, queued=True):
    """对账。返回给常浩看的变化列表 [(卡片号, 一句话)]；读不到中台就不动；从不重答。
    queued=False（恢复闸对账时）：只回读结果不明、在途过久的，不碰排队的——提交排队的回答是开闸以后的动作。"""
    notes = []
    try:
        owner = identities()[0]
    except F.Fail:
        return notes
    sql = "SELECT * FROM claims WHERE who = 'owner' AND state IN ('queued', 'inflight', 'unknown')"
    with F.db() as con:
        rows = [dict(r) for r in (con.execute(sql) if card_id is None else con.execute(sql + " AND card_id = ?", (card_id,)))]
    for r in rows:
        cid, attempt, age = r["card_id"], r["attempt"], _age(r["at"])
        if r["state"] == "queued":
            if queued:
                notes += _reconcile_queued(cid, attempt)
            continue
        if r["state"] == "inflight" and age < HARD_STALE and (age < STALE or pid_alive(r["pid"])):
            continue                             # 还在跑
        c = read_card(cid)
        verdict = judge(c, r["choice"], owner)
        if verdict == "answered" and move(cid, attempt, r["state"], "answered", "对账：中台里是你这一答"):
            notes.append((cid, "对账结果：你上次的回答中台已经收下了。"))
        elif verdict == "settled" and move(cid, attempt, r["state"], "settled", f"对账：{describe(c)}"):
            notes.append((cid, f"对账结果：这张卡已经有了别的结果（{describe(c)}）。" + secretary_note(c)))
        elif r["state"] == "inflight" and move(cid, attempt, "inflight", "unknown",
                                               f"对账：发送中途进程没了，回读：{describe(c)}"):
            where = "卡片还开着" if verdict == "open" else "中台这张卡的状态读不到或认不出"
            notes.append((cid, f"上次点击发送到一半中断了，结果不明，{where}；我不会重答。"
                               "要重新选，请明确告诉我放开这张卡。"))
    return notes


def _reconcile_queued(cid, attempt):
    """排队的本人回答：先按登记补报送达，再交中台。两步都往中台写，都过恢复闸（审查 P1-04）：补报送达在
    report_delivered 里入口看一眼、桥接锁里再核（闸关了抛 Deferred，先不动）；交中台在 submit 的领资格事务里核
    （闸关了留在排队）。"""
    with F.db() as con:
        card = con.execute("SELECT message_id, hub_delivered_at FROM feishu_cards WHERE card_id = ?", (cid,)).fetchone()
    if card and card["message_id"] and not card["hub_delivered_at"]:
        try:
            F.report_delivered(cid)              # 送达报告可能已被中台收下、本地没记上：按登记原样重报
        except F.Deferred:                       # 恢复闸关着：先不动，开闸后再对
            return []
        except F.HubError as e:
            if e.status in DEFINITE:             # 中台不收这份送达：看看卡还开不开
                c = read_card(cid)
                if ended(c) and move(cid, attempt, "queued", "settled", f"对账：{describe(c)}，排队的没发过"):
                    return [(cid, f"这张卡已经结束（{describe(c)}），你排队的选择没有发出去。")]
            return []
    got = submit(cid)
    return [(cid, got[1])] if got and got[0] != 3 else []


def release_unknown(card_id):
    """常浩明确同意后放开一张结果不明的卡：先回读，卡已有结果就按结果结算，不放开。"""
    owner = identities()[0]
    with F.db() as con:
        r = owner_row(con, card_id)
    if not r or r["state"] != "unknown":
        raise F.Fail(f"卡片 {card_id} 没有结果不明的本人回答")
    c = read_card(card_id)
    verdict = judge(c, r["choice"], owner)
    if verdict in ("answered", "settled"):
        move(card_id, r["attempt"], "unknown", verdict, f"放开前回读：{describe(c)}")
        return f"没放开：中台里这张卡已经是 {describe(c)}，按这个结果记了。"
    if verdict is None:
        raise F.Fail(f"中台这张卡读不到或状态认不出（{describe(c)}），先不放开")
    if not release(card_id, r["attempt"], "unknown", "常浩明确同意放开（接受原请求可能晚到、仍以中台先受理的为准）"):
        raise F.Fail("状态已经变了，没放开")
    return "已放开，可以再点。注意：上次的请求万一晚到，仍以中台先受理的为准。"


# ---------- 入口 ----------
def answer(token, option, chat, operator, version=None):
    """返回 (退出码, 给常浩的话或 None（不回话）, 记录)。"""
    try:
        card = check(token, option, chat, operator, version)
    except Quiet as e:
        return 1, None, {"ok": False, "quiet": True, "why": str(e)}
    except F.Fail as e:
        return 1, f"这次点击没有交给中台：{e}。", {"ok": False, "why": str(e)}
    try:
        F.hub_target()                           # 钥匙、配置读不到就先说，不登记
        reconcile(card["card_id"])               # 上一次在途或结果不明的，先对一次账
        attempt, note = claim(card, option, version)
    except F.Fail as e:
        return 1, f"这次点击没有交给中台：{e}。", {"ok": False, "card": card["card_id"], "why": str(e)}
    got = submit(card["card_id"])
    if got is None:                              # 别的执行者（送达后的 flush、对账）已经领走发送资格
        got = (3, "已记下你的选择，正在交给中台。")
    code, said = got
    if note:
        said += f"（{note}）"
    return code, said, {"ok": code == 0, "card": card["card_id"], "code": code, "attempt": attempt}


def tell(chat, text):
    from tidings_weekly import SendError, send
    try:
        send(chat, "text", {"text": text}, f"al-ans-{secrets.token_hex(8)}")   # 飞书幂等键限 50 字
    except SendError:
        pass                                     # 回话发不出不影响答卷结果；结果在卡片上会更新


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["reconcile"], ["release"]):
        ap = argparse.ArgumentParser(description=argv[0])
        ap.add_argument("--card", type=int, required=argv[0] == "release")
        ap.add_argument("--confirm", action="store_true")
        a = ap.parse_args(argv[1:])
        try:
            if argv[0] == "reconcile":
                out = {"notes": reconcile(a.card)}
            elif not a.confirm:
                raise F.Fail("放开结果不明的卡要常浩明确同意：加 --confirm")
            else:
                out = {"said": release_unknown(a.card)}
        except F.Fail as e:
            print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
            return 1
        print(json.dumps({"at": F.now(), **out}, ensure_ascii=False))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("token")
    ap.add_argument("option", type=int)
    ap.add_argument("--version", type=int, default=None, help="按钮所在那一版的版本号（没带就是旧样式按钮，一律拒）")
    ap.add_argument("--chat", default="")
    ap.add_argument("--operator", default="")
    ap.add_argument("--no-reply", action="store_true", help="不在私聊回话（测试用）")
    a = ap.parse_args(argv)
    code, said, rec = answer(a.token, a.option, a.chat, a.operator, a.version)
    print(json.dumps({"at": F.now(), **rec}, ensure_ascii=False), flush=True)
    if said and not a.no_reply:                  # 只回常浩私聊，不回点击来的那个聊天
        tell(identities()[1], said)
    return code


if __name__ == "__main__":
    sys.exit(main())
