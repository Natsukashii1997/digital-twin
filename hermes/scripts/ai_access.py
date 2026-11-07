"""数字分身的「AI 接入」：读配置、查执行方能不能用、打印状态、切换（全局规则 P-13；只用标准库）。

用法：
  python3 ai_access.py status [--json]              打印四块：默认执行方和各执行方状态、功能清单、对外开放、数据对象
  python3 ai_access.py check claude|codex           只查一个执行方；能用退出码 0，不能用 1
  python3 ai_access.py set default claude|codex     改车间的默认执行方（下一次派单生效）
  python3 ai_access.py set feature 功能id claude|codex|default   单独指定某个可切换功能的执行方；default 表示跟随默认

配置：仓库根目录 ai_access.json（功能清单和出厂默认值，$DT_AI_ACCESS 可改路径）；
常浩切换后的选择：~/Library/Application Support/DigitalTwin/ai-access.json（$DT_AI_CHOICE 可改路径），只存 set 写的几项。
执行方不能用时只报错、指向这里，不自动换另一家（Hermes 自己的模型回退是登记过的例外，见配置 exceptions）。
"""
import argparse
import datetime as dt
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CONFIG = Path(os.environ.get("DT_AI_ACCESS", "~/Documents/ChatGPT/个人/work/digital-twin/ai_access.json")).expanduser()
CHOICE = Path(os.environ.get("DT_AI_CHOICE", "~/Library/Application Support/DigitalTwin/ai-access.json")).expanduser()
HERMES = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
CHECK_TIMEOUT = 30
SWITCHABLE = ("claude", "codex")


class AccessError(Exception):
    pass


