#!/usr/bin/env python3
"""0D 恢复门变异测试（#195 批 0D，L3 第 1 轮返修后重写）：把关键检查逐处改坏，跑相关测试，必须有测试失败（被抓到）。
每处改完立即还原，并用 prod_snapshot 核对本机正式目录没变（变了立刻停，退出码 3）。不是 test_*.py，全量测试不会跑它。

  python3 -B hermes/scripts/tests/mutation_0d.py [--only 序号,序号] [--out 结果文件]
在仓库根目录运行；会改工作区里的源文件（改完还原），跑的时候别在同一个工作区改代码。"""
import argparse
import subprocess
import sys
from pathlib import Path

W = Path(__file__).resolve().parents[3]
S = W / "hermes/scripts"
sys.path.insert(0, str(S / "tests"))
import prod_guard as pg  # noqa: E402
import prod_snapshot as ps  # noqa: E402

EX, RC, GR, FS, DR, PB, SG = ("test_autoloop_exits", "test_autoloop_recovery", "test_autoloop_grants",
                              "test_autoloop_feishu", "test_autoloop_drill", "test_autoloop_publish",
                              "test_secretary_gate")
# (文件, 原文, 改坏成, 守的是什么, 跑哪些测试（模块或 模块.类.方法）)
M = [
    # ---------- 出口账本 ----------
    ("autoloop_exits.py", 'if row and row["state"] == "done":', 'if False:', "做成的不重做", [EX]),
    ("autoloop_exits.py", 'if row and row["state"] in UNSETTLED:', 'if False:', "同一动作结果不明不重做", [EX]),
    ("autoloop_exits.py", '        if busy:\n', '        if False:\n', "同一范围冻结 / 串行", [EX]),
    ("autoloop_exits.py", '    return st == row["proc"]', '    return False', "只有确认进程结束才算强杀", [EX]),
    ("autoloop_exits.py", '        _finish(key, "unknown", {"why"', '        _finish(key, "failed", {"why"', "异常记结果不明", [EX]),
    ("autoloop_exits.py", '            if why:\n                raise Refused(f"{why}（{action}）")',
     '            if False:\n                raise Refused(f"{why}（{action}）")', "凭证现读判定", [EX]),
    ("autoloop_exits.py", '    if target.get("github") != row["github"]:', '    if False:', "P1-07 目标仓库绑定凭证", [EX]),
    ("autoloop_exits.py", '        if row and json.loads(row["target"]) != target:', '        if False:', "同一幂等键不能换目标", [EX]),
    ("autoloop_exits.py", '    if sha == t.get("before"):\n        return "failed"', '    if True:\n        return "failed"',
     "推分支对账：别的提交 ≠ 没推", [EX]),
    ("autoloop_exits.py", '            notes.append({"key": r["key"], "state": r["state"], "note": f"外部系统连不上，下次再对：{e}"})\n            continue',
     '            verdict, evidence = "failed", {}', "连不上不判没做成", [EX]),
    ("autoloop_exits.py", "SELECT * FROM exits WHERE state = 'unknown' ", "SELECT * FROM exits WHERE state IN ('unknown','unreconciled') ",
     "P1-01 核不清不被对账自动改", [EX]),
    ("autoloop_exits.py", "        if state == \"unknown\":\n            mark_dirty(con, f\"{key} 结果不明\")", "        pass",
     "结果不明关闸", [EX]),
    ("autoloop_exits.py", '    if stuck:\n        reconcile(scope=scope)', '    if False:\n        reconcile(scope=scope)',
     "冻结范围先对账（网络恢复后解开）", [DR + ".PushBranch.test_network_down_stays_frozen_until_back"]),
    ("autoloop_exits.py", '    if (m.get("sender") or {}).get("sender_type") != "app" or not m.get("message_id"):',
     '    if False:', "P1-05 回读只认机器人发的、有消息号的", [EX]),
    ("autoloop_exits.py", '        return any(d.get("autoloop") == t["marker"] for d in _walk(content))',
     '        return t["marker"] in raw', "P1-05 回读按按钮回调值认令牌", [EX]),
    ("autoloop_exits.py", '    return None, {"hits": 0, "note": "查不到不等于没发，交常浩核实"}', '    return "failed", {"hits": 0}',
     "P1-05 查不到不判没发", [EX]),
    ("autoloop_exits.py", '        R.admit(con, epoch)\n', '', "P1-03 领资格的事务里核闸", [RC + ".AdmissionRace"]),
    # ---------- 恢复闸 ----------
    ("autoloop_recovery.py", '    changed = [words[k] for k in words if old.get(k) != epoch.get(k)]', '    changed = []',
     "开机、睡醒、中台重启关闸", [RC]),
    ("autoloop_recovery.py", '        elif epoch["hub_commit"] != want:', '        elif False:', "版本不是锁定的不开闸", [RC]),
    ("autoloop_recovery.py", '    if want != got:\n        return "工人镜像和凭证库对不上", None', '    if False:\n        return "", None',
     "镜像对不上不开闸", [RC]),
    ("autoloop_recovery.py", '        if why is None and now_dirty != seen:', '        if False:', "对账期间新出的不明不被清掉", [RC]),
    ("autoloop_recovery.py", 'A.reconcile(queued=False)', 'A.reconcile(queued=True)', "对账时不提交排队的回答", [RC]),
    ("autoloop_recovery.py", '    if r["dirty"]:\n        raise E.Refused', '    if False:\n        raise E.Refused',
     "P1-03 admit 拦结果不明", [RC + ".AdmissionRace"]),
    ("autoloop_recovery.py", '    if r["hold"]:\n        raise E.Refused', '    if False:\n        raise E.Refused',
     "P1-06 admit 拦维护停写", [RC + ".AdmissionRace"]),
    ("autoloop_recovery.py", '    if row and row.get("hold"):\n        return f"维护中', '    if False:\n        return f"维护中',
     "P1-06 维护中闸关着", [RC + ".Maintenance"]),
    ("autoloop_recovery.py", '    if r is None or r["state"] != "held":', '    if r is None:', "P1-06 作废的恢复点不能回滚", [RC + ".Maintenance"]),
    ("autoloop_recovery.py", '        if bad:\n            raise E.Refused(f"回滚后卡片号可能错位', '        if False:\n            raise E.Refused(f"回滚后卡片号可能错位',
     "P1-06 回滚后核卡片号", [RC + ".Maintenance"]),
    ("autoloop_recovery.py", '    why = why or sec_bad\n', '', "秘书账本有结果不明不开闸", [SG + ".RecoveryGateTests"]),
    # ---------- 接线 ----------
    ("autoloop_grants.py", '            with tx(path) as con, R.admission(epoch):', '            with tx(path) as con:',
     "P1-03 派单事件逐个领取时核闸", [RC + ".AdmissionRace"]),
    ("autoloop_feishu.py", '    try:\n        R.ensure_open()\n    except E.Refused as e:\n        return {"ok": False, "paused": str(e)}',
     '    pass', "领卡前过闸", [FS + ".RecoveryGate"]),
    ("autoloop_feishu.py", '            except Deferred as e:                # 暂缓不是失败', '            except ZeroDivisionError as e:',
     "P1-04 暂缓不报 failed", [FS + ".RecoveryGate"]),
    ("autoloop_feishu.py", '            if "跑不起来" in msg and "Timeout" not in msg:', '            if True:', "P1-05 发卡报错记结果不明",
     [FS + ".RecoveryGate"]),
    ("autoloop_answer.py", '        with F.db(write=True) as con, R.admission(epoch):', '        with F.db(write=True) as con:',
     "P1-03 本人回答领资格时核闸", [RC + ".AdmissionRace"]),
    ("autoloop_answer.py", '        R.ensure_open()\n    except E.Refused:', '        pass\n    except E.Refused:',
     "P1-04 送达补报过闸", [FS + ".Answer.test_gate_closed_compensation_reports_nothing_to_hub"]),
    ("autoloop_publish.py", '            got = exit_guard("push_branch"', '            got = {"again": False} or exit_guard("push_branch"',
     "推分支经出口账本", [PB + ".ExitLedger"]),
    ("autoloop_publish.py", '    if any(s in err for s in NOT_SENT):', '    if True:', "推送报错分不清记结果不明",
     [PB + ".ExitLedger"]),
    ("autoloop_publish.py", '        raise PrUnknown(f"开 PR 报错', '        raise Rejected(f"开 PR 报错', "建 PR 报错记结果不明",
     [PB + ".ExitLedger"]),
    ("secretary_gate.py", '    with F.db(write=True) as con, R.admission(ctx.get("epoch")):', '    with F.db(write=True) as con:',
     "秘书获准时核闸", [SG + ".RecoveryGateTests"]),
    ("secretary_gate.py", '        ctx["epoch"] = recovery_open()\n', '', "秘书提议先过闸", [SG + ".RecoveryGateTests"]),
    ("secretary_gate.py", '    if result == "unknown":\n        with E.db() as con:', '    if False:\n        with E.db() as con:',
     "秘书结果不明关闸", [SG + ".RecoveryGateTests"]),
    ("secretary_gate.py", '    try:\n        R.ensure_open()\n    except E.Refused as e:\n        return {"ok": False, "paused": str(e)}',
     '    pass', "秘书领卡过闸", [SG + ".RecoveryGateTests"]),
    # ---------- 演练 ----------
    ("autoloop_exits.py", '    return st == row["proc"]', '    return True', "强杀扫描（演练：真强杀后对账、不重推）",
     [DR + ".PushBranch.test_kill_after_push_is_reconciled_not_redone"]),
    ("autoloop_exits.py", '    if st == "?" or row["proc"] in (None, "?"):\n        return True',
     '    if True:\n        return False', "P1-02 活着超 30 分钟不放行（演练：真并发）",
     [DR + ".PushBranch.test_live_executor_past_hard_stale_is_never_retried"]),
]


