"""日报拍板账与按钮（#235）自测：只认常浩本人、常浩私聊、最新一张卡；记账幂等；卡片原地改；定过的第二天不再问。
不连飞书：lark 换成记录器；身份配置用环境变量。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import pdaily_act as A  # noqa: E402
import pdaily_publish as P  # noqa: E402
import test_pdaily_publish as T  # noqa: E402

DAY = T.DAY
TOKEN = "0123456789abcdef"


class ActTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.facts = T.facts()
        self.facts["decisions"].append({"kind": "merge", "project_id": "digital-twin", "item": "#102 去留", "source": "合并官",
                                        "evidence": [], "since": "2026-11-05T10:00:00+08:00"})
        d = T.card_fields()["decisions"][0]
        self.fields = T.card_fields(decisions=[d, dict(d, type="普通", kind="选", candidates=[1], project="数字分身",
                                                       item="#102 去留", recommendation="退回重做", deadline=None),
                                               dict(d, type="普通", kind="做", candidates=[], project="数码世界",
                                                    item="5178 终验", recommendation="今晚验", deadline=None)])
        card = P.build_card(self.facts, self.fields, "t", token=TOKEN)
        (self.tmp / f"{DAY}.facts.json").write_text(json.dumps(self.facts, ensure_ascii=False))
        (self.tmp / f"{DAY}.card-fields.json").write_text(json.dumps(self.fields, ensure_ascii=False))
        (self.tmp / f"{DAY}.card.sent.json").write_text(json.dumps(card, ensure_ascii=False))
        snap, sha = A.snapshot(self.facts, self.fields["decisions"])
        (self.tmp / f"{DAY}.decisions.sent.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1))
        self.deliv = {"stat_day": DAY, "sent": True, "read_back": True, "message_id": "om_abc123", "card_token": TOKEN,
                      "decisions_sha": sha}
        (self.tmp / f"{DAY}.delivery.json").write_text(json.dumps(self.deliv))
        self.calls = []
        self.patches = [mock.patch.object(A, "DAILY", self.tmp),
                        mock.patch.object(P, "lark", side_effect=lambda args, what, who, cwd=None: self.calls.append(args) or {}),
                        mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": "ou_owner", "DT_OWNER_CHAT_ID": "oc_home"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_cmd(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = A.main(list(argv))
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def click(self, n, token=TOKEN, chat="oc_home", operator="ou_owner"):
        return self.run_cmd("button", token, str(n), "--chat", chat, "--operator", operator)

    def test_only_owner_in_private_chat(self):
        for kw in ({"operator": "ou_other"}, {"chat": "oc_group"}, {"token": "XYZ"}):
            rc, out = self.click(1, **kw)
            self.assertEqual((rc, out["msg"]), (1, "不认"), kw)
        self.assertEqual(A.rows(), [])
        with mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": "", "DT_OWNER_CHAT_ID": ""}):
            self.assertEqual(self.click(1)[1]["msg"], "不认")        # 本机没配身份：一律不认，不拿空值去比

    def test_old_card_token_refused_with_reason(self):
        rc, out = self.click(1, token="fedcba9876543210")
        self.assertEqual(rc, 1)
        self.assertIn("不是最新一张日报卡片", out["msg"])
        self.assertEqual(A.rows(), [])

    def test_click_records_once_and_asks_hermes(self):
        rc, out = self.click(2)
        self.assertEqual(rc, 0)
        self.assertEqual(len(out["entries"]), 1)
        self.assertIn("「② 按推荐」", out["inject"])
        self.assertIn(out["entries"][0], out["inject"])
        e = A.entries()[out["entries"][0]]
        self.assertEqual((e["n"], e["choice"], e["via"], e["item"]), (2, "按推荐", "按钮", "#102 去留"))
        self.assertEqual(e["keys"], [A.cand_key(self.facts["decisions"][1])])
        self.assertEqual(self.click(2)[1]["entries"], [])                # 同一条再点：不重复记
        self.assertEqual(len(A.rows()), 1)

    def test_all_means_the_rest_that_can_be_done(self):
        """全部按推荐 = 其余还没定、有推荐、不用常浩亲手做的；③ 是要常浩亲手做的，不算、也没有单独的按钮。"""
        self.click(2)
        rc, out = self.click(0)
        self.assertEqual([A.entries()[i]["n"] for i in out["entries"]], [1])
        self.assertIn("「① 按推荐」", out["inject"])
        rc, out = self.click(3)
        self.assertEqual(rc, 1)
        self.assertIn("要你亲手做", out["msg"])
        rc, out = self.click(0)                                          # 都定了：说一句现在的状态
        self.assertEqual(out["entries"], [])
        self.assertIn("没有还没定", out["tell"])
        rc, out = self.click(2)
        self.assertIn("② 已经定过（按推荐，还没有办理结果）", out["tell"])

    def test_buttons_follow_sent_snapshot_not_rebuilt_draft(self):
        """审查 P1-1：发卡后重新写稿（card-fields 变了），按钮仍对应发出去的那一版；快照被改就不认。"""
        swapped = dict(self.fields, decisions=[self.fields["decisions"][2], self.fields["decisions"][0]])
        (self.tmp / f"{DAY}.card-fields.json").write_text(json.dumps(swapped, ensure_ascii=False))
        _, out = self.click(2)
        self.assertEqual(A.entries()[out["entries"][0]]["item"], "#102 去留")
        snap = json.loads((self.tmp / f"{DAY}.decisions.sent.json").read_text())
        snap["decisions"][0]["recommendation"] = "改过的"
        (self.tmp / f"{DAY}.decisions.sent.json").write_text(json.dumps(snap, ensure_ascii=False))
        rc, out = self.click(1)
        self.assertEqual(rc, 1)
        self.assertIn("对不上", out["msg"])

    def test_latest_card_not_read_back_means_no_card(self):
        """审查 P1-2：最新一张没回读通过，不往前找旧卡。"""
        newer = dict(self.deliv, stat_day="2026-11-09", read_back=False, card_token="fedcba9876543210")
        (self.tmp / "2026-11-09.delivery.json").write_text(json.dumps(newer))
        rc, out = self.click(1)
        self.assertEqual(rc, 1)
        self.assertIn("不是最新一张", out["msg"])
        self.assertEqual(self.run_cmd("todos")[0], 1)
        (self.tmp / "2026-11-10.delivery.json").write_text(json.dumps({"stat_day": "2026-11-10", "sent": False}))
        self.assertEqual(self.run_cmd("todos")[0], 1)                    # 更新的一天没发成：跳过它，看 11-09，仍然不往前

    def test_after_refreshes_card_and_tells_when_not_injected(self):
        _, out = self.click(2)
        rc, _ = self.run_cmd("after", "--day", DAY, "--entries", out["entries"][0], "--injected", "0")
        self.assertEqual(rc, 0)
        patch, say = self.calls
        self.assertEqual(patch[:5], ["im", "messages", "patch", "--message-id", "om_abc123"])
        card = json.loads(json.loads(patch[6])["content"])
        tab = [e for e in card["body"]["elements"] if e.get("tag") == "table"][-1]
        self.assertEqual([r["a"] for r in tab["rows"]][1], "✅ 按推荐")
        btns = [c for e in card["body"]["elements"] if e.get("tag") == "column_set" for c in e["columns"]]
        self.assertEqual([A.btn_value(c)["n"] for c in btns], [0, 1])     # ② 的按钮去掉了；③ 本来就没有
        self.assertEqual(btns[0]["elements"][0]["text"]["content"], "其余全部按推荐")
        self.assertIn("引用这张日报卡片回一句「办 ②」", say[-1])
        self.assertEqual(json.loads((self.tmp / f"{DAY}.card.sent.json").read_text()), card)

    def test_all_decided_drops_button_row(self):
        self.click(0)
        self.run_cmd("after", "--day", DAY, "--entries", "x", "--injected", "1")
        card = json.loads(json.loads(self.calls[0][6])["content"])
        self.assertFalse([e for e in card["body"]["elements"] if e.get("tag") == "column_set"])
        self.assertEqual(len(self.calls), 1)                             # 注入成功：不另发私聊

    def test_record_from_reply_and_done(self):
        rc, out = self.run_cmd("record", "--n", "1,3", "--choice", "先放着")
        self.assertEqual(rc, 0)
        self.assertEqual([x["n"] for x in out["entries"]], [1, 3])
        self.assertEqual(self.run_cmd("record", "--n", "9", "--choice", "不做")[0], 1)
        self.assertEqual(self.run_cmd("record", "--n", "2", "--choice", "随便")[0], 1)
        rc, out = self.run_cmd("record", "--n", "2", "--choice", "按推荐", "--note", "只退回不关")
        eid = out["entries"][0]["id"]
        rc, out = self.run_cmd("record", "--n", "2", "--choice", "不做")      # 已定的不改，照实说
        self.assertEqual((out["entries"], out["already"][0]["choice"]), ([], "按推荐"))
        self.assertIn("已经定过", out["msg"])
        self.assertEqual(self.run_cmd("done", "--entry", eid, "--result", "派了 Claude 变基", "--order", "D20261119-3")[0], 0)
        self.assertEqual(self.run_cmd("done", "--entry", "p-nope", "--result", "x")[0], 1)
        rc, out = self.run_cmd("todos")
        d2 = out["decisions"][1]
        self.assertEqual(d2["decided"]["note"], "只退回不关")
        self.assertEqual(d2["decided"]["results"][0]["order"], "D20261119-3")
        self.assertEqual(d2["sources"][0]["source"], "合并官")
        self.assertEqual(out["decisions"][0]["decided"]["choice"], "先放着")

    def test_remembered_for_next_day(self):
        _, out = self.run_cmd("record", "--n", "2", "--choice", "按推荐")
        self.run_cmd("record", "--n", "1", "--choice", "先放着")
        k1, k2 = A.cand_key(self.facts["decisions"][0]), A.cand_key(self.facts["decisions"][1])
        keys, recent, held = A.remembered()
        self.assertTrue({k1, k2} <= keys)
        self.assertEqual((len(recent), held), (1, 1))
        day = A.dt.timedelta(days=1)
        keys, _, _ = A.remembered(A.now() + 3 * day)
        self.assertNotIn(k2, keys)                                       # 按推荐但 3 天没办理结果：重新拿出来问（审查 P2-5）
        self.assertIn(k1, keys)
        self.run_cmd("done", "--entry", out["entries"][0]["id"], "--result", "派了 Claude", "--order", "D1")
        keys, _, held = A.remembered(A.now() + 8 * day)
        self.assertIn(k2, keys)                                          # 办成了：30 天内不再问
        self.assertNotIn(k1, keys)                                       # 先放着过了 7 天：重新拿出来问
        self.assertEqual(held, 0)
        self.run_cmd("done", "--entry", out["entries"][0]["id"], "--result", "没办：要合并，超出授权")
        self.assertNotIn(k2, A.remembered()[0])                          # 最后结果是没办：不算定过

    def test_next_day_material_and_check(self):
        self.run_cmd("record", "--n", "2", "--choice", "按推荐")
        m = P.material(self.facts)
        self.assertTrue(m["decisions"][1].get("already_decided"))
        self.assertFalse(m["decisions"][0].get("already_decided"))
        self.assertEqual(m["ledger"]["recently_decided"][0]["item"], "#102 去留")
        card = T.card_fields(decisions=[self.fields["decisions"][1], self.fields["decisions"][0]],
                             risks=[dict(T.card_fields()["risks"][0], to_decisions=[1, 2])])
        w = P.normalize(self.facts, card)                                # 定过的不算核对失败：规整时去掉，风险去向跟着挪（审查 P1-3）
        self.assertTrue(any("已经定过" in x for x in w), w)
        self.assertEqual([d["item"] for d in card["decisions"]], ["夜跑收尾"])
        self.assertEqual(card["risks"][0]["to_decisions"], [1])
        self.assertEqual(P.check(self.facts, card, T.report_text()), [])
        fb, _ = P.fallback(self.facts, "x")                              # 事实版也不再列定过的
        self.assertEqual([d["item"] for d in fb["decisions"]], ["夜跑停机后收尾，授权 72 到期 2026-11-09T06:00"])
        c = P.build_card(self.facts, fb, "t", token=TOKEN)
        self.assertFalse([e for e in c["body"]["elements"] if e.get("tag") == "column_set"])   # 降级卡不放按钮

    def fake_dispatch(self, busy_times=0):
        """假 dispatch.py：new 回单号，run 前 busy_times 次说忙；每次调用记一行。"""
        log = self.tmp / "dispatch.log"
        fake = self.tmp / "fake_dispatch.py"
        fake.write_text(
            "import json, sys, pathlib\n"
            f"log = pathlib.Path({str(log)!r})\n"
            "n = len(log.read_text().splitlines()) if log.exists() else 0\n"
            "log.open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1] == 'new':\n"
            "    print(json.dumps({'ok': True, 'msg': '已开单', 'order': {'no': 'D2026-%d' % n}}))\n"
            f"elif sum(1 for l in log.read_text().splitlines() if l.startswith('run')) <= {busy_times}:\n"
            "    print(json.dumps({'ok': False, 'msg': '排队中', 'busy': True}))\n"
            "else:\n"
            "    print(json.dumps({'ok': True, 'msg': '跑完'}))\n", encoding="utf-8")
        return log, fake

    def test_checks_dispatch_read_only_orders(self):
        r = dict(T.card_fields()["risks"][0], check={"project": "牛马之家", "task": "查清 15 处改动的性质"})
        r2 = dict(r, cause="别的", check={"project": "外星项目", "task": "x"})       # 名册外的跳过
        fields = dict(self.fields, risks=[r, r2])
        (self.tmp / f"{DAY}.card-fields.json").write_text(json.dumps(fields, ensure_ascii=False))
        log, fake = self.fake_dispatch(busy_times=1)
        with mock.patch.object(A, "DISPATCH", fake), mock.patch.object(A, "CHECK_WAIT", 0):
            rc, out = self.run_cmd("checks", "--day", DAY)
        self.assertEqual(rc, 0)
        calls = log.read_text().splitlines()
        self.assertTrue(calls[0].startswith("new --project work-desk --mode read --task-file "))
        self.assertEqual(calls[1:], ["run D2026-0", "run D2026-0"])          # 第一次说忙，隔一会儿再跑
        task = Path(calls[0].split("--task-file ")[1]).read_text()
        self.assertIn("查清 15 处改动的性质", task)
        self.assertIn("不得修改项目文件", task)
        recs = json.loads((self.tmp / f"{DAY}.checks.json").read_text())
        self.assertEqual([(x["project"], x["order"], x["run"]) for x in recs], [("work-desk", "D2026-0", "跑完")])
        self.assertEqual(self.run_cmd("checks", "--day", DAY)[1]["msg"], "这一天已经派过了")
        root = self.tmp / "dispatch"
        (root / "D2026-0" / "out").mkdir(parents=True)
        (root / "D2026-0" / "out" / "receipt.json").write_text(json.dumps({"exit_code": "0", "summary": "15 处都是草稿，建议丢弃"}))
        with mock.patch.dict(os.environ, {"DT_DISPATCH_ROOT": str(root)}):
            got = A.checks_for_material(DAY)
        self.assertEqual((got[0]["finished"], got[0]["summary"]), (True, "15 处都是草稿，建议丢弃"))
        f = dict(self.facts, stat_day="2026-11-09")
        with mock.patch.dict(os.environ, {"DT_DISPATCH_ROOT": str(root)}):
            self.assertEqual(P.material(f)["yesterday_checks"][0]["order"], "D2026-0")

    def test_checks_none_written_once(self):
        log, fake = self.fake_dispatch()
        with mock.patch.object(A, "DISPATCH", fake):
            self.assertEqual(self.run_cmd("checks", "--day", DAY)[1]["msg"], "派了 0 张只读单")
        self.assertFalse(log.exists())
        self.assertEqual(json.loads((self.tmp / f"{DAY}.checks.json").read_text()), [])

    def test_broken_ledger_lines_ignored(self):
        (self.tmp / "decisions.jsonl").write_text("坏行\n[1]\n")
        self.assertEqual(A.entries(), {})
        self.assertEqual(self.click(1)[0], 0)


if __name__ == "__main__":
    unittest.main()