def find_bin(name):
    extra = ["/usr/local/bin", "/opt/homebrew/bin", str(HOME / ".local/bin"),
             *sorted(glob.glob(str(HOME / ".nvm/versions/node/*/bin")), reverse=True)]
    return shutil.which(name, path=os.pathsep.join([*os.environ.get("PATH", "").split(os.pathsep), *extra]))


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def load():
    """配置 + 常浩的选择，合成一份。读不了配置抛 AccessError。"""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise AccessError(f"读不了 AI 接入配置 {CONFIG}（{e}）")
    choice = {}
    if CHOICE.exists():
        try:
            choice = json.loads(CHOICE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise AccessError(f"读不了 AI 接入选择 {CHOICE}（{e}）；修好或删掉它再试")
    cfg["choice"] = choice
    cfg["default_worker_factory"] = cfg["default_worker"]
    if choice.get("default_worker"):
        cfg["default_worker"] = choice["default_worker"]
    for f in cfg["features"]:
        if f["id"] in choice.get("features", {}):
            f["executor"] = choice["features"][f["id"]]
    return cfg


def feature(cfg, fid):
    for f in cfg["features"]:
        if f["id"] == fid:
            return f
    raise AccessError(f"AI 接入配置里没有功能 {fid}")


def resolve(fid, cfg=None):
    """功能实际交给哪个执行方：返回 (执行方 id, 来源说明)。"""
    cfg = cfg or load()
    f = feature(cfg, fid)
    if f["executor"] == "default":
        src = "AI 接入默认执行方" + ("（常浩切换过）" if cfg["choice"].get("default_worker") else "（出厂默认）")
        return cfg["default_worker"], src
    return f["executor"], "AI 接入里单独指定"


def check(eid, cfg=None):
    """查一个可切换执行方的状态：{id, name, access, state, detail, checked_at}；state 是 可用 / 未安装 / 未登录 / 检查失败。
    额度用尽没法提前查，开跑时车间报错会写进回执。"""
    cfg = cfg or load()
    ex = cfg["executors"].get(eid)
    if not ex or "check" not in ex:
        raise AccessError(f"执行方 {eid} 没有可用性检查")
    res = {"id": eid, "name": ex["name"], "access": ex["access"], "checked_at": now()}
    cmd = list(ex["check"]["cmd"])
    exe = find_bin(cmd[0])
    if not exe:
        return {**res, "state": "未安装", "detail": f"找不到 {cmd[0]} 命令"}
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=CHECK_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {**res, "state": "检查失败", "detail": f"{' '.join(cmd)} 跑不起来（{type(e).__name__}）"}
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and ex["check"]["ok"] in out:
        return {**res, "state": "可用", "detail": f"{' '.join(cmd)} 正常"}
    tail = " ".join(out.split())[:120]
    return {**res, "state": ex["check"].get("not_logged_in", "未登录"),
            "detail": f"{' '.join(cmd)} 退出码 {r.returncode}：{tail or '无输出'}"}


def unavailable_msg(st, fid="workshop"):
    return (f"{st['name']}现在{st['state']}（{st['detail']}），这单没有开跑，也没有换成另一家。"
            f"去终端登录 {st['id']}，或改 AI 接入设置：python3 ~/.hermes/scripts/ai_access.py set "
            + ("default 另一家" if fid == "workshop" else f"feature {fid} 另一家"))


# ---------- Hermes 自己的模型（只读） ----------

def hermes_models():
    """从 ~/.hermes/config.yaml 读主模型、辅助模型、回退模型（只认这几段的两层缩进，不引第三方 YAML 库）。"""
    try:
        lines = (HERMES / "config.yaml").read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return {"error": f"读不了 Hermes 配置（{e}）"}
    out, top, sub = {}, None, None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ind = len(line) - len(line.lstrip())
        key, _, val = line.strip().partition(":")
        val = val.strip().strip("'\"")
        if ind == 0:
            top, sub = key, None
        elif top in ("model", "fallback_model") and ind == 2 and val:
            out.setdefault(top, {})[key] = val
        elif top == "auxiliary" and ind == 2:
            sub = key
        elif top == "auxiliary" and ind == 4 and sub and key in ("provider", "model") and val:
            out.setdefault("auxiliary", {}).setdefault(sub, {})[key] = val
    return out


def gateway_state():
    try:
        st = json.loads((HERMES / "gateway_state.json").read_text())
    except (OSError, ValueError) as e:
        return "检查失败", f"读不了网关状态文件（{e}）"
    fs = st.get("platforms", {}).get("feishu", {}).get("state")
    if st.get("gateway_state") == "running" and fs == "connected":
        return "可用", f"网关 running、飞书 connected（心跳 {str(st.get('updated_at', ''))[:19]}）"
    return "不可用", f"网关 {st.get('gateway_state')}、飞书 {fs}"


def hermes_usage(days=7):
    """最近实际用的模型（主对话）和近 N 天回退到 deepseek 的调用次数；读不了返回 error。"""
    try:
        con = sqlite3.connect(HERMES / "state.db", timeout=10)
        last = con.execute("select model, billing_provider, last_seen from session_model_usage where task='' "
                           "order by last_seen desc limit 1").fetchone()
        since = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
        fb = con.execute("select coalesce(sum(api_call_count),0) from session_model_usage "
                         "where billing_provider='deepseek' and last_seen>=?", (since,)).fetchone()[0]
        con.close()
    except Exception as e:
        return {"error": f"读不了 Hermes 会话库（{e}）"}
    res = {"fallback_calls": fb, "days": days}
    if last:
        res["last"] = {"model": last[0], "provider": last[1],
                       "at": dt.datetime.fromtimestamp(last[2]).isoformat(timespec="minutes") if last[2] else None}
    return res


def cron_last_model():
    try:
        rows = (HERMES / "cron/usage_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        j = json.loads(rows[-1])
        return {"model": j.get("model"), "at": j.get("ts"), "job": j.get("job_id")}
    except (OSError, ValueError, IndexError):
        return None


def session_models(title_prefix="", within_hours=3):
    """最近 within_hours 小时内、标题以 title_prefix 开头的最新一个 Hermes 定时任务会话（source=cron）实际用过的主模型
    （含回退，按先后），给日报周报留痕用；查不到返回 []。"""
    try:
        con = sqlite3.connect(HERMES / "state.db", timeout=10)
        since = (dt.datetime.now() - dt.timedelta(hours=within_hours)).timestamp()
        row = con.execute("select id from sessions where source='cron' and started_at>=? and coalesce(title,'') like ? "
                          "order by started_at desc limit 1", (since, title_prefix + "%")).fetchone()
        models = [] if not row else con.execute(
            "select model, billing_provider from session_model_usage where session_id=? and task='' "
            "order by first_seen", (row[0],)).fetchall()
        con.close()
    except Exception:
        return []
    return [f"{m} · {p}" if p else m for m, p in models]


# ---------- 状态与切换 ----------

def status(cfg):
    workers = {eid: check(eid, cfg) for eid in SWITCHABLE}
    gw, gw_detail = gateway_state()
    return {"checked_at": now(), "config": str(CONFIG), "choice_file": str(CHOICE),
            "choice_updated": cfg["choice"].get("updated_at"),
            "default_worker": cfg["default_worker"], "default_worker_factory": cfg["default_worker_factory"],
            "workers": workers,
            "hermes": {"state": gw, "detail": gw_detail, "models": hermes_models(), "usage": hermes_usage(),
                       "cron_last": cron_last_model()},
            "features": [{**f, "resolved": resolve(f["id"], cfg) if f["executor"] == "default" else None}
                         for f in cfg["features"]],
            "exceptions": cfg.get("exceptions", []), "open": cfg.get("open", []), "open_note": cfg.get("open_note"),
            "data_objects": cfg.get("data_objects", []), "no_ai": cfg.get("no_ai", [])}


def tilde(p):
    return str(p).replace(str(HOME), "~", 1)


def render(s, cfg):
    ex = cfg["executors"]
    L = [f"# 数字分身 · AI 接入（检查于 {s['checked_at']}）", ""]
    L.append("## 1. 默认执行方与各执行方状态")
    dw = s["default_worker"]
    how = "常浩切换过，" + str(s["choice_updated"]) if s["choice_updated"] else "出厂默认"
    L.append(f"- 车间默认执行方：**{ex[dw]['name']}**（{how}；切换：ai_access.py set default claude|codex）")
    for eid, st in s["workers"].items():
        L.append(f"- {st['name']}｜{st['access']}｜{st['state']}｜{st['detail']}｜额度：开跑前查不到，用完时车间报错写进回执")
    h, m = s["hermes"], s["hermes"]["models"]
    main = m.get("model", {})
    fb = m.get("fallback_model", {})
    L.append(f"- {ex['hermes']['name']}｜{ex['hermes']['access']}｜{h['state']}｜{h['detail']}")
    if m.get("error"):
        L.append(f"  - 模型配置：{m['error']}")
    else:
        L.append(f"  - 主模型 {main.get('default', '?')}（提供方 {main.get('provider', '?')}）；"
                 f"回退 {fb.get('model', '无')}（{fb.get('provider', '-')}）；切换：hermes config set model.default …，要重启网关")
        aux = "、".join(f"{k} {v.get('model', '跟随主模型')}" for k, v in m.get("auxiliary", {}).items() if v.get("model"))
        if aux:
            L.append(f"  - 辅助任务：{aux}")
    u = h["usage"]
    if u.get("error"):
        L.append(f"  - 实际用量：{u['error']}")
    else:
        last = u.get("last") or {}
        L.append(f"  - 最近一次主对话实际用的模型：{last.get('model', '无记录')}（{last.get('provider', '-')}，{last.get('at', '-')}）；"
                 f"近 {u['days']} 天回退 deepseek 调用 {u['fallback_calls']} 次")
    if h["cron_last"]:
        c = h["cron_last"]
        L.append(f"  - 带模型定时任务最近一次用的模型：{c['model']}（{str(c['at'])[:16]}，任务 {c['job']}）")
    L.append(f"- {ex['chatgpt-web']['name']}｜{ex['chatgpt-web']['access']}｜送审时查（chatgpt_relay.py status）")
    L += ["", "## 2. 功能清单", "功能｜执行方｜接入方式｜读哪些数据｜产出写到哪里"]
    for f in s["features"]:
        if f["resolved"]:
            who = f"{ex[f['resolved'][0]]['name']}（跟随默认）"
        elif f["executor"] == "per-task":
            who = "按任务的负责方（claude / codex）"
        else:
            who = ex[f["executor"]]["name"] + ("（单独指定）" if f["executor"] in SWITCHABLE else "")
        acc = ex[f["resolved"][0]]["access"] if f["resolved"] else (
            "本机 CLI" if f["executor"] in SWITCHABLE + ("per-task",) else ex[f["executor"]]["access"])
        L.append(f"- {f['name']}｜{who}｜{acc}｜{f['reads']}｜{f['writes']}")
        if f.get("note"):
            L.append(f"  - {f['note']}")
    L.append("- 不调用 AI：" + "；".join(s["no_ai"]))
    if s["exceptions"]:
        L += ["", "已登记的例外："]
        L += [f"- {e['what']}。理由：{e['why']}。批准：{e['approved']}" for e in s["exceptions"]]
    L += ["", "## 3. 对外开放"]
    L += [f"- {o}" for o in s["open"]] or [f"- 无。{s['open_note'] or ''}"]
    L += ["", "## 4. 数据对象（只统计或展示，不算执行方）"]
    L += [f"- {d['name']}｜来源 {d['source']}｜{d['used_by']}" for d in s["data_objects"]]
    L += ["", f"配置：{tilde(s['config'])}；常浩的选择：{tilde(s['choice_file'])}"]
    return "\n".join(L)


def save_choice(update):
    data = json.loads(CHOICE.read_text(encoding="utf-8")) if CHOICE.exists() else {}
    update(data)
    data["updated_at"] = now()
    CHOICE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHOICE.with_name(CHOICE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CHOICE)
    return data


def cmd_set(a, cfg):
    if a.what == "default":
        if a.value not in SWITCHABLE:
            raise AccessError(f"默认执行方只能是 {' / '.join(SWITCHABLE)}")
        save_choice(lambda d: d.update(default_worker=a.value))
        return f"车间默认执行方改为 {cfg['executors'][a.value]['name']}，下一次派单生效"
    f = feature(cfg, a.fid)
    if "choices" not in f or f["executor"] == "per-task":
        raise AccessError(f"{f['name']} 不能在这里切换（{f.get('note', '')}）")
    if a.value != "default" and a.value not in f["choices"]:
        raise AccessError(f"{f['id']} 只能选 {' / '.join(f['choices'])} 或 default")

    def upd(d):
        feats = d.setdefault("features", {})
        if a.value == "default":
            feats.pop(f["id"], None)
        else:
            feats[f["id"]] = a.value
    save_choice(upd)
    return f"{f['name']} 改为 {'跟随默认' if a.value == 'default' else cfg['executors'][a.value]['name']}，下一次调用生效"


def main(argv=None):
    ap = argparse.ArgumentParser(description="数字分身 AI 接入")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    sub.add_parser("check").add_argument("executor", choices=SWITCHABLE)
    s = sub.add_parser("set")
    ss = s.add_subparsers(dest="what", required=True)
    ss.add_parser("default").add_argument("value")
    sf = ss.add_parser("feature")
    sf.add_argument("fid")
    sf.add_argument("value")
    a = ap.parse_args(argv)
    try:
        cfg = load()
        if a.cmd == "status":
            st = status(cfg)
            print(json.dumps(st, ensure_ascii=False, indent=2) if a.json else render(st, cfg))
            return 0
        if a.cmd == "check":
            st = check(a.executor, cfg)
            print(json.dumps(st, ensure_ascii=False))
            return 0 if st["state"] == "可用" else 1
        print(cmd_set(a, cfg))
        return 0
    except AccessError as e:
        print(f"AI 接入：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
