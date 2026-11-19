#!/usr/bin/env python3
"""秘书代批控制面（#195 批 0B-4c 第 1 小批）：秘书交来的「提议」经前闸、记账、后闸、获准，再用 secretary 通道钥匙送达。

依据：秘书代批规格（docs/秘书代批-规格.md）第 3–8 节；ADR-015：「判断」和「持钥送达」分两个账号——秘书（以后在 dt-secretary）
只出提议，本脚本是控制面（以后在 dt-gate），做前后闸、持 secretary 通道钥匙送达。路径全走环境变量，整体能搬进 dt-gate。
第 2 小批（下一个 PR）才有秘书侧巡视、等待计时、① 的模型判断、②③ 出提议；本批不装常驻服务、不签钥匙、不发飞书。

提议（不可信输入，JSON，最多 16 KB；以后 dt-secretary 经窄接口调 submit）：
  {"schema": 1, "category": "ask" | "redispatch" | "reject",
   "card": 卡片号, "card_version": 卡片版本,          ← ask、redispatch（② 的卡片是中台放弃时发的通知卡）
   "choice": 第几项（从 1 数）,                       ← 只 ask；送中台时换成中台的下标 choice - 1
   "task": 任务号, "pr": PR 号, "head": 40 位提交,     ← 只 reject
   "text": {"context", "question", "summary", "basis"}  写给常浩的白话（规则同 secretary_ledger：ask 按「回复」，②③ 按「合并」）
   "run": {"session": Hermes 会话号},                 ← 只 ask：这次模型判断的运行记录（见下）
   "error": 判断出错 / 超时的说明或 null, "abstain": {"reason": 交常浩代号, "note": 说明} 或 null}
  控制面**只信选项号和白话**，其余一律从可信来源重取：卡片、轮次、事件从中台库（只读打开，同 autoloop_publish 的读法）；
  任务和代次从凭证库（只读）；开关、清单从 secretary.read_status；本人状态、飞书送达时间从 cards.db；PR 从受信发布记录。
  error、abstain 是秘书自报，只会让这件事交常浩（往保守的方向），不当成核实过的事实。
  ① 的模型运行证据（审查 P1-01：模型名、是否回退不收提议自报）：按 run.session 只读打开 Hermes 自己的运行记录
  （$HERMES_HOME/state.db，主模型和回退模型从 $HERMES_HOME/config.yaml 读），这次会话必须：source = tool、没有父会话、
  **只有一条输入**、正常结束、没调工具、开始不早于这一版卡片的飞书送达、结束不超过 30 分钟；**全部模型调用**（主循环和辅助调用）
  都是 Hermes 配置的主模型，主循环有调用（有回退模型或别的模型 → 交常浩）；输入第一行整行等于 run_marker(卡片号, 版本)；最后一条
  模型输出是一个 JSON 对象，只许有 category、card、card_version、choice、text、abstain、error，和提议逐项严格相同（类型也要一样；
  提议只是把这次输出原样转交）。②③ 不用模型（规格第 6 节），没有 run。source = tool 只是标签，不是调用者身份（ADR-015 账号隔离）。

结果（submit 返回 {"ok", "result", "msg", "decision"?}；命令行最后一行同样是一行 JSON，退出码见括号）：
  done（0）已送达并回读核实；handoff（4）记了一笔「交常浩」；not_ready（3）还不能办，过一会再交，不记账；
  refused（1）只拒、不记账（连可信事项都取不到，或秘书没开、记不了）；failed（1）确认没送达、额度已释放；
  yielded（1）常浩已经在点了，秘书让出（决定作废、额度释放）；unknown（2）结果不明：不重答，等 reconcile 按证据结算。

前闸（全部确定性；不过就记 handoff，reason 用规格第 4 节九条的代号，received 一律是可信侧取的快照）：
  格式不合格、秘书自报判断出错超时 → model；秘书主动弃权 → 它给的代号；
  卡片已不开着 / 版本变了 → refused（不用办或提议过时）；① 选项号不在范围 → model；
  等待：从 cards.db 里这张卡**这一版**的飞书送达时间起满 30 分钟（清单第 5 节「白天先等」；卡片改版后从改版送达起重算，审查 P1-02），
        托管开着（custody admission eligible）不等；没有飞书送达记录 → handoff（unsure，「你还没收到这张卡」）；登记的还不是这一版
        （桥接还没把改版送到）或没满 → not_ready；
        ① 还要秘书通道领到的是这一版、并经中台确认送达（secretary_cards 的 version 等于卡片版本、hub_delivered_at 有值）；
  ① 运行证据（见上）对不上、查不到、用了回退模型 → model；
  secretary 准入（开着、清单有效、会话清单就是当前清单）、清单里这一类是「是」、项目在清单里 → 否则 gate；
  绑定（规格第 5 节，复用 autoloop_publish.bind_run）：卡片的事项 = 任务号、规则 = 凭证的规则，所属轮次追到网关记的派单事件
        （事件号、规则名、执行方、事项、任务号、代次全对上，凭证现在有效且代次一致）→ 否则 unbound；
  卡片文字（标题、正文、选项）里有「常浩已同意 / 已授权 / 秘书请选」这类 → induced（疑似诱导）；
  这张任务账本里有结果不明 / 对账不清的秘书决定 → gate（规格第 7 节：冻结这张任务的后续代批，从账本现算）；
  ② 另核：那一轮是中台放弃（cancelled、连续 10 次没跑成），这件事没有还在排队或在跑的轮次，交接目录里没有没发出去的包 → 否则
        unsure；每张任务最多 1 次由账本额度管；
  ③ 另核：受信发布记录里有这个 PR（task_id、PR 号、提交、代次都对上）、现在 PR 开着且头提交没变、有能绑定 PR 和受审提交的审查记录、
        机器账号留言开关开着 → 否则 unsure / gate。**审查记录现在没有可信来源**（见 review_record），③ 一律交常浩。
  任务锁（审查 P1-03）：②③ 从前闸到续接事件发出，全程持着这张任务的受信发布锁（autoloop_publish.task_lock，和 publish 同一把）：
        持锁期间受信发布开不了，发布进行中秘书拿不到锁（→ unsure）；查活动轮次、查交接包、记账、获准、发续接事件都在锁里。
        同一事项的轮次由中台自己串行（kernel/decide.ts：同组同事项同时只认领一轮，新事件并进还在排队的那一轮），
        所以锁外中台新排的轮次只会排在后面，不会和新派的这一轮并跑。
记账：前闸过 → secretary_ledger.issue 预占额度（① 带卡片版本，v6 唯一约束：同卡同版本只许一个可送达决定，重复 → refused）。
后闸 + 获准（唯一的送达授权点）：持 cards.db 写锁（autoloop_feishu.db(write=True)），**锁的先后顺序固定：先 cards.db 再账本库**
  （本人点击只拿 cards.db；账本自己的写只拿账本库；没有谁先拿账本库再拿 cards.db）。锁里现读：中台卡片还开着、版本、事项、
  规则没变；凭证仍有效、代次没变；secretary 准入；任务没被结果不明冻结；① 还要秘书通道已领到、本人没占着（autoloop_feishu.owner_holds）：
  本人占着 → 作废这个决定（释放额度），claim_log 记一笔「你已经在点了，秘书让出」；否则写 claims（who = secretary，
  state = approved，attempt = 决定号）再 secretary_ledger.begin（issued → executing）。
送达：① POST /v1/cards/<号>/answer {choice, by: "秘书", ref: 秘书自己的 ref}（secretary 通道钥匙）。**① 的送达开关 REAL_ASK 关着**
  （PR #218 第 2 轮 P1-01）：中台 answer 只核卡片开着和 ref、不核版本，获准到送达之间卡片改版时旧答案会落到新版本上；
  要中台按版本核对（规格第 9 节验收 12，先向上游要扩展点）后才打开，并让请求带上获准时的版本；关着时 ① 前闸全过也交常浩；② autoloop_grants.resume_dispatch
  （redispatch）；③ 留言前再读一次 PR（开着、头提交还是受审的那个，否则没做成、不留言），机器账号在 PR 留言（REAL_COMMENT
  关着时只认测试注入的假 gh）+ resume_dispatch（rework）。
  错误分类照 autoloop_answer：没发出去或 HTTP 400/401/403/404/409 → finish(failed)、claims 删掉、额度释放；其余 → finish(unknown)、
  claims 记 unknown、不重答。回读（中台库只读）看到 decided、by = 秘书、choice 一致 → finish(done)，outcome 记 ref 和
  waited_seconds（飞书送达 → 秘书送达，给 secretary_ledger.stats）；中台说 changed=false → failed（是常浩先答的就记「你先答了」）。
reconcile：对 executing（超过 2 分钟）/ unknown / unreconciled 的秘书决定，按回读到的结构化证据调 secretary_ledger.reconcile，从不重发。
  ① 停在 executing 而 cards.db 里没有这个决定号的获准记录（claim_log 的 approved，和 claims 同一事务写）：获准没落盘，送达只在
  获准之后，确定没发 → failed（审查 P2-02：账本先提交 executing、cards.db 还没提交时被杀）。查的时候持 cards.db 写锁，不会读到
  正在获准、还没提交的那一刻。只认获准前记下身份的那一份 cards.db（received.cards_db = meta 里的库身份），用不新建、不补表的
  方式打开；文件不在、结构不全、身份对不上 → 不下结论（第 2 轮 P1-04：证据库没了不等于没有这条记录）。
lease：从 secretary 通道领一张卡，第一次领到时生成并落盘自己的 ref（cards.db 的 secretary_cards），报 delivered（同一 ref 重报幂等），
  中台收下后记「中台确认送达」；领到新版本时先清掉旧版本的确认时间，只有这一版报送达成功才记（审查 P1-02）；以后由常驻进程调（本批不装）。

  secretary_gate.py submit --file 提议.json
  secretary_gate.py reconcile [--card 卡片号]
  secretary_gate.py lease [--wait 秒]
  secretary_gate.py show                     只读
环境变量：DT_STATE_DB、DT_SECRETARY_*（同 secretary / secretary_ledger）、DT_CUSTODY_FILE、DT_AUTOLOOP_CARDS_DB、
DT_AUTOLOOP_CHANNEL_KEY_DIR（secretary 通道钥匙 <目录>/secretary-channel.key）、AGENT_AUTOLOOP_HOME（中台目录）、DT_GRANTS_DB 等
autoloop_grants 的变量、DT_PUBLISH_LOG / DT_PUBLISH_OUTBOX / DT_PUBLISH_MIRRORS（受信发布）、HERMES_HOME（① 的运行记录）。
不调用 AI。威胁模型同 custody：防模型自报、文本冒充、重放；同账号的恶意进程靠 ADR-015 的账号隔离挡，本脚本挡不住
（包括改 Hermes 运行记录：现在和秘书同一账号；分账号后控制面只读秘书账号的运行记录，0E 配权限）。
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ai_access as AA  # noqa: E402
import autoloop_config as AC  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import custody as CU  # noqa: E402
import custody_ledger as L  # noqa: E402
import secretary as S  # noqa: E402
import secretary_ledger as SL  # noqa: E402

CHANNEL = "secretary"
SECRETARY = AC.SECRETARY                    # 中台 secretary 通道 answerers 里唯一的名字
DEFINITE = (400, 401, 403, 404, 409)        # 中台明确没受理（同 autoloop_answer）
STALE = 120                                 # 秒：executing 超过这么久才对账（别的进程可能正在送）
DECISION_TTL = dt.timedelta(minutes=10)     # 记账到获准的有效期
MAX_PROPOSAL = 16 * 1024
GIVE_UP_ATTEMPTS = 10                       # 中台 kernel/decide.ts MAX_ATTEMPTS：连续 10 次没跑成就放弃
GIVE_UP_SUMMARY = f"连续 {GIVE_UP_ATTEMPTS} 次没跑成"
STOP_KEY = re.compile(r"^run:([1-9]\d{0,11}):stopped$")   # 中台自己发的放弃通知（工人回执的卡片 key 都是 thread:… 开头）
ANSWERED = ("decided", "done")
INDUCE = re.compile(r"常浩\s*(已经?|已)?\s*(同意|授权|批准|确认|点头)|已\s*(获|经)?\s*(得到)?\s*授权|秘书\s*(请|直接|可以|就)?\s*选|"
                    r"请\s*秘书|替\s*常浩\s*(选|定|答)|代\s*常浩|owner\s*(has\s*)?(approved|authori[sz]ed)", re.I)
CATEGORIES = ("ask", "redispatch", "reject")
KEYS = {"ask": {"card", "card_version", "choice"}, "redispatch": {"card", "card_version"}, "reject": {"task", "pr", "head"}}
COMMON_KEYS = {"schema", "category", "text", "error", "abstain"}
TEXT_KEYS = ("context", "question", "summary", "basis")
RUN_KEYS = ("category", "card", "card_version", "choice", "text", "abstain", "error")   # 模型输出只许有这些字段，提议逐项相同
RUN_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
RUN_SOURCE = "tool"                         # hermes chat --source tool 的单次调用
RUN_END = ("cli_close", "agent_close")      # 正常结束
RUN_FRESH = dt.timedelta(minutes=30)        # 判断结束到交提议的最长间隔
REAL_ASK = False        # ① 真送达：中台 answer 能按版本核对（CAS）之前整体关闭（PR #218 第 2 轮 P1-01，常浩 11-19 选甲，规格第 9 节验收 12）
TEST_ASK = False        # 测试在进程内打开 ① 的送达；不经环境变量
REAL_COMMENT = False    # ③ 机器账号留言：0E-4 在 dt-gate 配机器账号令牌之前整体关闭（同 autoloop_publish.REAL_PUBLISH）
TEST_GH = None          # 测试在进程内换成假 gh 的路径；不经环境变量
REVIEW_SOURCE = None    # ③ 审查记录的可信来源（见 review_record）；现在没有，测试在进程内注入


class Refused(Exception):
    """只拒、不记账（说人话的原因）。"""


class SwitchOff(Refused):
    """送达开关关着（① REAL_ASK、③ REAL_COMMENT）：前闸都过了也交常浩。"""


class NotReady(Exception):
    """还不能办：过一会再交，不记账。"""


class Handoff(Exception):
    """前闸不过：记一笔交常浩。reason 是规格第 4 节九条的代号，why 是写给常浩的白话（不超过 100 字，不带内部术语）。"""

    def __init__(self, reason, why):
        super().__init__(why)
        self.reason, self.why = reason, why


# ---------- 可信来源（只读） ----------
def _ro(path):
    """只读打开一个 SQLite 库（不存在就抛 sqlite3.Error，不新建）。"""
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def grant(task_id):
    """凭证库只读：任务凭证和作废的代次。库或这张单不在返回 None（不经 autoloop_grants.tx：那会建库）。"""
    if not G.TASK_ID.match(str(task_id or "")):
        return None
    try:
        con = _ro(G.db_path())
    except sqlite3.Error:
        return None
    try:
        if con.execute("PRAGMA user_version").fetchone()[0] != G.SCHEMA_VERSION:
            return None
        row = con.execute("SELECT * FROM grants WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "voided": G.voided_of(con, task_id)}
    except sqlite3.Error:
        return None
    finally:
        con.close()


def gateway_events(task_id, kind, since=None):
    """网关记的这类续接事件（凭证库 hub_events，只读），since（带时区的时刻）之后记的；读不了返回 None。"""
    try:
        con = _ro(G.db_path())
    except sqlite3.Error:
        return None
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM hub_events WHERE task_id = ? AND kind = ? ORDER BY rowid",
                                             (task_id, kind))]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return [r for r in rows if since is None or G.parse_iso(r["at"]) >= since]


def gateway_check(row, generation):
    """给 autoloop_publish.bind_run 的 on_dispatch：这个派单事件必须是网关记过账、已送达中台的那一条——
    事件号（中台的）、事件键、任务号、规则名、执行方、代次全对上（规格第 5 节）。"""
    def check(e):
        try:
            con = _ro(G.db_path())
        except sqlite3.Error:
            return "凭证库读不了，核不了派单事件"
        try:
            r = con.execute("SELECT * FROM hub_events WHERE event_key = ?", (str(e.get("key") or ""),)).fetchone()
        except sqlite3.Error:
            r = None
        finally:
            con.close()
        if r is None:
            return "这一轮的派单事件不是网关记过账的"
        if (r["task_id"], r["rule"], r["executor"], r["generation"], r["sent"], r["hub_event"]) != \
                (row["task_id"], row["rule"], row["executor"], generation, 1, e.get("id")):
            return "这一轮的派单事件和网关记的账对不上（事件号、规则、执行方或代次）"
        return None
    return check


def hub_runs(lane, rule, states):
    """中台库只读：这件事（事项 + 规则）在这些状态里的轮次号。"""
    try:
        con = P._hub()
    except sqlite3.Error:
        return None
    try:
        q = f"SELECT id FROM runs WHERE lane = ? AND rule = ? AND state IN ({','.join('?' * len(states))})"
        return [r[0] for r in con.execute(q, (lane, rule, *states))]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def options_of(card):
    try:
        opts = json.loads(card.get("options") or "[]")
    except (TypeError, ValueError):
        return None
    return opts if isinstance(opts, list) and all(isinstance(o, str) for o in opts) else None


def card_snapshot(card):
    """received：可信侧（中台库）取到的卡片快照。"""
    return {"source": "中台库（只读）", "card": card["id"], "version": card["version"], "kind": card["kind"],
            "state": card["state"], "title": card.get("title") or "", "text": card.get("text") or "",
            "options": options_of(card), "lane": card.get("lane"), "rule": card.get("rule"), "run_id": card.get("run_id"),
            "key": card.get("key")}


def feishu_delivery(card_id):
    """飞书桥接的送达登记（cards.db feishu_cards）：version 是最后送到常浩那里的版本，delivered_at 是它送到的时刻；没有返回 None。"""
    with F.db() as con:
        r = con.execute("SELECT version, delivered_at FROM feishu_cards WHERE card_id = ?", (card_id,)).fetchone()
    return dict(r) if r and r["delivered_at"] else None


def secretary_delivered(sc, version):
    """秘书通道领到的是这一版，并且这一版的送达经中台确认。"""
    return bool(sc) and sc["version"] == version and bool(sc["hub_delivered_at"])


def secretary_card(con, card_id):
    r = con.execute("SELECT * FROM secretary_cards WHERE card_id = ?", (card_id,)).fetchone()
    return dict(r) if r else None


def publish_record(task_id, pr):
    """受信发布记录（autoloop_publish 的 publish.jsonl）里这张单开出 / 沿用的这个 PR，取最后一条。坏行跳过。"""
    found = None
    try:
        lines = P.log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        m = re.search(r"/pull/(\d+)$", str(e.get("pr") or "")) if isinstance(e, dict) else None
        if (m and e.get("what") == "发布" and e.get("task_id") == task_id and int(m.group(1)) == pr
                and P.SHA.match(str(e.get("head") or "")) and isinstance(e.get("github"), str)):
            found = e
    return found


def unpublished_package(task_id):
    """交接目录里有没有这张任务还没发布出去的包。返回原因或 None。调用方必须持着这张任务的发布锁（task_locked），
    否则查完到续接之间受信发布可能开始（审查 P1-03）。"""
    try:
        m = P.read_manifest(task_id)
    except P.Rejected:
        return None                                          # 交接目录里没有这张单（或包不合格，发布本来就不会发）
    try:
        lines = P.log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("what") == "发布" and e.get("task_id") == task_id and e.get("head") == m["head"]:
            return None
    return "交接目录里有这张任务还没发布出去的改动"


def review_record(github, pr, head):
    """③ 的审查记录：要能绑定这个 PR 和受审提交，且出自控制面可信的组件。现在**没有**合格来源（0B-4c 第 1 小批调查结论）：
      - ChatGPT 审查回执（orca/chatgpt_relay.py）：元数据只有送审编号（送审包摘要）、正文摘要、结论，没有仓库、PR 号、受审提交
        这几个结构化字段，回执路径也由调用方随意指定；PR 和提交只可能出现在送审包正文里（自由文字），绑定不可信；
      - 影子合并官（merge_officer.py 的 agreement.csv）：有 repo、pr、head_sha、建议，但它是按 merge_rules 的规则判定
        （CI、冲突、敏感路径），不是审查本身；文件在主账号、秘书够得着，不在控制面。
    要的字段：仓库（owner/仓库）、PR 号、受审提交（40 位）、结论（退回）、审查方、记录时间，由控制面里的组件写、秘书写不到。
    来源定下之前一律返回 None（③ 交常浩）；测试在进程内注入 REVIEW_SOURCE(github, pr, head) → {"verdict": "REWORK", ...}。"""
    return REVIEW_SOURCE(github, pr, head) if REVIEW_SOURCE else None


def gh_cmd():
    """③ 留言用的 gh：0E-4 前整体关闭，只认测试注入的假 gh（同 autoloop_publish.require_publish_enabled 的做法）。"""
    if TEST_GH:
        return TEST_GH
    if REAL_COMMENT:
        return "gh"
    raise SwitchOff("机器账号还没在 dt-gate 配好（0E-4），退回 PR 的留言整体关闭")


def ask_switch():
    """① 的送达开关：中台回答还不能按版本核对，获准到送达之间卡片改版时旧答案会落到新版本上（PR #218 第 2 轮 P1-01）。
    中台有了版本核对（规格第 9 节验收 12）再打开，并让 deliver_ask 带上获准时的版本。"""
    if not (REAL_ASK or TEST_ASK):
        raise SwitchOff("中台收回答还不能按卡片版本核对，秘书替你选选项暂时整体关闭，这张交给你")


def pr_now(github, pr):
    """PR 现在的状态和头提交（gh pr view）。查不了抛 Refused。"""
    try:
        r = subprocess.run([gh_cmd(), "pr", "view", str(pr), "-R", github, "--json", "state,headRefOid"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refused(f"查 PR 失败：{type(e).__name__}")
    if r.returncode != 0:
        raise Refused(f"查 PR 失败：{r.stderr.strip()[:200]}")
    try:
        out = json.loads(r.stdout or "{}")
    except ValueError:
        raise Refused("查 PR 的输出读不懂")
    return out.get("state"), out.get("headRefOid")


# ---------- 白话 ----------
def plain(text, limit):
    """把可信侧的文字（卡片标题、选项，工人写的）变成能进账本的白话：去掉内部术语、限长。"""
    text = L.JARGON.sub("…", re.sub(r"\s+", " ", str(text or "")).strip())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def handoff_text(category, snap, why):
    """交常浩的五项白话由控制面按可信数据生成（ADR-015 第 3.3 节第 4 条）；秘书写的字不进这里。"""
    if category == "reject":
        context = plain(f"退回 PR #{snap.get('pr')}（{snap.get('task')}）", 40)
        question = "审查没过的这个 PR 要不要退回、让工人返修一轮"
        ask = "看一下审查意见，决定退不退回，跟我说一声"
    elif category == "redispatch":
        context = plain(snap.get("title") or "中台放弃的一件事", 40)
        question = plain(f"中台连续 {GIVE_UP_ATTEMPTS} 次没跑成、已经停下：要不要重新派一次", 80)
        ask = "看一下这件事，要重派就跟我说一声"
    else:
        opts = "／".join(snap.get("options") or [])
        context = plain(snap.get("title") or "中台提问", 40)
        question = plain(f"{snap.get('title') or '提问'}（选项：{opts}）", 80)
        ask = "在飞书卡片上自己点一个选项"
    return {"context": context or "中台的一件事", "question": question, "basis": plain(why, 100), "ask_owner": ask}


# ---------- 账本 ----------
def frozen_task(st, task_id, but=None):
    """这张任务有没有结果不明 / 对账不清的秘书决定（规格第 7 节：冻结后续代批，从账本现算）。"""
    rows = st.conn.execute("SELECT id FROM secretary_decisions WHERE task_id = ? AND status IN ('unknown','unreconciled')"
                           " AND id != ?", (task_id, but or "")).fetchall()
    return [r[0] for r in rows]


def record_handoff(st, ctx, reason, why, now):
    snap = ctx["received"]
    d = SL.issue(st, session_id=ctx["session"], project=ctx["project"], action="handoff", category=ctx["category"],
                 reason=reason, target=ctx["target"], revocability="irrevocable", received=snap,
                 card_version=ctx.get("card_version"), now=now, **handoff_text(ctx["category"], snap, why))
    return {"ok": True, "result": "handoff", "decision": d["id"], "reason": reason,
            "msg": f"交常浩（{SL.HANDOFF_REASONS[reason]}）：{why}"}


# ---------- 提议格式 ----------
def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def format_errors(p, cat):
    """提议格式（头注释）：字段白名单、类型；做决定的提议还要五项白话合格。返回错误列表。"""
    errs = []
    want = COMMON_KEYS | KEYS[cat] | ({"run"} if cat == "ask" else set())
    if set(p) - want:
        errs.append(f"多了不认识的字段：{'、'.join(sorted(set(p) - want))}")
    if p.get("schema") != 1:
        errs.append("schema 要是 1")
    for k in KEYS[cat]:
        v = p.get(k)
        ok = (isinstance(v, str) and P.SHA.match(v)) if k == "head" else \
            (isinstance(v, str) and G.TASK_ID.match(v)) if k == "task" else _pos_int(v)
        if not ok:
            errs.append(f"{k} 格式不对")
    if p.get("error") is not None and not (isinstance(p["error"], str) and p["error"].strip()):
        errs.append("error 要么是 null，要么写明")
    ab = p.get("abstain")
    if ab is not None and not (isinstance(ab, dict) and set(ab) <= {"reason", "note"} and ab.get("reason") in SL.HANDOFF_REASONS):
        errs.append("abstain 要写交常浩的原因代号")
    if "run" in p and cat != "ask":
        errs.append("只有选选项（①）带模型运行记录")
    if cat == "ask" and ab is None and p.get("error") is None:
        r = p.get("run")
        if not (isinstance(r, dict) and set(r) == {"session"} and isinstance(r["session"], str)
                and RUN_SESSION.match(r["session"])):
            errs.append("run 要写 {session: Hermes 会话号}")
    if ab is None and p.get("error") is None:
        t = p.get("text")
        if not (isinstance(t, dict) and set(t) <= set(TEXT_KEYS) and all(isinstance(v, str) for v in t.values())):
            errs.append("text 要是 {context, question, summary, basis} 的文字")
        else:
            errs += L.check_owner_text(SL.OWNER_RULE[cat], t)
    return errs


# ---------- ① 的模型运行证据（审查 P1-01） ----------
def hermes_home():
    return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()


def run_marker(card_id, version):
    """秘书侧给模型的输入**第一行**必须原样是这一行（整行相等，不是出现在哪里都算）：运行记录据此绑到这张卡的这一版
    （第 2 小批的秘书侧照此写）。"""
    return f"dt-secretary-card {int(card_id)} v{int(version)}"


def _same(a, b):
    """严格相同：类型也要一样（True 不等于 1，1.0 不等于 1），按 JSON 规范化后比。"""
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _json_object(text):
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", str(text or "").strip())
    try:
        out = json.loads(text)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def run_evidence(session, card, since, t):
    """按 Hermes 自己的运行记录核这次判断（头注释「① 的模型运行证据」）。返回 (证据, 模型输出的 JSON 对象)；不过抛 Handoff(model)。"""
    def no(why):
        raise Handoff("model", f"这次判断的运行记录{why}，不代批")
    home = hermes_home()
    cfg = AA.hermes_models(home)
    main = cfg.get("model") or {}
    if cfg.get("error") or not main.get("default") or not main.get("provider"):
        no("核不了（读不到 Hermes 主模型配置）")
    try:
        con = _ro(home / "state.db")
    except sqlite3.Error:
        no("查不到（Hermes 运行库读不了）")
    try:
        s = con.execute("SELECT * FROM sessions WHERE id = ?", (session,)).fetchone()
        if s is None:
            no("查不到")
        usage = con.execute("SELECT model, billing_provider, task, api_call_count FROM session_model_usage WHERE session_id = ?",
                            (session,)).fetchall()
        users = con.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id",
                            (session,)).fetchall()
        last = con.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'assistant' AND active = 1 "
                           "AND content IS NOT NULL AND content != '' ORDER BY id DESC LIMIT 1", (session,)).fetchone()
    except sqlite3.Error:
        no("读不全")
    finally:
        con.close()
    if s["source"] != RUN_SOURCE or s["parent_session_id"]:
        no("不是一次单独的判断调用")
    if s["ended_at"] is None or s["end_reason"] not in RUN_END:
        no("显示没有正常结束")
    if s["tool_call_count"]:
        no("显示判断时调用了工具")
    try:
        started = dt.datetime.fromtimestamp(float(s["started_at"]), tz=t.tzinfo)
        ended = dt.datetime.fromtimestamp(float(s["ended_at"]), tz=t.tzinfo)
    except (TypeError, ValueError, OverflowError):
        no("时间读不懂")
    if started < since:
        no("早于这一版卡片送到你那里")
    if t - ended > RUN_FRESH:
        no(f"是 {int(RUN_FRESH.total_seconds() // 60)} 分钟以前的")
    # 这次会话的全部模型调用（主循环 task='' 和标题生成、压缩等辅助调用）都要是主模型：口径是「整次判断只用了主模型」
    # （#218 第 2 轮 P2-02）。主循环必须有调用。
    if not any(r["task"] == "" and r["api_call_count"] for r in usage) or \
            any((r["model"], r["billing_provider"]) != (main["default"], main["provider"]) for r in usage):
        no("显示用了回退模型或别的模型")
    if len(users) != 1:                                    # source=tool 只是标签：要求整次会话只有一条输入（#218 第 2 轮 P2-01）
        no("不是只有一条输入的单次判断")
    head = str(users[0]["content"] or "").splitlines()
    if not head or head[0].strip() != run_marker(card["id"], card["version"]):
        no("的输入不是这张卡的这一版")
    out = _json_object(last["content"]) if last else None
    if out is None:
        no("里没有判断结果")
    return {"session": session, "model": main["default"], "provider": main["provider"],
            "started_at": CU.iso(started), "ended_at": CU.iso(ended)}, out


def run_gate(p, ctx, t):
    """模型输出只许有 RUN_KEYS 里的字段，并和提议逐项严格相同（缺省等于 null；类型也要一样）。提议本身已按 format_errors
    严格核过类型，所以输出也就是同样合格的类型（#218 第 2 轮 P1-02：error 也比，True / 1.0 不当 1）。"""
    ev, out = run_evidence(p["run"]["session"], ctx["card"], ctx["feishu_at"], t)
    if set(out) - set(RUN_KEYS) or any(not _same(out.get(k), p.get(k)) for k in RUN_KEYS):
        raise Handoff("model", "提议和这次判断的实际输出对不上，不代批")
    ctx["received"] = {**ctx["received"], "run": ev}


