"""卡片字节快照（#333）：常浩定过稿的卡片，生成出来的 JSON 文字要和快照一个字节不差。

为什么要它：各脚本发卡片都是 json.dumps(card, ensure_ascii=False) 拼成文字再发，键的先后也算在内；原有的基线测试多数只比
「内容相同」（键换了先后不会红），群学习报告、热点榜连整卡基线都没有。把零件收进 card_parts.py 这类重构，靠这里证明没变样。

怎么组织（fixtures/card_bytes/）：
- <脚本>.in.json：固定输入，{场景名: 参数}。从各脚本原有测试的造数函数冻结下来的，之后和那些函数无关。
- <脚本>.<场景名>.card.json：快照，就是 json.dumps(卡片, ensure_ascii=False) 那一串文字，末尾没有换行。
- 托管单、午晚单、中台卡片三份原有基线（常浩定稿登记的那三张）不另存快照，直接拿原基线文件比，连键的先后一起比。

红了怎么办：**不要改快照**。快照是定稿卡片的样子，红了说明这次改动让卡片变样了：改回去；确实要变样的，先按全局规则第 15 节
发「改动确认」，常浩点头后删掉那一份快照，再跑 `/usr/bin/python3 -B test_card_bytes.py --write-missing` 补上
（只补缺的，已有的快照一份都不覆盖）。新加场景：在 .in.json 里加一项，再跑同一条命令。

不发飞书、不读正式目录：只调各脚本拼卡片的纯函数。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import aihot_hot  # noqa: E402
import autoloop_cardview  # noqa: E402
import away_sheet  # noqa: E402
import groupdigest  # noqa: E402
import openloop_slip  # noqa: E402
import pdaily_publish  # noqa: E402

DIR = HERE / "fixtures" / "card_bytes"
WRITE = "--write-missing"


def at(text):
    return dt.datetime.fromisoformat(text)


def text(card):
    """发给飞书的那一串文字（各脚本发卡片时都是这么拼的）。"""
    return json.dumps(card, ensure_ascii=False)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cardview(a):
    card = a["card"]
    return autoloop_cardview.render(card, lambda i: {"autoloop": a["token"], "o": i, "v": card["version"]}, **a.get("kw", {}))


def pdaily(a):
    if "reason" in a:
        return pdaily_publish.failure_card(a["day"], a["reason"])
    return pdaily_publish.build_card(a["facts"], a["card"], a["trace"], **a.get("kw", {}))


# 脚本 → 用一份固定输入拼出卡片
BUILD = {
    "away_sheet": lambda a: away_sheet.build_card(a["sheet"], at(a["now"])),
    "autoloop_cardview": cardview,
    "openloop_slip": lambda a: openloop_slip.build_card(a["ledger"], at(a["now"]), a["repos"]),
    "pdaily": pdaily,
    "groupdigest": lambda a: groupdigest.build_card(a["day"], a["fields"], a["meta"], a["mat"], **a.get("kw", {})),
    "aihot_hot": lambda a: aihot_hot.build_card(a["items"], at(a["now"]), a.get("banner_key")),
}


def cases():
    """[(脚本, 场景名)]，照 .in.json 里的先后。"""
    return [(family, name) for family in BUILD for name in read(DIR / f"{family}.in.json")]


def snapshot(family, name):
    return DIR / f"{family}.{name}.card.json"


def build(family, name):
    return text(BUILD[family](read(DIR / f"{family}.in.json")[name]))    # 每次重读输入：拼卡片的函数就算改了输入也串不到别的场景


def where(got, want):
    """第一处不一样的位置，前后各带一小段，方便看是哪个组件变了。"""
    i = next((k for k, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    return (f"第 {i} 个字符起不一样（现在 {len(got)} 个字符，快照 {len(want)} 个）\n"
            f"  现在：…{got[max(0, i - 60):i + 60]}…\n  快照：…{want[max(0, i - 60):i + 60]}…")


class Snapshots(unittest.TestCase):
    """每个场景一条测试（下面按 .in.json 逐个挂上来），名字是 test_<脚本>__<场景名>。"""

    def check(self, family, name):
        path = snapshot(family, name)
        self.assertTrue(path.exists(), f"{path.name} 没有快照：新加的场景跑 {WRITE} 补上（见文件头）")
        got, want = build(family, name), path.read_text(encoding="utf-8")
        self.assertTrue(got == want, f"{family} 的卡片「{name}」和定稿快照不一样了。不要改快照（见文件头）。\n{where(got, want)}")


for _family, _name in cases():
    setattr(Snapshots, f"test_{_family}__{_name}",
            lambda self, f=_family, n=_name: self.check(f, n))


class Bookkeeping(unittest.TestCase):
    def test_every_snapshot_belongs_to_a_case_and_the_other_way_round(self):
        want = sorted(snapshot(f, n).name for f, n in cases())
        self.assertEqual(sorted(p.name for p in DIR.glob("*.card.json")), want)

    def test_every_script_has_cases(self):
        for family in BUILD:
            self.assertGreaterEqual(len(read(DIR / f"{family}.in.json")), 3, family)

    def test_same_input_gives_same_bytes_twice(self):
        for family, name in cases():
            self.assertEqual(build(family, name), build(family, name), f"{family} {name}")

    def test_snapshots_are_what_json_dumps_writes(self):
        """快照是 json.dumps 的原样产物：没有手改进去的空格、换行。"""
        for family, name in cases():
            raw = snapshot(family, name).read_text(encoding="utf-8")
            self.assertEqual(raw, text(json.loads(raw)), f"{family} {name}")


class SettledBaselines(unittest.TestCase):
    """常浩定稿时登记的三份基线：原有测试比的是内容相同，这里连键的先后一起比。基线文件不动。"""

    def same(self, card, baseline, what):
        got, want = text(card), text(baseline)
        self.assertTrue(got == want, f"{what} 和登记的定稿基线不一样了（含键的先后）。不要改基线。\n{where(got, want)}")

    def test_away_sheet(self):
        fx = HERE / "fixtures" / "away_sheet"
        self.same(away_sheet.build_card(read(fx / "sheet.json"), at("2026-11-21T15:50:00+08:00")), read(fx / "card.json"), "托管单")

    def test_openloop_slip(self):
        fx = HERE / "fixtures" / "openloop_slip"
        card = openloop_slip.build_card(read(fx / "ledger.json"), at("2026-11-20T21:00:00+08:00"), {"dt": "o/dt", "skills": "o/skills"})
        self.same(card, read(fx / "card.json"), "午晚单")

    def test_autoloop_cardview(self):
        sample = read(HERE.parents[1] / "samples" / "autoloop-card-c.json")
        self.assertEqual(set(sample["states"]), {"open", "decided", "closed"})
        for name, s in sample["states"].items():
            self.same(cardview({"card": s["input"], "token": "0123456789abcdef", "kw": {"mark": "尾号 cdef"}}), s["card"], f"中台卡片（{name}）")


def write_missing():
    made = 0
    for family, name in cases():
        path = snapshot(family, name)
        if not path.exists():
            path.write_text(build(family, name), encoding="utf-8")
            made += 1
            print(f"补上 {path.name}")
    print(f"补了 {made} 份快照；已有的没动")


if __name__ == "__main__":
    if WRITE in sys.argv:
        write_missing()
    else:
        unittest.main()
