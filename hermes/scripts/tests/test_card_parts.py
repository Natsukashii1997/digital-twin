"""卡片小零件 card_parts.py（#333）自测：每个零件给定参数，产出的 JSON 文字写死比对（键的先后也算）。

这里写死的文字就是常浩定过稿的折叠面板、表格的外壳。红了不要改这里的期望值：先看是不是把零件改变样了；
确实要变的先按全局规则第 15 节发「改动确认」。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import ast
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import card_parts as P  # noqa: E402

PANEL = ('{"tag": "collapsible_panel", "expanded": false, "background_color": "grey-50", '
         '"border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px", '
         '"header": {"title": {"tag": "plain_text", "content": "其余 3 件 · 展开"}, "width": "fill", "vertical_align": "center", '
         '"icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"}, '
         '"icon_position": "right", "icon_expanded_angle": -180}, '
         '"elements": [{"tag": "markdown", "content": "- 第一件"}, {"tag": "markdown", "content": "- 第二件"}]}')
TABLE = ('{"tag": "table", "page_size": 10, "row_height": "auto", "row_max_height": "999px", '
         '"header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": true, "lines": 1}, '
         '"columns": [{"name": "k", "display_name": "指标", "data_type": "lark_md", "width": "25%", "vertical_align": "top"}, '
         '{"name": "v", "display_name": "昨日", "data_type": "lark_md", "width": "75%", "vertical_align": "top"}], '
         '"rows": [{"k": "合并 PR", "v": "**2**"}]}')


def text(part):
    return json.dumps(part, ensure_ascii=False)


class Parts(unittest.TestCase):
    def test_md_is_tag_content_then_extras_in_the_order_written(self):
        self.assertEqual(text(P.md("**标题**")), '{"tag": "markdown", "content": "**标题**"}')
        self.assertEqual(text(P.md("01", text_size="heading", text_align="center")),
                         '{"tag": "markdown", "content": "01", "text_size": "heading", "text_align": "center"}')
        self.assertEqual(text(P.md("01", text_align="center", text_size="heading")),
                         '{"tag": "markdown", "content": "01", "text_align": "center", "text_size": "heading"}')

    def test_grey_is_small_grey_text(self):
        self.assertEqual(text(P.grey("托管方：Claude")),
                         '{"tag": "markdown", "content": "<font color=\'grey\'>托管方：Claude</font>", "text_size": "notation"}')

    def test_hr(self):
        self.assertEqual(text(P.hr()), '{"tag": "hr"}')

    def test_panel_is_the_settled_folded_shell(self):
        self.assertEqual(text(P.panel("其余 3 件 · 展开", [P.md("- 第一件"), P.md("- 第二件")])), PANEL)

    def test_panel_takes_any_components(self):
        button = {"tag": "button", "text": {"tag": "plain_text", "content": "改选：保留 pip"}}
        self.assertEqual(P.panel("不同意？其他选择 · 展开", [button])["elements"], [button])

    def test_table_is_the_settled_shell(self):
        self.assertEqual(text(P.table((("k", "指标", "25%"), ("v", "昨日", "75%")), [{"k": "合并 PR", "v": "**2**"}])), TABLE)
        self.assertEqual(text(P.table([("k", "指标", "25%"), ("v", "昨日", "75%")], [{"k": "合并 PR", "v": "**2**"}])), TABLE)   # 列写成列表也一样


class Fresh(unittest.TestCase):
    """每次调用新画一份：改了拿到的那份，下一次画出来的不受影响；调用方之后再动自己传进来的列表，也改不到已经画好的零件。"""

    def test_each_call_draws_a_new_object(self):
        for draw in (P.hr, lambda: P.md("x"), lambda: P.grey("x"), lambda: P.panel("t", []), lambda: P.table([("k", "n", "1%")], [])):
            first, again = draw(), draw()
            self.assertIsNot(first, again)
            before = text(again)
            first["tag"] = "改掉"
            for v in first.values():
                if isinstance(v, dict):
                    v.clear()
            self.assertEqual(text(draw()), before)

    def test_callers_list_is_copied(self):
        inside, rows = [P.md("一")], [{"k": "一"}]
        panel, table = P.panel("t", inside), P.table([("k", "n", "100%")], rows)
        inside.append(P.md("二"))
        rows.append({"k": "二"})
        self.assertEqual(len(panel["elements"]), 1)
        self.assertEqual(len(table["rows"]), 1)


class Standalone(unittest.TestCase):
    def test_imports_nothing(self):
        """不依赖别的脚本，连标准库也不用：托管单、中台卡片这类「可信边界收得很紧」的脚本才能放心用它。"""
        tree = ast.parse((SCRIPTS / "card_parts.py").read_text(encoding="utf-8"))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], [])

    def test_only_the_five_parts(self):
        tree = ast.parse((SCRIPTS / "card_parts.py").read_text(encoding="utf-8"))
        self.assertEqual([n.name for n in tree.body if isinstance(n, ast.FunctionDef)], ["md", "grey", "hr", "panel", "table"])
        self.assertEqual([n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.Expr))], [])   # 没有模块级的变量、常量


if __name__ == "__main__":
    unittest.main()