# ---------- 前闸 ----------
def locate(p):
    """找出提议说的是哪件事，从可信侧取快照。连这都取不到就 Refused（只拒、不记账）。"""
    if not isinstance(p, dict):
        raise Refused("提议要是一个 JSON 对象")
    cat = p.get("category")
    if "card" in p:
        if not _pos_int(p["card"]):
            raise Refused("提议里的卡片号格式不对，查不了中台")
        card = P.hub_card(p["card"])
        if not card:
            raise Refused(f"卡片 {p['card']} 在中台库里查不到，不记账")
        if card["kind"] == "ask":
            trusted = "ask"
        elif card["kind"] == "notice" and STOP_KEY.match(str(card.get("key") or "")) and not card.get("run_id"):
            trusted = "redispatch"
        else:
            raise Refused("这张卡不是秘书能代批的类型（只有提问卡、中台放弃的通知卡）")
        task = card.get("lane")
        row = grant(task)
        if row is None:
            raise Refused(f"卡片 {card['id']} 的事项不是凭证库里的任务，绑不到任何凭证，记不了账（常浩在飞书上照样能答）")
        target = {"card": card["id"], "task": task, "generation": row["generation"]} if trusted == "ask" else \
            {"task": task, "generation": row["generation"]}
        return {"category": trusted, "claimed": cat, "card": card, "row": row, "task": task, "target": target,
                "received": card_snapshot(card), "card_version": card["version"] if trusted == "ask" else None}
    if cat == "reject":
        task, pr = p.get("task"), p.get("pr")
        if not (isinstance(task, str) and G.TASK_ID.match(task) and _pos_int(pr)):
            raise Refused("退回 PR 的提议要写任务号和 PR 号")
        rec = publish_record(task, pr)
        if not rec:
            raise Refused(f"受信发布记录里没有任务 {task} 的 PR #{pr}，不记账")
        row = grant(task)
        if row is None:
            raise Refused(f"凭证库里没有任务 {task}，记不了账")
        snap = {"source": "受信发布记录", "task": task, "github": rec["github"], "pr": pr, "pr_url": rec.get("pr"),
                "head": rec["head"], "branch": rec.get("branch"), "generation": rec.get("generation")}
        return {"category": "reject", "claimed": cat, "row": row, "task": task, "rec": rec, "received": snap,
                "target": {"task": task, "repo": rec["github"], "pr": pr, "head": rec["head"]}}
    raise Refused("提议里没有能查的卡片号或 PR")


