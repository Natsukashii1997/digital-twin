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
        self.deliv = {"stat_day": DAY, "sent": True, "read_back": True, "message_id": "om_abc123", "card_token": TOKEN}
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

    def test_all_means_the_rest(self):
        self.click(2)
        rc, out = self.click(0)
        self.assertEqual([A.entries()[i]["n"] for i in out["entries"]], [1, 3])
        self.assertIn("①、③ 按推荐", out["inject"])

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
        self.assertEqual([A.btn_value(c)["n"] for c in btns], [0, 1, 3])  # ② 的按钮去掉了
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
        self.assertEqual(self.run_cmd("done", "--entry", eid, "--result", "派了 Claude 变基", "--order", "D20261119-3")[0], 0)
        self.assertEqual(self.run_cmd("done", "--entry", "p-nope", "--result", "x")[0], 1)
        rc, out = self.run_cmd("todos")
        d2 = out["decisions"][1]
        self.assertEqual(d2["decided"]["note"], "只退回不关")
        self.assertEqual(d2["decided"]["results"][0]["order"], "D20261119-3")
        self.assertEqual(d2["sources"][0]["source"], "合并官")
        self.assertEqual(out["decisions"][0]["decided"]["choice"], "先放着")

    def test_remembered_for_next_day(self):
        self.run_cmd("record", "--n", "2", "--choice", "按推荐")
        self.run_cmd("record", "--n", "1", "--choice", "先放着")
        keys, recent, held = A.remembered()
        self.assertIn(A.cand_key(self.facts["decisions"][1]), keys)
        self.assertIn(A.cand_key(self.facts["decisions"][0]), keys)
        self.assertEqual((len(recent), held), (1, 1))
        later = A.now() + A.dt.timedelta(days=8)
        keys, _, held = A.remembered(later)
        self.assertNotIn(A.cand_key(self.facts["decisions"][0]), keys)  # 先放着过了 7 天：重新拿出来问
        self.assertEqual(held, 0)
        self.assertIn(A.cand_key(self.facts["decisions"][1]), keys)

    def test_next_day_material_and_check(self):
        self.run_cmd("record", "--n", "2", "--choice", "按推荐")
        m = P.material(self.facts)
        self.assertTrue(m["decisions"][1].get("already_decided"))
        self.assertFalse(m["decisions"][0].get("already_decided"))
        self.assertEqual(m["ledger"]["recently_decided"][0]["item"], "#102 去留")
        e = P.check(self.facts, T.card_fields(decisions=[self.fields["decisions"][1]]), T.report_text())
        self.assertTrue(any("已经定过" in x for x in e), e)
        self.assertEqual(P.check(self.facts, T.card_fields(), T.report_text()), [])

    def test_broken_ledger_lines_ignored(self):
        (self.tmp / "decisions.jsonl").write_text("坏行\n[1]\n")
        self.assertEqual(A.entries(), {})
        self.assertEqual(self.click(1)[0], 0)


if __name__ == "__main__":
    unittest.main()
