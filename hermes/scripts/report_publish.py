"""把 Hermes 写好的日报 / 周报插到并跑文档最上面（标题和说明段之后），写后回读（只用标准库）。

用法：python3 report_publish.py daily|weekly <Markdown 文件>
检查：第一行标题格式和星期对得上；日报六段和覆盖行都在；不含表格；同一标题文档里还没有。
不通过或写入失败：输出 {"ok": false, "error": …}，退出码 1，不重试。成功另存一份到证据目录。
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import report_data as rd  # noqa: E402

EVIDENCE = Path(os.environ.get("DT_REPORT_EVIDENCE", "~/Library/Application Support/DigitalTwin/evidence/reports")).expanduser()
DAILY_TITLE = re.compile(r"^## 📅 (\d{4}-\d{2}-\d{2})（周([一二三四五六日])）$")
WEEKLY_TITLE = re.compile(r"^## 第(\d+)周 (\d{4})\.(\d+)（(\d{4})\.(\d{2})\.(\d{2}) - (\d{2})\.(\d{2})）$")
DAILY_PARTS = ["### 一、", "### 二、", "### 三、", "### 四、", "### 五、", "### 六、", "来源与覆盖"]
WEEKLY_PARTS = ["### 一、", "### 二、", "### 三、", "### 四、", "### 五、", "### 六、"]


def check(kind, text):
    """返回标题；不合格抛 ValueError。"""
    title = text.strip().splitlines()[0].strip() if text.strip() else ""
    if kind == "daily":
        m = DAILY_TITLE.match(title)
        if not m:
            raise ValueError(f"标题格式不对：{title[:40]}（应为「## 📅 YYYY-MM-DD（周X）」）")
        if rd.WEEKDAYS[dt.date.fromisoformat(m.group(1)).weekday()] != m.group(2):
            raise ValueError(f"标题里的星期和日期对不上：{title}")
        parts = DAILY_PARTS
    else:
        m = WEEKLY_TITLE.match(title)
        if not m:
            raise ValueError(f"标题格式不对：{title[:40]}（应为「## 第NN周 YYYY.NN（YYYY.MM.DD - MM.DD）」）")
        if dt.date(int(m.group(4)), int(m.group(5)), int(m.group(6))).isocalendar()[1] != int(m.group(1)):
            raise ValueError(f"周号和起始日期对不上：{title}")
        parts = WEEKLY_PARTS
    missing = [p for p in parts if p not in text]
    if missing:
        raise ValueError(f"缺段：{'、'.join(missing)}")
    if re.search(r"^\s*\|.*\|\s*$", text, re.M):
        raise ValueError("含表格行（新结构不用表格，改成「｜」分隔的单行）")
    if len(re.findall(r"^## ", text, re.M)) != 1:
        raise ValueError("只能有一个二级标题（一篇一个日期）")
    return title


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("daily", "weekly"):
        print(__doc__)
        sys.exit(2)
    kind, path = sys.argv[1], Path(sys.argv[2])
    doc = rd.DAILY_DOC if kind == "daily" else rd.WEEKLY_DOC
    try:
        text = path.read_text(encoding="utf-8").strip() + "\n"
        title = check(kind, text)
        if title in rd.fetch_doc(doc).splitlines():
            raise ValueError(f"文档里已经有「{title}」，不重写")
        xml = rd.lark("docs", "+fetch", "--doc", doc, "--detail", "with-ids")["document"]["content"]
        anchor = re.search(r'</title>\s*<p id="([^"]+)"', xml)
        if not anchor:
            raise ValueError("找不到文档开头的说明段，没法定位插入点")
        rd.lark("docs", "+update", "--doc", doc, "--command", "block_insert_after", "--block-id", anchor.group(1),
                "--doc-format", "markdown", "--content", text)
        after = rd.fetch_doc(doc).splitlines()
        if after.count(title) != 1:
            raise ValueError(f"写后回读：「{title}」出现 {after.count(title)} 次，请人工核对，不要重跑")
    except (ValueError, rd.Fail, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^\w.-]", "", title.replace("## ", ""))[:40]
    (EVIDENCE / f"{kind}-{key}.md").write_text(text, encoding="utf-8")
    print(json.dumps({"ok": True, "title": title, "doc": f"https://vm94j8bzy7.feishu.cn/docx/{doc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