def wait_gate(ctx, t):
    """白天先等 30 分钟（从这一版在飞书成功送达起），托管开着不等。返回这一版的飞书送达时间。"""
    card = ctx["card"]
    got = feishu_delivery(card["id"])
    if got is None:
        raise Handoff("unsure", "你还没收到这张卡（飞书桥接没有送达记录），秘书不替你答")
    if got["version"] != card["version"]:                    # 改版了、桥接还没把新版本送到你那里：等它送到再从头等
        raise NotReady("这张卡改版了，飞书上还没换成这一版，过一会再交")
    sent = CU.parse_iso(got["delivered_at"])
    custody_on = CU.read_status(now=t)["admission"]["eligible"]
    if not custody_on and t - sent < dt.timedelta(minutes=S.WAIT_MINUTES):
        left = dt.timedelta(minutes=S.WAIT_MINUTES) - (t - sent)
        raise NotReady(f"白天先等你 {S.WAIT_MINUTES} 分钟，还差约 {max(1, int(left.total_seconds() // 60))} 分钟")
    if ctx["category"] == "ask":
        with F.db() as con:
            sc = secretary_card(con, card["id"])
            ctx["received"] = {**ctx["received"], "cards_db": F.db_id(con)}   # 对账时核对是不是同一份 cards.db
        if not secretary_delivered(sc, card["version"]):
            raise NotReady("秘书通道还没领到这张卡的这一版（或中台还没确认送达），过一会再交")
    return sent


