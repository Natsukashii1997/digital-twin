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
    ("autoloop_exits.py", '    ev = {"remote_head": sha, "before": t.get("before")}\n',
     '    ev = {"remote_head": sha, "before": t.get("before")}\n    if sha == t.get("before"):\n        return "failed", ev\n',
     "P1-10 推分支对账：远端等于推之前 ≠ 没推", [EX, PB + ".ExitLedger", DR + ".PushBranch"]),
    ("autoloop_exits.py", '    ev = {"remote_head": sha, "before": t.get("before")}\n',
     '    ev = {"remote_head": sha, "before": t.get("before")}\n    if sha is not None:\n        return "failed", ev\n',
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
    ("autoloop_recovery.py", '"secretary": sec_bad or sec_note})\n    why = why or sec_bad\n',
     '"secretary": sec_bad or sec_note})\n', "秘书账本有结果不明不开闸",
     [RC + ".Order.test_secretary_unknown_keeps_gate_closed", SG + ".RecoveryGateTests"]),
    # ---------- 接线 ----------
    ("autoloop_grants.py", '            with tx(path) as con, R.admission(epoch):', '            with tx(path) as con:',
     "P1-03 派单事件逐个领取时核闸", [RC + ".AdmissionRace"]),
    ("autoloop_feishu.py", '    try:\n        epoch = R.ensure_open()\n    except E.Refused as e:\n        return {"ok": False, "paused": str(e)}\n    if not hub_readable():',
     '    epoch = None\n    if not hub_readable():', "领卡前过闸", [FS + ".RecoveryGate"]),
    ("autoloop_feishu.py", '            except Deferred as e:                # 暂缓不是失败', '            except ZeroDivisionError as e:',
     "P1-04 暂缓不报 failed", [FS + ".RecoveryGate"]),
    ("autoloop_feishu.py", '            if "跑不起来" in msg and "Timeout" not in msg:', '            if True:', "P1-05 发卡报错记结果不明",
     [FS + ".RecoveryGate"]),
    ("autoloop_answer.py", '        with F.db(write=True) as con, R.admission(epoch):', '        with F.db(write=True) as con:',
     "P1-03 本人回答领资格时核闸", [RC + ".AdmissionRace"]),
    ("autoloop_answer.py", '        except F.Deferred:                       # 恢复闸关着：先不动，开闸后再对\n            return []\n',
     '        except ZeroDivisionError:\n            return []\n',
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
    ("secretary_gate.py", '        E.close_gate(f"秘书代批 {decision_id} 结果不明")', '        pass',
     "秘书结果不明关闸", [SG + ".RecoveryGateTests"]),
    ("secretary_gate.py", '    try:\n        epoch = R.ensure_open()\n    except E.Refused as e:\n        return {"ok": False, "paused": str(e)}\n    card = F.hub("GET"',
     '    epoch = None\n    card = F.hub("GET"', "秘书领卡过闸", [SG + ".RecoveryGateTests"]),
    # ---------- 第 2 轮预审（Claude 子 agent）补的 ----------
    ("autoloop_recovery.py", "    waiting = [r[0] for r in rows if _waits_on_gateway(r, gcon)]", "    waiting = []",
     "P1-A 秘书 ② 只差网关补发不挡闸（否则死锁）", [SG + ".RedispatchTests.test_redispatch_unknown_does_not_deadlock_the_gate"]),
    ("autoloop_recovery.py", 'return e["sent"] == 2 and G.now_tz() - G.parse_iso(e["claimed_at"]) >= G.STALE',
     'return True',
     "P1-A 事件作废或已送达的照常挡闸", [SG + ".RedispatchTests.test_redispatch_without_a_gateway_event_still_blocks"]),
    ("secretary_gate.py", '        if d["status"] != "unreconciled":\n            raise Refused(f"只有', '        if False:\n            raise Refused(f"只有',
     "P1-A 秘书人工结算只认核不清", [SG + ".RecoveryGateTests.test_unreconciled_is_settled_by_owner_and_then_stops_blocking"]),
    ("autoloop_feishu.py", "        try:\n            _still_open(epoch)\n        except Deferred as e:                    # 领卡之后",
     "        try:\n            pass\n        except Deferred as e:                    # 领卡之后",
     "P2-C 桥接锁里再核闸（改卡）", [FS + ".RecoveryGate.test_gate_closing_before_the_bridge_lock_writes_nothing"]),
    ("autoloop_feishu.py", "    with bridge_lock():\n        _still_open(epoch)\n        _report_delivered(card_id)",
     "    with bridge_lock():\n        _report_delivered(card_id)",
     "P2-C 桥接锁里再核闸（补报送达）", [FS + ".RecoveryGate.test_gate_closing_before_the_bridge_lock_writes_nothing"]),
    ("autoloop_recovery.py", "        if F.bridge_busy():", "        if False:", "P2-C 排空看桥接锁",
     [FS + ".RecoveryGate.test_gate_closing_before_the_bridge_lock_writes_nothing"]),
    ("autoloop_recovery.py", "                    if now - G.parse_iso(r[1]) < G.STALE]", "                    ]",
     "排空只算刚领走的派单事件", [RC + ".Maintenance.test_drain_counts_only_freshly_claimed_dispatch_events"]),
    ("autoloop_feishu.py", "    except Exception as e:                         # 结果不明", "    except ZeroDivisionError as e:",
     "P2-B 发卡结果不明是暂缓、不报 failed", [FS + ".Deliver.test_once_reports_delivered_or_failed"]),
    ("autoloop_feishu.py", '    mid = got.get("message_id")\n    if not mid:', '    mid = got["message_id"]\n    if not mid:',
     "P2-B2 账本没消息号时暂缓", [FS + ".RecoveryGate.test_settled_send_without_message_id_is_deferred"]),
    ("autoloop_exits.py", '        if result == "done" and row["kind"] in SETTLE_NEEDS_REF and not ref:', "        if False:",
     "P2-B2 结算发飞书要消息号", [EX + ".Reconcile.test_settle_done_writes_the_ref_and_feishu_needs_one"]),
    # ---------- 演练 ----------
    ("autoloop_exits.py", '    return st == row["proc"]', '    return True', "强杀扫描（演练：真强杀后对账、不重推）",
     [DR + ".PushBranch.test_kill_after_push_is_reconciled_not_redone"]),
    ("autoloop_exits.py", '    if st == "?" or row["proc"] in (None, "?"):\n        return True',
     '    if True:\n        return False', "P1-02 活着超 30 分钟不放行（演练：真并发）",
     [DR + ".PushBranch.test_live_executor_past_hard_stale_is_never_retried"]),
    # ---------- L3 第 3 轮返修（11-21）----------
    ("autoloop_recovery.py", "        if F.bridge_busy():", "        if F.db_file().exists() and F.bridge_busy():",
     "P1-06 排空看桥接锁不依赖 cards.db", [RC + ".Maintenance.test_drain_sees_bridge_lock_before_cards_db_exists"]),
    ("secretary_gate.py", "            F._still_open(epoch)\n        except F.Deferred as e:                              # 领卡之后",
     "            pass\n        except F.Deferred as e:                              # 领卡之后",
     "P1-06 秘书领卡锁里再核闸", [SG + ".LeaseTests"]),
    ("secretary_gate.py", "    try:                                         # 先关闸、再记结果不明（L3 第 3 轮 P1-08）：两步之间别的写入者过不了 admit。\n"
     "        E.close_gate(f\"秘书代批 {decision_id} 结果不明\")",
     "    SL.finish(st, decision_id, result, outcome, now=now)\n    try:\n        E.close_gate(f\"秘书代批 {decision_id} 结果不明\")",
     "P1-08 秘书先关闸再记结果不明", [SG + ".RecoveryGateTests.test_unknown_closes_the_gate_before_the_ledger_records_it"]),
    ("autoloop_answer.py", "    if state == \"unknown\":                       # 结果不明：先关恢复闸、再记",
     "    if False:                       # 结果不明：先关恢复闸、再记",
     "P1-08 同类：本人回答先关闸再记 unknown", [FS + ".Answer.test_unknown_answer_closes_the_gate_before_recording_it"]),
    ("autoloop_grants.py", "    if event_key and lg.get(\"event\") != event_key:", "    if False:",
     "P1-09 决定记的事件号要和绑定的一致", [SG + ".RejectTests"]),
    ("autoloop_grants.py", "e[\"generation\"] != lg.get(\"generation\") \\", "False \\",
     "P1-09 绑定事件的代次要对", [SG + ".RejectTests", SG + ".RedispatchTests"]),
    ("autoloop_grants.py", "    logs = resume_refs(con, task_id).get(ref, [])",
     "    logs = [{\"event\": r[0], \"generation\": r[1]} for r in con.execute(\"SELECT event_key, generation FROM hub_events \"\n"
     "            \"WHERE task_id = ? AND kind = ? ORDER BY rowid DESC LIMIT 1\", (task_id, kind))]",
     "P1-09 不按任务 + 种类猜事件", [SG + ".RejectTests"]),
    ("autoloop_grants.py", '        if generation is not None and row["generation"] != generation:', "        if False:",
     "P1-09 续接核发起时的代次", [GR + ".Resume"]),
    ("autoloop_grants.py", "        if ref is not None and ref in resume_refs(con, task_id):", "        if False:",
     "P1-09 同一决定只续接一次", [GR + ".Resume"]),
    ("secretary_gate.py", "        out = G.resume_dispatch(d[\"task_id\"], kind, reason, now=now, ref=d[\"id\"], generation=generation,\n",
     "        out = G.resume_dispatch(d[\"task_id\"], kind, reason, now=now,\n",
     "P1-09 秘书续接带上决定号", [SG + ".RejectTests", SG + ".RedispatchTests"]),
    ("autoloop_exits.py", "    ours = [p for p in prs if P.pr_is_ours(p, t[\"github\"], t[\"branch\"], t[\"head\"])]",
     "    ours = [p for p in prs if p.get(\"headRefOid\") == t[\"head\"] and p.get(\"baseRefName\") == \"main\"]",
     "P2-04 开 PR 对账核 PR 来源", [EX + ".Readers.test_read_pr"]),
    # ---------- L3 第 4 轮决策升级后（11-21，常浩「按推荐做」）----------
    ("autoloop_feishu.py", "    with bridge_lock():\n        if maintenance():\n            return False\n        return _alert(key, text, every)",
     "    return _alert(key, text, every)", "P1-06 维护中提醒不发（alert）", [FS + ".MaintenanceQuiet"]),
    ("autoloop_answer.py", "        if F.maintenance():\n            return False\n", "", "P1-06 维护中回话不发（tell）",
     [FS + ".MaintenanceQuiet.test_alert_and_tell_are_quiet_during_maintenance_then_resume"]),
    ("autoloop_feishu.py", "            if held:                                   # 维护停写", "            if False:                                   # 维护停写",
     "P1-06 常驻循环维护中什么都不跑", [FS + ".MaintenanceQuiet.test_run_loop_does_nothing_during_maintenance"]),
    ("autoloop_feishu.py", "    with bridge_lock():\n        _still_open(epoch)\n        return _deliver(card)",
     "    with bridge_lock():\n        return _deliver(card)", "P1-06 deliver 锁里核闸",
     [FS + ".MaintenanceQuiet.test_deliver_and_confirm_check_the_gate"]),
    ("autoloop_feishu.py", "    report_delivered(card_id)\n    _flush(card_id)", "    with bridge_lock():\n        _report_delivered(card_id)\n    _flush(card_id)",
     "P1-06 confirm 过闸", [FS + ".MaintenanceQuiet.test_deliver_and_confirm_check_the_gate"]),
    ("autoloop_recovery.py", "    if E.read_markers():                                 # 关闸标记", "    if False:                                 # 关闸标记",
     "P1-08 看闸认关闸标记", [RC + ".Markers"]),
    ("autoloop_recovery.py", "    if E.read_markers():\n        raise E.Refused(\"恢复闸关着：出现结果不明（关闸标记）", "    if False:\n        raise E.Refused(\"恢复闸关着：出现结果不明（关闸标记）",
     "P1-08 领资格认关闸标记", [RC + ".Markers.test_marker_alone_closes_the_gate_until_reconciled"]),
    ("autoloop_recovery.py", "        if why is None and set(E.read_markers()) - set(marks):", "        if False:",
     "P1-08 对账期间新建的标记不开闸", [RC + ".Markers.test_marker_written_during_reconcile_keeps_the_gate_closed"]),
    ("autoloop_exits.py", "    try:\n        write_marker(reason)\n        marked = True\n    except OSError:\n        pass\n",
     "", "P1-08 关闸先建标记", [RC + ".Markers.test_close_gate_survives_a_locked_ledger",
                              SG + ".RecoveryGateTests.test_locked_ledger_while_closing_still_keeps_the_gate_closed"]),
    ("autoloop_answer.py", "        except Exception as e:\n            failed = e\n", "        except Exception:\n            raise\n",
     "P1-08 本人回答关闸失败照样记 unknown", [FS + ".Answer.test_gate_close_failure_still_records_unknown"]),
    ("autoloop_recovery.py", 'if verdict != "ok" or e["invalid"]:', 'if verdict != "ok":', "P1-09 凭证失效的事件不算只差补发",
     [SG + ".RedispatchTests.test_event_of_a_frozen_or_expired_grant_is_not_waiting_for_resend"]),
    ("secretary_gate.py", '    if e["sent"] in (0, 2) and e["invalid"]:', '    if False:',
     "P1-09 秘书对账不干等补发不了的事件", [SG + ".RedispatchTests"]),
    ("secretary_gate.py", '    if verdict == "none":\n        return ("unreconciled"', '    if verdict == "none":\n        return ("done"',
     "P1-09 ③ 找不到绑定事件记核不清", [SG + ".RejectTests.test_secretary_reconcile_never_takes_a_delivered_event_of_another_generation"]),
    # ---------- 第 5 轮送审前预审 P2 ----------
    ("autoloop_feishu.py", "    with bridge_lock():\n        if maintenance():\n            return False\n        return _alert(key, text, every)",
     "    if maintenance():\n        return False\n    with bridge_lock():\n        return _alert(key, text, every)",
     "P1-06 alert 锁里查维护", [FS + ".MaintenanceQuiet.test_maintenance_is_checked_inside_the_bridge_lock"]),
    ("autoloop_answer.py", "    with F.bridge_lock():\n        if F.maintenance():\n            return False\n",
     "    if F.maintenance():\n        return False\n    with F.bridge_lock():\n",
     "P1-06 tell 锁里查维护", [FS + ".MaintenanceQuiet.test_maintenance_is_checked_inside_the_bridge_lock"]),
    ("autoloop_feishu.py", "                if not A.tell(A.identities()[1], text):\n                    held_back.append((cid, text))",
     "                A.tell(A.identities()[1], text)",
     "维护刚开始时压下的对账回话维护后补发", [FS + ".MaintenanceQuiet"]),
    ("autoloop_feishu.py", 'if held != "?"', 'if True', "读不了账本不写成维护中",
     [FS + ".MaintenanceQuiet.test_unreadable_gate_means_quiet"]),
    # L3 第 5 轮 P1-08（常浩 11-21 经飞书选甲）：对账开闸前看已开始、没记结果的受保护动作
    ("autoloop_recovery.py", "        if busy:\n            why = f\"还有", "        if False:\n            why = f\"还有",
     "对账开闸前不看已开始的受保护动作", [RC + ".StartedBeforeOpen"]),
    ("autoloop_recovery.py", '+ [f"本人回答 卡片 {c}" for c in _owner_inflight()]\n            + [f"秘书代批',
     '+ []\n            + [f"秘书代批', "开闸前不看本人回答 inflight",
     [RC + ".StartedBeforeOpen.test_owner_answer_in_flight_keeps_the_gate_closed"]),
    ("autoloop_recovery.py", "for d in _secretary_sending(fresh_only=False) if d not in waits]",
     "for d in _secretary_sending(fresh_only=False)]", "开闸前把只差网关补发的秘书 ② / ③ 也算在途（11-20 的死锁）",
     [SG + ".RedispatchTests.test_redispatch_stuck_in_executing_does_not_deadlock_the_gate"]),
    ("autoloop_recovery.py", "return ([f\"出口 {r[0]}\" for r in con.execute(\"SELECT key FROM exits WHERE state = 'executing'\")]",
     "return ([]", "开闸前不看出口账本 executing", [RC + ".StartedBeforeOpen"]),
    ("autoloop_recovery.py", "_secretary_sending(fresh_only=False)", "_secretary_sending()",
     "开闸前只看 2 分钟内动过的秘书代批", [RC + ".StartedBeforeOpen"]),
    ("autoloop_recovery.py", 'return [f"?（卡片库读不了：{type(e).__name__}）"]', "return []",
     "卡片库读不了当作没有在发的本人回答", [RC + ".StartedBeforeOpen"]),
    # 续审第 1 轮 P1-08（常浩 11-21 侧栏授权续审）
    ("autoloop_answer.py", '            con.execute("COMMIT")                # 放出口账本写锁之前',
     '            pass                                 # 放出口账本写锁之前', "本人回答 inflight 放锁之后才提交",
     [RC + ".OwnerCommitVisibility"]),
    ("autoloop_recovery.py", "            waits = []\n            sec_bad, _ = _secretary(waits, gcon)\n            why = why or sec_bad\n",
     "            pass\n", "开闸前不持凭证库锁重判只差补发（用第 4 步的旧名单）",
     [SG + ".RedispatchTests.test_freeze_between_step4_and_open_keeps_the_gate_closed",
      SG + ".RedispatchTests.test_grant_expiring_between_step4_and_open_keeps_the_gate_closed"]),
    ("autoloop_recovery.py", 'return e["sent"] == 2 and G.now_tz() - G.parse_iso(e["claimed_at"]) >= G.STALE',
     'return e["sent"] == 2', "刚领走在发的事件也算只差补发",
     [SG + ".RedispatchTests.test_fresh_claim_is_a_send_in_progress_not_a_resend"]),
    ("autoloop_recovery.py", "            why = why or sec_bad\n        frozen = E.frozen_scopes(con)",
     "            why = why or sec_bad\n            gcon.execute(\"COMMIT\")\n            gcon.execute(\"BEGIN\")\n        frozen = E.frozen_scopes(con)",
     "重判完先放凭证库锁再开闸（冻结能插进来）",
     [SG + ".RedispatchTests.test_freeze_waits_until_the_gate_decision_is_committed"]),
    # 续审第 2 轮 P1-08-02
    ("autoloop_recovery.py", '        try:\n            con = locks.enter_context(E.db(timeout=5 if gcon is not None else 30))\n        except sqlite3.OperationalError as e:\n            locks.close()\n            return {"open": False, "why": f"出口账本这会儿被别的写入者占着（{e}），这次不开闸，下次再对", "frozen": [],\n                    "steps": steps, "epoch": epoch}\n        if gcon is not None:                      # 两把锁都拿到之后才重判：等出口账本锁那几秒里凭证可能自然过期（续审第 2 轮）\n            waits = []\n            sec_bad, _ = _secretary(waits, gcon)\n            why = why or sec_bad\n', '        if gcon is not None:                      # 两把锁都拿到之后才重判：等出口账本锁那几秒里凭证可能自然过期（续审第 2 轮）\n            waits = []\n            sec_bad, _ = _secretary(waits, gcon)\n            why = why or sec_bad\n        try:\n            con = locks.enter_context(E.db(timeout=5 if gcon is not None else 30))\n        except sqlite3.OperationalError as e:\n            locks.close()\n            return {"open": False, "why": f"出口账本这会儿被别的写入者占着（{e}），这次不开闸，下次再对", "frozen": [],\n                    "steps": steps, "epoch": epoch}\n', "重判放在拿出口账本锁之前（等锁那几秒里过期了也不知道）",
     [SG + ".RedispatchTests.test_grant_expiring_while_waiting_for_the_exits_lock_keeps_the_gate_closed"]),
    ("autoloop_recovery.py", "EXPIRY_MARGIN = dt.timedelta(seconds=60)", "EXPIRY_MARGIN = dt.timedelta(seconds=0)",
     "快到期的续接事件也算只差补发", [SG + ".RedispatchTests.test_grant_about_to_expire_is_not_waiting_for_resend"]),
    # 续审第 3 轮 P1-08-01
    ("autoloop_grants.py", "    if not task_locked:\n        try:\n", "    if False:\n        try:\n",
     "续接不拿任务锁（命令行能另记一条事件）",
     [SG + ".RedispatchTests.test_cli_resume_cannot_add_a_second_event_while_the_secretary_holds_the_task"]),
    # 续审第 3 轮预审 P1-A / P2-A
    ("autoloop_grants.py", "        if pending:                              #", "        if False:                              #",
     "这张单还有没确认送到的续接事件也照样续接（锁放开后冒出第二个事件号）",
     [GR + ".Resume", SG + ".RedispatchTests.test_cli_resume_cannot_add_a_second_event_while_the_secretary_holds_the_task"]),
    ("autoloop_grants.py", '    d = Path(path or db_path()).parent / "task-locks"', '    d = Path(path or db_path()).parent / "locks-elsewhere"',
     "任务锁不在凭证库旁边", [GR + ".Resume.test_task_lock_lives_next_to_the_grants_db"]),
]


TIMEOUT = 600   # 秒：单组测试正常几秒到五分钟（受信发布那组约五分钟）


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
        try:
            r = subprocess.run([sys.executable, "-B", "-m", "unittest", *args], cwd=W, capture_output=True, text=True,
                               timeout=TIMEOUT)
        except subprocess.TimeoutExpired:                  # 改坏后测试挂住（例如同一进程里重复拿恢复锁）：也算没通过
            worst, tails = worst or 124, tails + [f"{mod} 超过 {TIMEOUT} 秒没跑完（挂住）"]
            continue
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
