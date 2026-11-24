"""共享参考库技能 dt-shared-reference（#359，docs/技能架构.md 第 4 节、第 9 节 5b-2）的静态核对。

参考库是给别的技能按文件读的说明文字，模型怎么用它这里测不到（要装上后用 hermes chat 跑案例）。
这里只核文字和仓库对不对得上：
  1. 文件齐，技能头写对，SKILL.md 模块表列的文件都在、references/ 下的文件都列进了表；
  2. 模块里点名的技能、脚本、脚本子命令、文档（含根目录的）、「某技能第 N 节」「清单第 N 节」都真的存在，引用的卡片字样真的在卡片上；
     发出通道表里新底座、旧车间两行各自的授权条件写全（卫士卡点击；先回「按老办法派」再对单回「可以」），三类例子的处理句都在（#383 第 1 轮 P2-03）；
  3. 四个档位的说法和《常设授权清单》第 2 节一字不差；
  4. 已经作废的说法不再出现（打字「派单 <确认码>」批准，0E-3b 常浩 11-23 定删掉），参考库里不夹带私聊号这类个人信息。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "hermes" / "skills"
SCRIPTS = ROOT / "hermes" / "scripts"
LIB = SKILLS / "dt-shared-reference"
MODULE = LIB / "references" / "decision-request.md"


def read(p):
    return p.read_text(encoding="utf-8")


class Files(unittest.TestCase):
    def test_layout(self):
        # 第 4 节：里面只有 SKILL.md、references/<模块>.md、CHANGELOG.md
        names = sorted(str(p.relative_to(LIB)) for p in LIB.rglob("*") if p.is_file())
        self.assertIn("SKILL.md", names)
        self.assertIn("CHANGELOG.md", names)
        for n in names:
            self.assertTrue(n in ("SKILL.md", "CHANGELOG.md") or re.fullmatch(r"references/[a-z0-9-]+\.md", n), n)

    def test_front_matter(self):
        text = read(LIB / "SKILL.md")
        head = text.split("---")[1]
        self.assertIn("name: dt-shared-reference\n", head)
        self.assertRegex(head, r"\nversion: \d+\.\d+\.\d+\n")
        self.assertRegex(head, r'\ndescription: ".+不是入口.+"\n')

    def test_module_table_matches_files(self):
        listed = set(re.findall(r"`(references/[a-z0-9-]+\.md)`", read(LIB / "SKILL.md")))
        present = {str(p.relative_to(LIB)) for p in (LIB / "references").glob("*.md")}
        self.assertEqual(listed, present)


class References(unittest.TestCase):
    text = read(MODULE)

    def test_skills_named_exist(self):
        named = set(re.findall(r"`([a-z][a-z-]+)` 技能", self.text))
        self.assertTrue({"pdaily-act", "autoloop", "workshop", "merge-officer"} <= named, named)
        for s in named:
            self.assertTrue((SKILLS / s / "SKILL.md").is_file(), s)

    def test_scripts_and_subcommands_exist(self):
        calls = re.findall(r"`([a-z_]+\.py)((?: (?:[a-z-]+|/))*)`", self.text)
        self.assertTrue(calls)
        for script, rest in calls:
            path = SCRIPTS / script
            self.assertTrue(path.is_file(), script)
            src = read(path)
            for sub in [w for w in rest.split() if w != "/"]:
                self.assertIn(f'add_parser("{sub}"', src, f"{script} 没有子命令 {sub}")

    def test_docs_named_exist(self):
        named = [d for d in re.findall(r"`([^`\s]+\.md)`", self.text) if not d.startswith("references/")]
        self.assertTrue({"docs/常设授权清单.md", "CURRENT.md"} <= set(named), named)
        for d in named:
            self.assertTrue((ROOT / d).is_file(), d)

    def test_sections_named_exist(self):
        # 「`workshop` 技能第 2 节」「清单第 3 节」「`docs/技能架构.md` 第 9 节」这类指向要真有那一节
        def has(path, n):
            return re.search(rf"^## {n}\.", read(path), re.M) is not None
        refs = re.findall(r"`([a-z-]+)` 技能第 (\d+) 节", self.text)
        self.assertTrue(refs)
        for skill, n in refs:
            self.assertTrue(has(SKILLS / skill / "SKILL.md", n), f"{skill} 第 {n} 节")
        for n in re.findall(r"清单第 (\d+) 节", self.text):
            self.assertTrue(has(ROOT / "docs" / "常设授权清单.md", n), f"清单第 {n} 节")
        for doc, n in re.findall(r"`(docs/[^`]+\.md)` 第 (\d+) 节", self.text):
            self.assertTrue(has(ROOT / doc, n), f"{doc} 第 {n} 节")

    def channel_rows(self):
        part = self.text.split("## 3. 从哪发", 1)[1].split("## 4.", 1)[0]
        return {cells[1].strip(): cells for cells in (l.split("|") for l in part.splitlines() if l.startswith("| "))}

    def test_channel_new_base(self):
        # 新底座写入派单：只认卫士卡上的点击，私聊回话不算
        row = next(v for k, v in self.channel_rows().items() if k.startswith("新底座的写入派单"))
        self.assertIn("dt-sandbox", row[1])
        self.assertIn("卫士", row[2])
        self.assertIn("「批准派单」", row[3])
        self.assertNotIn("可以", row[3])
        self.assertIn("批准只认他在「卫士」确认卡上的点击", read(SKILLS / "autoloop" / "SKILL.md"))

    def test_channel_old_workshop(self):
        # 旧车间：先要他明说换通道，再对那张单回「可以」（#383 第 1 轮 P1-01）
        row = next(v for k, v in self.channel_rows().items() if k.startswith("旧车间的写入派单"))
        for cell in (row[2], row[3]):
            self.assertIn("「按老办法派」", cell)
        self.assertIn("只批这一张单", row[3])
        self.assertLess(row[3].index("按老办法派"), row[3].index("「可以」"))
        self.assertIn("明说「按老办法派」，才按 `workshop` 技能走", read(SKILLS / "autoloop" / "SKILL.md"))
        self.assertIn("不等于同意了走哪条通道", self.text)

    def test_tier_labels_match_standing_list(self):
        listed = dict(re.findall(r"^\| \*\*([A-D]) ([^*]+)\*\* \|", read(ROOT / "docs" / "常设授权清单.md"), re.M))
        self.assertEqual(sorted(listed), list("ABCD"))
        for tier, label in listed.items():
            self.assertIn(f"**{tier} 档**（{label}）", self.text)

    def test_quoted_channels_are_real(self):
        # 模块里让常浩点的、回的字样，要和现在真实的卡片、技能对得上
        self.assertIn("批准派单", self.text)
        self.assertIn('"✅ 批准派单"', read(SCRIPTS / "autoloop_cardview.py"))
        self.assertIn("📝 待确认派单", self.text)
        self.assertIn("📝 待确认派单", read(SKILLS / "workshop" / "SKILL.md"))

    def test_core_rules_present(self):
        for must in ("一次最多问三件事", "推荐放在第一个选项", "主要风险和代价", "不定会怎样", "不属于 D 档", "不是授权凭证", "私聊打字一律不算批准",
                     "含糊的回复不当高风险授权", "把回答带回原任务", "只按回答的范围做", "不拿已经定了的事再问一遍"):
            self.assertIn(must, self.text)

    def test_three_cases(self):
        # 第 9 节验收的三类案例：在授权范围内、超出授权、复杂主观取舍；不只看标题，也看各自的处理句
        part = self.text.split("## 5. 三个例子", 1)[1].split("## 6.", 1)[0]
        cases = re.split(r"\n\*\*例[一二三]：", part)[1:]
        self.assertEqual(len(cases), 3)
        must = [("在授权范围内，不打扰他", "A 档", "不发拍板请求"),
                ("超出授权，停下这个动作再问", "D 档", "【要你定】", "卫士卡上点", "不自己改走旧车间", "「按老办法派」",
                 "没同意换通道"),
                ("复杂的主观取舍", "推荐和一句依据", "最后由他选", "不因为他没回就按推荐做")]
        for body, words in zip(cases, must):
            for w in words:
                self.assertIn(w, body)

    def test_record_places_match_scripts(self):
        # 带回原任务时点名的记录位置，要和脚本真有的字段对得上（#383 第 1 轮 P2-01、P2-02）
        self.assertIn("没有消息号字段", self.text)
        self.assertIn("写进 `--note`（脚本只留前 200 字", self.text)
        src = read(SCRIPTS / "pdaily_act.py")
        self.assertIn('r.add_argument("--note")', src)
        self.assertNotIn("message", src.split('r = sub.add_parser("record")', 1)[1].split("sub.add_parser", 1)[0])
        # --note 要真的一路存进拍板账：cmd_record 交给 record_choices，record_choices 写进 note（#383 第 2 轮 P2-03）
        cmd = src.split("def cmd_record(a):", 1)[1].split("\ndef ", 1)[0]
        self.assertRegex(cmd, r"record_choices\([^)]*\ba\.note\)")
        self.assertRegex(src, r"def record_choices\([^)]*\bnote=None\)")
        self.assertIn('e["note"] = P.clip(note, 200)', src)
        self.assertIn("不自己找个文件代替账本", self.text)
        self.assertIn("依赖这个决定的动作也先不做", self.text)
        # 账本只要多了任何子命令（不管叫什么），就回来改模块第 4 节第 2 条的过渡办法（#383 第 2 轮 P2-03）
        subs = set(re.findall(r'add_parser\("([a-z_-]+)"', read(SCRIPTS / "openloop.py")))
        self.assertEqual(subs, {"scan", "show"}, "账本命令变了：改模块第 4 节第 2 条的过渡办法")


class NoStaleOrPrivate(unittest.TestCase):
    def test_typed_approval_gone(self):
        for p in [*LIB.rglob("*.md"), ROOT / "docs" / "技能架构.md"]:
            self.assertNotRegex(read(p), r"派单 <(8 位)?确认码>", str(p))

    def test_no_ids_in_library(self):
        for p in LIB.rglob("*.md"):
            self.assertNotRegex(read(p), r"\b(ou|oc|om|on)_[0-9a-z]{8,}", str(p))


if __name__ == "__main__":
    unittest.main()