def admission_gate(ctx, st_status):
    adm = st_status["admission"]
    if not adm["eligible"]:
        raise Handoff("gate", "秘书现在不能代批：" + "；".join(adm["reasons"])[:80])
    pol = st_status["policy"]
    if (pol.get("categories") or {}).get(ctx["category"]) is not True:
        raise Handoff("gate", f"代批清单里「{S.CATEGORIES[ctx['category']]}」写的是「否」")
    if ctx["project"] not in (pol.get("projects") or ()):
        raise Handoff("gate", f"项目「{ctx['project']}」不在代批清单里")


def bind_gate(ctx, t):
    row = ctx["row"]
    why = G.check_row(row, "edit", row["generation"], t, row["voided"])
    if why:
        raise Handoff("unbound", f"这件事的任务凭证现在不能用（{why}）")
    card = ctx.get("card")
    if card is None:
        return
    if (card.get("lane"), card.get("rule")) != (row["task_id"], row["rule"]):
        raise Handoff("unbound", "卡片的事项或规则和任务凭证对不上")
    run_id, states = card.get("run_id"), ("done",)
    if ctx["category"] == "redispatch":
        run_id, states = int(STOP_KEY.match(card["key"]).group(1)), ("cancelled",)
    if not run_id:
        raise Handoff("unbound", "卡片查不到是哪一轮提的")
    reason = P.bind_run(run_id, row, row["generation"], states, gateway_check(row, row["generation"]))
    if reason:
        raise Handoff("unbound", f"卡片追不到这张任务当前的派单：{reason}"[:100])


