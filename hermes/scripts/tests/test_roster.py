"""名册（projects.json）的结构校验：同一个仓库只能登记一次（M5 #7：dt-sandbox 差点在 inventory 里重复登记）。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import collections
import json
import os
import unittest
from pathlib import Path

ROSTER = Path(__file__).resolve().parents[3] / "projects.json"


def same_place(path):
    """本机路径按用的人那样展开 ~ 再规整（dispatch、merge_act 都先 expanduser），同一个目录的不同写法算同一个；
    不要求目录存在，CI 上没有本机名册里的目录。github: 路径原样，仓库查重在下面单独做。"""
    return path if path.startswith("github:") else os.path.normpath(os.path.expanduser(path))


def problems(d):
    """名册里重复登记的 path、GitHub 仓库、项目 id。每个条目的 github 字段和 github: 路径算同一个仓库，只计一次。"""
    entries = d.get("projects", []) + d.get("inventory", [])
    out = []

    def dup(what, values):
        out.extend(f"{what} 重复：{v}" for v, n in sorted(collections.Counter(values).items()) if n > 1)

    # 报重复时用名册里的原写法，好找；同一目录的几种写法只报最先登记的那种
    wrote = {}
    for e in entries:
        if e.get("path"):
            wrote.setdefault(same_place(e["path"]), []).append(e["path"])
    out.extend(f"path 重复：{ws[0]}" for _, ws in sorted(wrote.items()) if len(ws) > 1)
    repos = []
    for e in entries:
        own = {e.get("github")} | ({e["path"][len("github:"):]} if str(e.get("path", "")).startswith("github:") else set())
        repos += [r for r in own if r]
    dup("GitHub 仓库", repos)
    dup("项目 id", [e["id"] for e in d.get("projects", []) if e.get("id")])
    return out


class RosterTest(unittest.TestCase):
    def test_real_roster_has_no_duplicates(self):
        self.assertEqual(problems(json.loads(ROSTER.read_text(encoding="utf-8"))), [])

    def test_duplicate_repo_between_projects_and_inventory_is_found(self):
        d = {"projects": [{"id": "a", "path": "~/work/a", "github": "me/sandbox"}],
             "inventory": [{"path": "github:me/sandbox", "sandbox": True}]}
        self.assertEqual(problems(d), ["GitHub 仓库 重复：me/sandbox"])

    def test_duplicate_path_and_id_are_found(self):
        d = {"projects": [{"id": "a", "path": "~/work/a"}, {"id": "a", "path": "~/work/a"}], "inventory": []}
        self.assertEqual(problems(d), ["path 重复：~/work/a", "项目 id 重复：a"])

    def test_same_folder_written_differently_is_found(self):
        home = os.path.expanduser("~")
        for other in (home + "/work/a", "~/work/a/", "~/work/./a", "~/work/b/../a"):
            d = {"projects": [{"id": "a", "path": "~/work/a"}], "inventory": [{"path": other}]}
            self.assertEqual(problems(d), ["path 重复：~/work/a"], other)

    def test_different_folders_and_github_paths_are_not_mixed_up(self):
        d = {"projects": [{"id": "a", "path": "~/work/a"}, {"id": "b", "path": "~/work/ab"}],
             "inventory": [{"path": "github:work/a"}, {"path": "~/work/a b"}]}
        self.assertEqual(problems(d), [])

    def test_same_entry_github_field_and_path_count_once(self):
        d = {"projects": [], "inventory": [{"path": "github:me/x", "github": "me/x"}]}
        self.assertEqual(problems(d), [])


if __name__ == "__main__":
    unittest.main()