def run(tests):
    """按模块分组跑（discover -p 模块.py，测试目录先导入 _isolation）；带类名 / 方法名的用 -k 只跑那几个。"""
    worst, tails = 0, []
    groups = {}
    for t in tests:
        mod, _, name = t.partition(".")
        groups.setdefault(mod, []).append(name)
    for mod, names in groups.items():
        args = ["discover", "-s", "hermes/scripts/tests", "-p", f"{mod}.py"]
        for n in names:
            if n:
                args += ["-k", n]
        r = subprocess.run([sys.executable, "-B", "-m", "unittest", *args], cwd=W, capture_output=True, text=True,
                           timeout=1800)
        worst = worst or r.returncode
        tails += [x for x in r.stderr.strip().splitlines() if x.startswith(("Ran ", "OK", "FAILED"))][-2:]
    return worst, " ".join(tails)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    home = ps.REAL_HOME
    dt_dir = home / "Library/Application Support/DigitalTwin"
    # 只核这批代码可能写到的正式东西：正式中台的配置、规则、主密钥、secrets（prod_guard 的做法：哈希，中台数据库一直在写不核），
    # DigitalTwin/autoloop、工人共享的凭证镜像和任务单（prod_guard）、秘书 / 托管目录、LaunchAgents（prod_snapshot）。
    # 整个 DigitalTwin、~/.hermes 里还有日报、网关心跳等定时任务一直在写，跑二十多分钟必然有和测试无关的变化
    # （11-19 第一次跑就被 ~/.hermes、evidence 目录本身的修改时间拦下）。
    roots = [dt_dir / "secretary", dt_dir / "custody", home / "Library/LaunchAgents"]
    base = (pg.snapshot(), ps.take(roots))
    lines, caught, total = [], 0, 0
    for i, (f, old, new, what, tests) in enumerate(M, 1):
        if only and i not in only:
            continue
        total += 1
        p = S / f
        src = p.read_text(encoding="utf-8")
        if src.count(old) != 1:
            line = f"{i:2} ✗ 找不到改坏点（{src.count(old)} 处）：{what}"
        else:
            p.write_text(src.replace(old, new), encoding="utf-8")
            try:
                code, tail = run(tests)
            finally:
                p.write_text(src, encoding="utf-8")
            caught += code != 0
            line = f"{i:2} {'抓到' if code else '漏了'}：{what}（{f}）{tail}"
        print(line, flush=True)
        lines.append(line)
        try:
            ps.assert_unchanged(base[1])
            if pg.snapshot() != base[0]:
                raise ps.ProdChanged("正式中台配置或 DigitalTwin/autoloop、工人共享目录变了（prod_guard）")
        except ps.ProdChanged as e:
            print(f"!! 正式目录变了，停下：{e}")
            return 3
    summary = f"共 {total} 处，抓到 {caught} 处；每处之后正式目录都没变"
    print(summary)
    if a.out:
        Path(a.out).write_text("\n".join(lines + [summary]) + "\n", encoding="utf-8")
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