def induce_gate(ctx):
    card = ctx.get("card")
    if not card:
        return
    words = " ".join([card.get("title") or "", card.get("text") or "", *(options_of(card) or [])])
    hit = INDUCE.search(words)
    if hit:
        raise Handoff("induced", f"卡片里写着「{plain(hit.group(0), 20)}」这类话，疑似诱导，交给你看")


def redispatch_gate(ctx):
    card, row = ctx["card"], ctx["row"]
    run = P.hub_run(int(STOP_KEY.match(card["key"]).group(1)))
    if not run or run["state"] != "cancelled" or not str(run.get("summary") or "").startswith(GIVE_UP_SUMMARY):
        raise Handoff("unsure", "这一轮不是中台连续没跑成后放弃的，不按放弃重派")
    attempts = hub_attempts(run["id"])
    if attempts is None or attempts < GIVE_UP_ATTEMPTS:
        raise Handoff("unsure", "这一轮的重试次数对不上中台放弃的条件")
    live = hub_runs(row["task_id"], row["rule"], ("queued", "claimed", "unknown"))
    if live is None or live:
        raise Handoff("unsure", "这件事还有排着或在跑的轮次，不用重派")
    why = unpublished_package(row["task_id"])
    if why:
        raise Handoff("unsure", why)
    ctx["received"] = {**ctx["received"], "run": {"id": run["id"], "state": run["state"], "summary": run.get("summary"),
                                                  "attempts": attempts}}


def hub_attempts(run_id):
    try:
        con = P._hub()
    except sqlite3.Error:
        return None
    try:
        r = con.execute("SELECT attempts FROM runs WHERE id = ?", (int(run_id),)).fetchone()
        return r[0] if r and isinstance(r[0], int) else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def reject_gate(ctx, p):
    rec = ctx["rec"]
    if p.get("head") != rec["head"]:
        raise Handoff("unsure", "提议里的提交和受信发布开出的不一致")
    if rec.get("generation") != ctx["row"]["generation"]:              # 审查 P2-01：发布记录要是当前代次发的
        raise Handoff("unsure", "这个 PR 是这张任务以前的代次开出的，退不退回由你定")
    review = review_record(rec["github"], p["pr"], rec["head"])
    if not (isinstance(review, dict) and review.get("verdict") == "REWORK" and review.get("head") == rec["head"]
            and review.get("pr") == p["pr"] and review.get("repo") == rec["github"]):
        raise Handoff("unsure", "找不到能绑定这个 PR 和受审版本的审查记录，退不退回由你定")
    try:
        state, head = pr_now(rec["github"], p["pr"])
    except Refused as e:
        raise Handoff("gate", str(e)[:100])
    if state != "OPEN" or head != rec["head"]:
        raise Handoff("unsure", "这个 PR 已经不开着，或审查之后又有新提交")
    ctx["received"] = {**ctx["received"], "review": review}


def front_gates(p, ctx, st_status, t, st):
    """前闸，按顺序；不过抛 Handoff / Refused / NotReady。"""
    cat = ctx["category"]
    errs = [] if ctx["claimed"] == cat else [f"类别写成了 {ctx['claimed']!r}，这件事是 {cat}"]
    errs += format_errors(p, cat)
    if errs:
        raise Handoff("model", "秘书交来的提议格式不对：" + "；".join(errs)[:80])
    if p.get("error"):                                      # 秘书自报：只会交常浩
        raise Handoff("model", "秘书说它判断时出错或超时")
    if p.get("abstain"):
        raise Handoff(p["abstain"]["reason"], "秘书说自己没把握，交给你定")
    card = ctx.get("card")
    if card is not None:
        if card["state"] != "open":
            raise Refused(f"卡片已经不开着了（{card['state']}），不用办")
        if card["version"] != p["card_version"]:
            raise Refused(f"卡片已经变了（现在第 {card['version']} 版），提议作废，按新版本重交")
        if cat == "ask":
            opts = options_of(card)
            if not opts or not 1 <= p["choice"] <= len(opts):
                raise Handoff("model", "秘书选的选项不在卡片的选项里")
        ctx["feishu_at"] = wait_gate(ctx, t)
        if cat == "ask":
            run_gate(p, ctx, t)
    admission_gate(ctx, st_status)
    bind_gate(ctx, t)
    induce_gate(ctx)
    frozen = frozen_task(st, ctx["task"])
    if frozen:
        raise Handoff("gate", "这件事有一次代批结果还不明，对账清楚之前不再代批")
    if cat == "redispatch":
        redispatch_gate(ctx)
    if cat == "reject":
        reject_gate(ctx, p)
        gh_cmd()                                             # 留言开关关着就到此为止（SwitchOff → 交常浩，见 submit）
    if cat == "ask":
        ask_switch()                                         # 送达开关关着就到此为止（SwitchOff → 交常浩）


