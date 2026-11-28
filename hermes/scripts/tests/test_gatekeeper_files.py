"""门卫目录的文件清单（ops/gatekeeper_files.txt，#405 审查 P2-02）：按导入关系现算 gatekeeper.py 要的同目录脚本，和清单逐个对上。

门卫目录是 root 手工装的一份副本，少一个文件门卫启动就导入失败（#358 加的 task_brief.py、#319 起 autoloop_publish 导入的
gate_github.py 都是这种）。这里只读源码，不跑门卫、不碰真实安装目录。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import ast
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
LIST = SCRIPTS.parents[1] / "ops" / "gatekeeper_files.txt"


def closure(start, here=SCRIPTS):
    """start 连同它直接、间接 import 的同目录脚本（函数里的 import 也算：跑到那里一样要有文件）。"""
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((here / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods = [node.module.split(".")[0]]
            else:
                continue
            todo += [m for m in mods if (here / f"{m}.py").is_file()]
    return {f"{n}.py" for n in seen}


class GatekeeperFiles(unittest.TestCase):
    def listed(self):
        return [l.strip() for l in LIST.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]

    def test_list_matches_import_closure(self):
        self.assertEqual(sorted(self.listed()), sorted(closure("gatekeeper")))

    def test_list_has_no_duplicates_and_files_exist(self):
        names = self.listed()
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertTrue((SCRIPTS / n).is_file(), n)
        self.assertIn("task_brief.py", names)       # autoloop_grants 顶层导入它（#358）
        self.assertIn("gate_github.py", names)      # autoloop_publish 顶层导入它（#319）

    def test_closure_sees_function_level_and_indirect_imports(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            here = Path(d)
            (here / "a.py").write_text("import os\nimport b\n", encoding="utf-8")
            (here / "b.py").write_text("def f():\n    import c\n", encoding="utf-8")
            (here / "c.py").write_text("from d import x\n", encoding="utf-8")
            (here / "d.py").write_text("x = 1\n", encoding="utf-8")
            (here / "e.py").write_text("", encoding="utf-8")
            self.assertEqual(closure("a", here), {"a.py", "b.py", "c.py", "d.py"})


if __name__ == "__main__":
    unittest.main()
