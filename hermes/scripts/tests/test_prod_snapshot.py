"""prod_snapshot.py（正式目录元数据摘要，#212）自测：只在临时目录里演示「改前 / 改后」，不碰真实正式目录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import builtins
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prod_snapshot as ps  # noqa: E402

TOOL = str(HERE / "prod_snapshot.py")


class Snapshot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-snap-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "prod"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "rules.json").write_text("{}", encoding="utf-8")
        (self.root / "sub/state.db").write_bytes(b"x" * 10)
        (self.root / "gone.txt").write_text("bye", encoding="utf-8")
        (self.root / "link").symlink_to("rules.json")
        self.secret = self.root / "hermes-source.key"
        self.secret.write_text("fixture", encoding="utf-8")
        self.secret.chmod(0)                                       # 读不了内容，摘要照样能取
        self.addCleanup(self.secret.chmod, 0o600)

    def take(self, **kw):
        return ps.take([self.root], **kw)

    def test_unchanged_has_no_diff(self):
        a = self.take()
        self.assertEqual(ps.diff(a, self.take()), [])
        ps.assert_unchanged(a)
        self.assertEqual(a["roots"][str(self.root)]["count"], 7)

    def test_finds_every_kind_of_change(self):
        a = self.take()
        (self.root / "new.json").write_text("{}", encoding="utf-8")                # 新增
        (self.root / "gone.txt").unlink()                                           # 删除
        (self.root / "sub/state.db").write_bytes(b"x" * 11)                         # 大小变了
        st = (self.root / "rules.json").stat()
        os.utime(self.root / "rules.json", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))   # 只改修改时间
        (self.root / "link").unlink()
        (self.root / "link").symlink_to("new.json")                                 # 链接换了指向
        changes = "\n".join(ps.diff(a, self.take()))
        self.assertIn(f"新增 {self.root}/new.json", changes)
        self.assertIn(f"删除 {self.root}/gone.txt", changes)
        self.assertIn(f"变了 {self.root}/sub/state.db（文件 10 字节 → 文件 11 字节", changes)
        self.assertIn(f"变了 {self.root}/rules.json（修改时间变了）", changes)
        self.assertIn("→ new.json", changes)
        with self.assertRaises(ps.ProdChanged) as cm:
            ps.assert_unchanged(a)
        self.assertIn("new.json", str(cm.exception))

    def test_never_opens_files(self):
        def refuse(*a, **kw):
            raise AssertionError("摘要不许打开文件")
        with mock.patch.object(builtins, "open", refuse), mock.patch.object(io, "open", refuse), \
                mock.patch.object(Path, "read_bytes", refuse), mock.patch.object(Path, "read_text", refuse):
            snap = self.take()
        self.assertEqual(snap["roots"][str(self.root)]["entries"]["hermes-source.key"][0], "f")

    def test_missing_root_and_created_later(self):
        missing = self.root.parent / "not-yet"
        a = ps.take([missing])
        self.assertIsNone(a["roots"][str(missing)]["entries"])
        self.assertEqual(ps.diff(a, ps.take([missing])), [])
        missing.mkdir()
        self.assertEqual(ps.diff(a, ps.take([missing])), [f"{missing}：新建出来了"])

    def test_exclude_skips_live_files(self):
        a = self.take(excludes=["*/sub/state.db"])
        (self.root / "sub/state.db").write_bytes(b"y" * 99)
        self.assertEqual(ps.diff(a, self.take(excludes=["*/sub/state.db"])), [])
        ps.assert_unchanged(a)                                     # 沿用摘要里记下的排除规则

    def test_excluding_a_directory_still_checks_what_is_inside(self):
        pat = [str(self.root / "sub")]                             # 只不比目录本身的修改时间
        a = self.take(excludes=pat)
        (self.root / "sub/new.db").write_bytes(b"")
        self.assertEqual(ps.diff(a, self.take(excludes=pat)), [f"新增 {self.root}/sub/new.db（文件 0 字节）"])

    def test_exclude_also_covers_the_root_itself(self):
        a = self.take(excludes=[str(self.root)])
        (self.root / "new.json").write_text("{}", encoding="utf-8")            # 根目录的修改时间跟着变
        self.assertEqual(ps.diff(a, self.take(excludes=[str(self.root)])), [f"新增 {self.root}/new.json（文件 2 字节）"])

    def test_default_roots_are_the_real_account_not_isolated_home(self):
        self.assertNotEqual(ps.REAL_HOME, Path.home())             # 测试隔离把 HOME 换成了临时目录
        for r in ps.DEFAULT_ROOTS[:4]:
            self.assertTrue(str(r).startswith(str(ps.REAL_HOME) + "/"), r)
        self.assertIn(Path("/Users/Shared/dt-autoloop"), ps.DEFAULT_ROOTS)


class Cli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-snap-cli-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "prod"
        self.root.mkdir()
        (self.root / "a.txt").write_text("a", encoding="utf-8")

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", TOOL, *args], capture_output=True, text=True, timeout=60)

    def test_take_then_check(self):
        before = str(self.dir / "before.json")
        self.assertEqual(self.cli("take", before, "--root", str(self.root)).returncode, 0)
        r = self.cli("check", before)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("前后一致", r.stderr)
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        r = self.cli("check", before)
        self.assertEqual(r.returncode, ps.CHANGED)
        self.assertIn(f"新增 {self.root}/b.txt", r.stderr)

    def test_diff_two_saved_snapshots(self):
        a, b = str(self.dir / "a.json"), str(self.dir / "b.json")
        self.cli("take", a, "--root", str(self.root))
        (self.root / "a.txt").write_text("aa", encoding="utf-8")
        self.cli("take", b, "--root", str(self.root))
        r = self.cli("diff", a, b)
        self.assertEqual(r.returncode, ps.CHANGED)
        self.assertIn("文件 1 字节 → 文件 2 字节", r.stderr)

    def test_run_wraps_a_command(self):
        r = self.cli("run", "--root", str(self.root), "--", sys.executable, "-c", "pass")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli("run", "--root", str(self.root), "--", sys.executable, "-c", "import sys; sys.exit(5)")
        self.assertEqual(r.returncode, 5)                          # 没变时退出码同命令
        touch = f"open({str(self.root / 'c.txt')!r}, 'w').close()"
        r = self.cli("run", "--root", str(self.root), "--out-dir", str(self.dir / "out"), "--", sys.executable, "-c",
                     touch)
        self.assertEqual(r.returncode, ps.CHANGED)
        self.assertIn(f"新增 {self.root}/c.txt", r.stderr)
        self.assertTrue((self.dir / "out/before.json").exists() and (self.dir / "out/after.json").exists())


if __name__ == "__main__":
    unittest.main()