# ---------- 任务锁、获准（唯一的送达授权点）与送达 ----------
@contextlib.contextmanager
def task_locked(task_id):
    """这张任务的受信发布锁（和 autoloop_publish.publish 同一把，不等待）：持着时发布开不了；发布正持着就交常浩。"""
    try:
        lock = P.task_lock(task_id)
        lock.__enter__()
    except P.Rejected:
        raise Handoff("unsure", "这张任务的受信发布正在进行，秘书不同时续接")
    except OSError:
        raise Handoff("gate", "查不了这张任务的受信发布锁")
    try:
        yield
    finally:
        lock.__exit__(None, None, None)

def _yield(st, d, con, card_id, choice, note, now):
    SL.void(st, d["id"], note, now=now)
    F.log_claim(con, card_id, "secretary", "yielded", choice, note, d["id"])


def approve(st, d, ctx, now):
    """后闸 + 获准：持 cards.db 写锁（先 cards.db 再账本库），现读各项，过了才写 claims approved、账本 begin。
    返回 None 表示获准；否则返回 (结果, 原因)，决定已作废、额度已释放。"""
    t = now or CU.now_tz()
    card = ctx.get("card")
    card_id = card["id"] if card else 0
    choice = ctx.get("hub_choice")
    with F.db(write=True) as con:
        why = None
        cur = P.hub_card(card_id) if card else None
        if card and (not cur or cur["state"] != "open" or cur["version"] != card["version"]
                     or (cur.get("lane"), cur.get("rule")) != (card.get("lane"), card.get("rule"))):
            why = "获准前现读：卡片已经不开着、版本变了，或事项、规则变了"
        row = grant(ctx["task"])
        if not why and (row is None or row["generation"] != ctx["row"]["generation"]
                        or G.check_row(row, "edit", row["generation"], t, row["voided"])):
            why = "获准前现读：任务凭证已经不能用，或代次变了"
        if not why and not S.read_status(now=t)["admission"]["eligible"]:
            why = "获准前现读：秘书已经不准入"
        if not why and frozen_task(st, ctx["task"], but=d["id"]):
            why = "获准前现读：这件事有一次代批结果还不明"
        if not why and ctx["category"] == "redispatch":
            live = hub_runs(ctx["task"], ctx["row"]["rule"], ("queued", "claimed", "unknown"))
            if live is None or live or unpublished_package(ctx["task"]):
                why = "获准前现读：这件事又有轮次在跑，或有在途的发布"
        ask = ctx["category"] == "ask"
        if not why and ask:
            sc = secretary_card(con, card_id)
            live = con.execute("SELECT state FROM claims WHERE card_id = ? AND who = 'secretary'", (card_id,)).fetchone()
            if not secretary_delivered(sc, card["version"]):
                why = "获准前现读：秘书通道还没领到这张卡的这一版"
            elif F.owner_holds(con, card_id):              # 本人点击已到本机：秘书让出（规格第 7 节、第 10 节第 4 条）
                _yield(st, d, con, card_id, choice, "你已经在点了，秘书让出", now)
                return "yielded", "你已经在点这张卡了，秘书让出，以你的为准"
            elif live:
                why = f"获准前现读：秘书对这张卡已经有一次在途（{live['state']}）"
            else:
                ctx["ref"] = sc["ref"]
        if why:
            SL.void(st, d["id"], why, now=now)
            if ask:
                F.log_claim(con, card_id, "secretary", "void", choice, why, d["id"])
            return "failed", why
        try:
            SL.begin(st, d["id"], now=now)                   # 账本的提交点（issued → executing）：之后才可能发请求
        except L.LedgerError as e:                           # 过期（begin 已作废）、准入刚好变了：作废，不送
            with contextlib.suppress(L.LedgerError):
                SL.void(st, d["id"], f"获准前现读：{e}", now=now)
            return "failed", f"获准前现读：{e}"
        if ask:                                              # 同一把 cards.db 写锁里登记秘书获准；本人点击此后照样能提交，中台先受理的为准
            con.execute("INSERT INTO claims (card_id, who, state, choice, at, pid, note, attempt, version) "
                        "VALUES (?, 'secretary', 'approved', ?, ?, ?, NULL, ?, ?)",
                        (card_id, choice, F.now(), os.getpid(), d["id"], card["version"]))
            F.log_claim(con, card_id, "secretary", "approved", choice, None, d["id"])
    return None


def set_claim(card_id, decision_id, state, note=None):
    """秘书一侧的 claims：按尝试号（= 决定号）条件更新；state=None 表示删掉（确认没送达）。"""
    with F.db(write=True) as con:
        r = con.execute("SELECT * FROM claims WHERE card_id = ? AND who = 'secretary' AND attempt = ?",
                        (card_id, decision_id)).fetchone()
        if not r:
            return
        if state is None:
            con.execute("DELETE FROM claims WHERE card_id = ? AND who = 'secretary' AND attempt = ?", (card_id, decision_id))
            F.log_claim(con, card_id, "secretary", "released", r["choice"], note, decision_id)
        else:
            con.execute("UPDATE claims SET state = ?, at = ?, note = COALESCE(?, note) WHERE card_id = ? AND who = 'secretary' "
                        "AND attempt = ?", (state, F.now(), note, card_id, decision_id))
            F.log_claim(con, card_id, "secretary", state, r["choice"], note, decision_id)


def readback_ok(c, choice):
    return bool(c) and c.get("state") in ANSWERED and c.get("by") == SECRETARY and c.get("choice") == choice


def deliver_ask(st, d, ctx, now):
    card_id, choice, ref = ctx["card"]["id"], ctx["hub_choice"], ctx["ref"]
    try:                                                     # 开关在获准后被关：确定没发
        ask_switch()
    except SwitchOff as e:
        SL.finish(st, d["id"], "failed", {"error": str(e)}, now=now)
        set_claim(card_id, d["id"], None, str(e))
        return {"ok": False, "result": "failed", "decision": d["id"], "msg": str(e)}
    try:
        out = F.hub("POST", f"/v1/cards/{card_id}/answer", {"choice": choice, "by": SECRETARY, "ref": ref}, channel=CHANNEL)
    except F.HubError as e:
        if not e.sent or e.status in DEFINITE:
            SL.finish(st, d["id"], "failed", {"error": str(e)}, now=now)
            set_claim(card_id, d["id"], None, str(e))
            return {"ok": False, "result": "failed", "decision": d["id"], "msg": f"中台没有受理：{e}"}
        SL.finish(st, d["id"], "unknown", {"error": str(e)}, now=now)
        set_claim(card_id, d["id"], "unknown", str(e))
        return {"ok": False, "result": "unknown", "decision": d["id"], "msg": f"结果不明（{e}），不重答，等对账"}
    except Exception as e:                                   # 请求可能已发出：按结果不明处理
        SL.finish(st, d["id"], "unknown", {"error": type(e).__name__}, now=now)
        set_claim(card_id, d["id"], "unknown", type(e).__name__)
        return {"ok": False, "result": "unknown", "decision": d["id"], "msg": "结果不明，不重答，等对账"}
    c = P.hub_card(card_id)
    if not out.get("changed"):
        first = bool(c) and c.get("state") in ANSWERED and c.get("by") not in (None, SECRETARY)
        note = "你先答了" if first else f"中台说这张卡已经有结果（{(c or {}).get('state')}）"
        SL.finish(st, d["id"], "failed", {"note": note}, now=now)
        set_claim(card_id, d["id"], None, note)
        return {"ok": False, "result": "failed", "decision": d["id"], "msg": f"没答上：{note}"}
    if readback_ok(c, choice):
        t = now or CU.now_tz()
        waited = max(0, int((t - ctx["feishu_at"]).total_seconds()))
        SL.finish(st, d["id"], "done", {"ref": ref, "answered_at": CU.iso(t), "waited_seconds": waited}, now=now)
        set_claim(card_id, d["id"], "delivered")
        return {"ok": True, "result": "done", "decision": d["id"], "msg": f"已替你答了卡片 {card_id}，回读核实"}
    SL.finish(st, d["id"], "unknown", {"error": "回读没对上"}, now=now)
    set_claim(card_id, d["id"], "unknown", "回读没对上")
    return {"ok": False, "result": "unknown", "decision": d["id"], "msg": "已交给中台，但回读没对上，等对账"}


