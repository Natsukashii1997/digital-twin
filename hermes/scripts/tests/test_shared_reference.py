"""共享参考库技能 dt-shared-reference（#359，docs/技能架构.md 第 4 节、第 9 节 5b-2）的静态核对。

参考库是给别的技能按文件读的说明文字，模型怎么用它这里测不到（要装上后用 hermes chat 跑案例）。
这里只核文字和仓库对不对得上：
  1. 文件齐，技能头写对，SKILL.md 模块表列的文件都在、references/ 下的文件都列进了表；
  2. 模块里点名的技能、脚本、脚本子命令、文档都真的存在，引用的卡片字样真的在卡片上；
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
        for d in re.findall(r"`(docs/[^`]+\.md)`", self.text):
            self.assertTrue((ROOT / d).is_file(), d)

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
        for must in ("一次最多问三件事", "推荐放在第一个选项", "不定会怎样", "不是授权凭证", "私聊打字一律不算批准",
                     "含糊的回复不当高风险授权", "把回答带回原任务", "只按回答的范围做", "不拿已经定了的事再问一遍"):
            self.assertIn(must, self.text)

    def test_three_cases(self):
        # 第 9 节验收的三类案例：在授权范围内、超出授权、复杂主观取舍
        for case in ("在授权范围内，不打扰他", "超出授权，停下这个动作再问", "复杂的主观取舍"):
            self.assertIn(case, self.text)


class NoStaleOrPrivate(unittest.TestCase):
    def test_typed_approval_gone(self):
        for p in [*LIB.rglob("*.md"), ROOT / "docs" / "技能架构.md"]:
            self.assertNotRegex(read(p), r"派单 <(8 位)?确认码>", str(p))

    def test_no_ids_in_library(self):
        for p in LIB.rglob("*.md"):
            self.assertNotRegex(read(p), r"\b(ou|oc|om|on)_[0-9a-z]{8,}", str(p))


if __name__ == "__main__":
    unittest.main()
