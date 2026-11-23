"""飞书卡片小零件（#333）：一段文字、灰色小字、分隔线、折叠面板、表格。发卡片的脚本共用这一份，不再各抄一份。

只管「给什么参数就画什么」：不读文件、不联网、不读环境变量、不 import 任何模块（连标准库也不用），Python 3.9 能跑。
每次调用都新画一份外壳，调用方改自己拿到的那份外壳不会串到别的卡片上。panel 的 elements、table 的 rows 只拷贝外层列表：
调用方之后再往自己的列表里加东西，改不到已经画好的零件；列表里的组件、行本身不拷贝，同一个组件对象放进两张卡，改它两张都变。

这里每个键、每个值、键的先后都是常浩定过稿的卡片的一部分（发给飞书的是 json.dumps 按先后拼出的文字，顺序一变字节就变）：
- 折叠面板外壳：日报乙版（docs/DECIDED.md 2026-11-19）、午晚单乙版（11-20）、中台卡片丙版（11-20）、托管单乙一（11-21）、
  群学习报告（11-17）共用的那一种；
- 表格外壳：日报速览表（2026-11-10 定稿的写法）、群学习报告、午晚单共用的那一种。
改这里任何一处，用它的卡片全部跟着变样：先按全局规则第 15 节发「改动确认」。tests/test_card_parts.py 把每个零件的产出写死比对，
tests/test_card_bytes.py 把各张卡片整张按字节比对。

不收的：各家的按钮（键的先后各不相同）、卡片外壳（标题栏、config）、只有一张卡用的版式（托管单的时间行、热点榜的名次行等），
它们留在各自脚本里。托管汇报卡的表格外壳和这里有四处不同，等 #298 合并后另收。
"""


def md(text, **extra):
    """一段文字（飞书 markdown 组件）。extra 原样接在后面，如 text_size="notation"、text_align="center"，先后照写的顺序。"""
    return {"tag": "markdown", "content": text, **extra}


def grey(text):
    """灰色小字（页脚、说明）。"""
    return md(f"<font color='grey'>{text}</font>", text_size="notation")


def hr():
    """分隔线。"""
    return {"tag": "hr"}


def panel(title, elements):
    """默认收起的折叠面板：灰底、灰边、圆角，标题右侧一个小箭头。elements 是面板里的一组组件，如 [md("…"), md("…")]。"""
    return {"tag": "collapsible_panel", "expanded": False, "background_color": "grey-50",
            "border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px",
            "header": {"title": {"tag": "plain_text", "content": title}, "width": "fill", "vertical_align": "center",
                       "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"},
                       "icon_position": "right", "icon_expanded_angle": -180},
            "elements": list(elements)}


def table(cols, rows):
    """表格：表头灰底加粗、自动行高、单元格顶对齐、换行不溢出。cols = [(键, 列名, 宽度)]，rows = [{键: 文字}]，文字按 markdown 显示。"""
    return {"tag": "table", "page_size": 10, "row_height": "auto", "row_max_height": "999px",
            "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": True, "lines": 1},
            "columns": [{"name": k, "display_name": n, "data_type": "lark_md", "width": w, "vertical_align": "top"}
                        for k, n, w in cols],
            "rows": list(rows)}