def _resume(st, d, kind, reason, now, partial=False):
    """经网关续接（② 重派、③ 返修）。按 resume_dispatch 的结果分类：凭证当场不放行（还没记事件）→ failed；
    事件已记、发出且中台给了事件号 → done；其余（发送结果不明、镜像写不成、事件还排着）→ unknown（网关以后补发，对账看它）。
    partial：前面已经做了撤不回的一步（③ 的留言已发出），这时网关不放行也只能记结果不明，交对账、不重发。"""
    try:
        out = G.resume_dispatch(d["task_id"], kind, reason, now=now)
    except G.Rejected as e:
        result = "unknown" if partial else "failed"
        SL.finish(st, d["id"], result, {"error": str(e), **({"note": "留言已发出、返修没派出"} if partial else {})}, now=now)
        return {"ok": False, "result": result, "decision": d["id"], "msg": f"网关没有续接：{e}" + ("（留言已发出）" if partial else "")}
    except Exception as e:                                   # 可能已记了事件：按结果不明处理
        SL.finish(st, d["id"], "unknown", {"error": type(e).__name__}, now=now)
        return {"ok": False, "result": "unknown", "decision": d["id"], "msg": "续接结果不明，等对账"}
    key = out["event_key"]
    sent = out["send"]
    hit = next((x for x in sent if x.get("event_key") == key), None) if isinstance(sent, list) else None
    if hit and hit.get("sent") is True:
        SL.finish(st, d["id"], "done", {"ref": key, "event_key": key, "hub_event": hit.get("hub_event")}, now=now)
        return {"ok": True, "result": "done", "decision": d["id"], "msg": f"已经由网关续接（{kind}）"}
    SL.finish(st, d["id"], "unknown", {"event_key": key, "send": sent if isinstance(sent, dict) else hit}, now=now)
    return {"ok": False, "result": "unknown", "decision": d["id"], "msg": "续接事件已记账、还没确认发到中台，等对账"}


def deliver_reject(st, d, ctx, now):
    rec, pr = ctx["rec"], ctx["target"]["pr"]
    try:                                                     # 留言前再读一次 PR（审查 P2-01）：关了或推了新提交就不留言
        state, head = pr_now(rec["github"], pr)
    except Refused as e:
        SL.finish(st, d["id"], "failed", {"error": str(e)[:200]}, now=now)
        return {"ok": False, "result": "failed", "decision": d["id"], "msg": f"留言前查不了 PR，没留言：{e}"}
    if state != "OPEN" or head != rec["head"]:
        SL.finish(st, d["id"], "failed", {"note": f"留言前现读：PR {state}，头提交 {str(head)[:12]}"}, now=now)
        return {"ok": False, "result": "failed", "decision": d["id"], "msg": "留言前现读：PR 已经不开着或有了新提交，没留言"}
    body = (f"秘书按常浩签的代批清单退回这个 PR，派工人返修一轮。\n\n{d['summary']}\n\n{d['basis']}\n\n"
            f"<!-- dt-secretary {d['id']} -->")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
    try:
        r = subprocess.run([gh_cmd(), "pr", "comment", str(pr), "-R", rec["github"], "-F", f.name],
                           capture_output=True, text=True, timeout=120)
    except (OSError, Refused) as e:                          # gh 根本没跑起来：确定没留言
        SL.finish(st, d["id"], "failed", {"error": str(e)[:200]}, now=now)
        return {"ok": False, "result": "failed", "decision": d["id"], "msg": f"留言没发出去：{e}"}
    except subprocess.SubprocessError as e:
        SL.finish(st, d["id"], "unknown", {"error": type(e).__name__}, now=now)
        return {"ok": False, "result": "unknown", "decision": d["id"], "msg": "留言结果不明，等对账"}
    finally:
        os.unlink(f.name)
    if r.returncode != 0:                                    # gh 报错分不清发没发：结果不明，不重发
        SL.finish(st, d["id"], "unknown", {"error": r.stderr.strip()[:200]}, now=now)
        return {"ok": False, "result": "unknown", "decision": d["id"], "msg": "留言结果不明，等对账"}
    return _resume(st, d, "rework", f"秘书代批 {d['id']}：审查没过，退回返修", now, partial=True)


# ---------- 入口 ----------
def submit(proposal, now=None):
    """处理一个提议。now 只给测试用（固定时间）；不给就各处现取。返回结果字典（头注释）。"""
    t = now or CU.now_tz()
    try:
        ctx = locate(proposal)
        st_status = S.read_status(now=t)
        sess = st_status.get("session") or {}
        if not sess.get("id"):
            raise Refused("秘书没开（没有当前会话），交常浩的这一笔也记不了")
        ctx.update(session=sess["id"], project=ctx["row"]["repo"])
    except Refused as e:
        return {"ok": False, "result": "refused", "msg": str(e)}
    try:
        with SL.open_store(now) as st, contextlib.ExitStack() as held:
            try:
                if ctx["category"] in ("redispatch", "reject"):     # 从前闸到续接发出全程持锁（审查 P1-03）
                    held.enter_context(task_locked(ctx["task"]))
                front_gates(proposal, ctx, st_status, t, st)
            except Handoff as h:
                return record_handoff(st, ctx, h.reason, h.why, now)
            except SwitchOff as e:
                return record_handoff(st, ctx, "gate", str(e)[:100], now)
            except Refused as e:
                if ctx["category"] == "reject":              # ③ 留言开关关着：交常浩
                    return record_handoff(st, ctx, "gate", str(e)[:100], now)
                return {"ok": False, "result": "refused", "msg": str(e)}
            except NotReady as e:
                return {"ok": False, "result": "not_ready", "msg": str(e)}
            cat = ctx["category"]
            if cat == "ask":
                ctx["hub_choice"] = proposal["choice"] - 1
            reply = {"choice": proposal["choice"]} if cat == "ask" else {"redispatch": True} if cat == "redispatch" else \
                {"reject": True, "comment": True, "rework": True}
            try:
                d = SL.issue(st, session_id=ctx["session"], project=ctx["project"], action=cat, target=ctx["target"],
                             revocability="correctable", expires_at=t + DECISION_TTL, received=ctx["received"], reply=reply,
                             card_version=ctx.get("card_version"), now=now,
                             **{k: proposal["text"].get(k, "") for k in TEXT_KEYS})
            except L.LedgerError as e:
                if e.code == "DuplicateCard":
                    return {"ok": False, "result": "refused", "msg": str(e)}
                return record_handoff(st, ctx, "model" if e.code in ("BadText", "BadInput") else "gate", str(e)[:100], now)
            got = approve(st, d, ctx, now)
            if got:
                return {"ok": False, "result": got[0], "decision": d["id"], "msg": got[1]}
            if cat == "ask":
                return deliver_ask(st, d, ctx, now)
            if cat == "redispatch":
                return _resume(st, d, "redispatch", f"秘书代批 {d['id']}：中台放弃后重派一次", now)
            return deliver_reject(st, d, ctx, now)
    except L.LedgerError as e:
        return {"ok": False, "result": "refused", "msg": f"账本记不了：{e}"}


def _age(at):
    try:
        return (CU.now_tz() - CU.parse_iso(at)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def reconcile(card=None, now=None):
    """对 executing（超过 2 分钟）/ unknown / unreconciled 的秘书决定按回读证据结算；从不重发。返回 [(决定号, 结论)]。"""
    notes = []
    with SL.open_store(now) as st:
        rows = st.conn.execute("SELECT id FROM secretary_decisions WHERE action != 'handoff' AND status IN "
                               "('executing','unknown','unreconciled')" + (" AND card_id = ?" if card else "")
                               + " ORDER BY created_at", (card,) if card else ()).fetchall()
        for (did,) in rows:
            d = SL.get(st, did)
            if d["status"] == "executing" and _age(d["updated_at"]) < STALE:
                continue                                     # 可能正在送
            got = _verdict(st, d)
            if not got or (got[0] == "unreconciled" and d["status"] == "unreconciled"):
                continue
            SL.reconcile(st, did, got[0], got[1], now=now)
            if d["action"] == "ask":
                set_claim(d["card_id"], did, {"done": "delivered", "failed": None, "unreconciled": "unknown"}[got[0]],
                          f"对账：{got[1]['checked']}")
            notes.append((did, got[0]))
    return notes


def _verdict(st, d):
    """按回读证据给结论：(结论, 证据)；读不到返回 None（不动）。"""
    if d["action"] == "ask":
        if d["status"] == "executing":                       # 审查 P2-02：获准记录没落盘就确定没发（持写锁读，见头注释）
            want = ((L.read_raw(d) or {}).get("received") or {}).get("cards_db")
            try:                                             # 第 2 轮 P1-04：只认获准时那一份、结构完整的 cards.db，不新建
                with F.db(write=True, create=False) as con:
                    if not want or F.db_id(con) != want:
                        return None
                    approved = con.execute("SELECT 1 FROM claim_log WHERE card_id = ? AND who = 'secretary' AND attempt = ? "
                                           "AND state = 'approved'", (d["card_id"], d["id"])).fetchone()
            except (F.Fail, sqlite3.Error):
                return None                                  # 证据库不在、不全或换了：核不了，不下结论
            if not approved:
                return "failed", {"checked": "cards.db 里没有这个决定的获准记录：获准没落盘，送达只在获准之后，确定没发",
                                  "found": False}
        raw = L.read_raw(d) or {}
        choice = (raw.get("reply") or {}).get("choice")
        c = P.hub_card(d["card_id"])
        if not c or not _pos_int(choice):
            return None
        checked = f"中台库卡片 {d['card_id']}：{c.get('state')}，回答人 {c.get('by') or '无'}，选项 {c.get('choice')}"
        if readback_ok(c, choice - 1):
            with F.db() as con:
                sc = secretary_card(con, d["card_id"])
            return "done", {"checked": checked, "found": True, "ref": (sc or {}).get("ref") or "秘书通道"}
        if c.get("state") in ANSWERED + ("closed",):
            return "failed", {"checked": checked, "found": False}
        return "unreconciled", {"checked": checked + "（还开着：请求可能晚到，不重答）"}
    kind = {"redispatch": "redispatch", "reject": "rework"}[d["action"]]
    key = d["outcome"].get("event_key")
    began = st.conn.execute("SELECT at FROM events WHERE object_type = ? AND object_id = ? AND kind = ? ORDER BY id LIMIT 1",
                            (SL.SECRETARY.obj, d["id"], f"{SL.SECRETARY.event}.executing")).fetchone()
    evs = gateway_events(d["task_id"], kind, CU.parse_iso(began[0] if began else d["created_at"]))
    if evs is None:
        return None
    if key:
        evs = [e for e in evs if e["event_key"] == key]
    if not evs:
        return ("unreconciled", {"checked": "网关没有这次续接的事件记录"}) if d["action"] == "reject" else \
            ("failed", {"checked": "网关没有这次续接的事件记录（网关先记账后发，没记就没发）", "found": False})
    e = evs[0]
    checked = f"网关事件 {e['event_key']}：sent={e['sent']}"
    if e["sent"] == 1:
        return "done", {"checked": checked, "found": True, "ref": e["event_key"]}
    if e["sent"] == -1:
        return "failed", {"checked": checked + "（作废未发）", "found": False}
    return None if e["sent"] in (0, 2) else ("unreconciled", {"checked": checked})


def lease(wait=50):
    """从 secretary 通道领一张卡：第一次领到时生成并落盘自己的 ref，报送达；中台收下后记「中台确认送达」。没有卡返回 None。"""
    card = F.hub("GET", f"/v1/cards/next?wait={wait}", timeout=wait + 20, channel=CHANNEL).get("card")
    if not card:
        return None
    cid, why = card["id"], None
    with F.db(write=True) as con:
        r = secretary_card(con, cid)
        if r:
            if card.get("ref") and card["ref"] != r["ref"]:
                why = "中台记的秘书 ref 和本地登记的对不上"
            else:                                            # 新版本：旧版本的确认不代表新版本（审查 P1-02）
                if card["version"] > r["version"]:
                    con.execute("UPDATE secretary_cards SET version = ?, hub_delivered_at = NULL WHERE card_id = ?",
                                (card["version"], cid))
                ref = r["ref"]
        elif card.get("ref"):
            why = "中台说这张卡已经送达过秘书通道，本地却没有登记"
        else:
            ref = "sec-" + secrets.token_hex(8)
            con.execute("INSERT INTO secretary_cards (card_id, version, ref, leased_at) VALUES (?, ?, ?, ?)",
                        (cid, card["version"], ref, F.now()))
    if why:
        F.hub("POST", f"/v1/cards/{cid}/failed", {"version": card["version"], "error": why}, channel=CHANNEL)
        return {"card": cid, "ok": False, "why": why}
    F.hub("POST", f"/v1/cards/{cid}/delivered", {"version": card["version"], "ref": ref}, channel=CHANNEL)
    with F.db(write=True) as con:                            # 只给报过的这一版记确认；其间又领到更新的版本就不记
        con.execute("UPDATE secretary_cards SET hub_delivered_at = COALESCE(hub_delivered_at, ?) WHERE card_id = ? AND version = ?",
                    (F.now(), cid, card["version"]))
    return {"card": cid, "ok": True, "ref": ref}


def show():
    out = {"decisions": [], "cards": [], "claims": []}
    with contextlib.suppress(L.LedgerError, L.TS.StateError, sqlite3.Error):
        with L.TS.Store(L.db_path(), readonly=True) as s:
            out["decisions"] = [dict(r) for r in s.conn.execute(
                "SELECT id, action, category, task_id, card_id, card_version, status, reason, created_at FROM secretary_decisions"
                " ORDER BY created_at DESC LIMIT 50")]
    with F.db() as con:
        out["cards"] = [dict(r) for r in con.execute("SELECT card_id, version, leased_at, hub_delivered_at FROM secretary_cards"
                                                     " ORDER BY card_id")]
        out["claims"] = [dict(r) for r in con.execute("SELECT * FROM claims WHERE who = 'secretary' ORDER BY card_id")]
    return out


def done_line(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False))


EXIT = {"done": 0, "refused": 1, "failed": 1, "yielded": 1, "unknown": 2, "not_ready": 3, "handoff": 4}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("submit").add_argument("--file", required=True)
    sub.add_parser("reconcile").add_argument("--card", type=int)
    sub.add_parser("lease").add_argument("--wait", type=int, default=50)
    sub.add_parser("show")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "submit":
            try:
                raw = Path(a.file).read_bytes()
                if len(raw) > MAX_PROPOSAL:
                    raise ValueError("提议太大")
                proposal = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError) as e:
                done_line(False, f"提议文件读不了或不是 JSON（{type(e).__name__}），连卡片都取不到，只拒不记账", result="refused")
                return 1
            out = submit(proposal)
            done_line(out["ok"], out["msg"], **{k: v for k, v in out.items() if k not in ("ok", "msg")})
            return EXIT[out["result"]]
        if a.cmd == "reconcile":
            notes = reconcile(a.card)
            done_line(True, f"对账 {len(notes)} 个决定", notes=notes)
            return 0
        if a.cmd == "lease":
            out = lease(a.wait)
            done_line(bool(out is None or out["ok"]), "没有卡片" if out is None else (out.get("why") or f"领到卡片 {out['card']}"),
                      **(out or {}))
            return 0 if out is None or out["ok"] else 1
        print(json.dumps(show(), ensure_ascii=False, indent=1))
        done_line(True, "只读")
        return 0
    except (F.Fail, L.LedgerError, L.TS.StateError, sqlite3.Error) as e:
        done_line(False, str(e), error=getattr(e, "code", type(e).__name__))
        return 1


if __name__ == "__main__":
    sys.exit(main())
