"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("dispatch", Path(__file__).parents[1] / "dispatch.py")
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


class FollowupReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-dispatch-test-")
        self.addCleanup(tmp.cleanup)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.root = Path(tmp.name)
        self.no = "D20000101-1"
        self.order_dir = self.root / "dispatch" / self.no
        self.out = self.order_dir / "out"
        self.out.mkdir(parents=True)
        self.receipt = self.out / "receipt.json"
        self.order_file = self.order_dir / "order.json"
        self.order_file.write_text(json.dumps({
            "no": self.no, "project": "fixture", "tool": "codex", "mode": "write",
            "status": "done", "cwd": str(self.root),
        }))
        (self.order_dir / "task.md").write_text("虚构任务", encoding="utf-8")
        self.followup = self.root / "reply.md"
        self.followup.write_text("虚构答复", encoding="utf-8")
        self.original = b'{ "session_id": "fixture-session", "round": 1, "summary": "first" }\n'
        self.receipt.write_bytes(self.original)
        for name, value in {
            "ROOT": self.root / "dispatch", "WT_ROOT": self.root / "worktrees",
            "WORKER": self.root / "fake_worker.py", "ROSTER": self.root / "roster.json",
        }.items():
            self.patches.enter_context(patch.object(dispatch, name, value))
        self.before_worker = lambda cmd: None
        self.worker_calls = []
        self.worker = self.patches.enter_context(patch.object(dispatch.subprocess, "run", side_effect=self.fake_worker))

    def fake_worker(self, cmd, **kwargs):
        # 任一意外命令都直接失败；不会运行真实 worker、Git 或外部服务。
        self.assertEqual(cmd[1], str(self.root / "fake_worker.py"))
        self.assertEqual(Path(cmd[cmd.index("--out") + 1]), self.out)
        self.before_worker(cmd)
        self.worker_calls.append(cmd)
        self.receipt.write_text(json.dumps({
            "session_id": "fixture-session", "round": len(self.worker_calls) + 1,
            "summary": "新的回执",
        }, ensure_ascii=False), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def invoke(self, followup=True, success=True):
        argv = ["dispatch.py", "run", self.no]
        if followup:
            argv += ["--followup", str(self.followup)]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                dispatch.main()
        self.assertEqual(stopped.exception.code, 0 if success else 1, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["ok"], success)
        return result

    def test_first_followup_archives_before_worker_and_resumes(self):
        def before(cmd):
            self.assertEqual((self.out / "receipt-1.json").read_bytes(), self.original)
            self.assertEqual(self.receipt.read_bytes(), self.original)
            self.assertEqual(cmd[cmd.index("--resume") + 1], "fixture-session")
        self.before_worker = before
        self.invoke()
        self.assertEqual((self.out / "receipt-1.json").read_bytes(), self.original)
        self.assertEqual(json.loads(self.receipt.read_bytes())["round"], 2)

    def test_multiple_followups_preserve_every_round(self):
        expected = []
        for number in range(1, 4):
            expected.append(self.receipt.read_bytes())
            self.invoke()
            self.assertEqual(
                [(self.out / f"receipt-{i}.json").read_bytes() for i in range(1, number + 1)],
                expected,
            )
        self.assertEqual(json.loads(self.receipt.read_bytes())["round"], 4)

    def test_existing_history_and_occupied_names_are_never_overwritten(self):
        history = {1: b"first archive", 3: b"third archive", 10: b"tenth archive"}
        for number, data in history.items():
            (self.out / f"receipt-{number}.json").write_bytes(data)
        (self.out / "receipt-11.json").mkdir()
        dangling = self.out / "receipt-12.json"
        dangling.symlink_to(self.out / "missing-target")
        (self.out / "receipt-not-a-number.json").write_bytes(b"unrelated")
        self.invoke()
        for number, data in history.items():
            self.assertEqual((self.out / f"receipt-{number}.json").read_bytes(), data)
        self.assertTrue((self.out / "receipt-11.json").is_dir())
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(dangling.exists())
        self.assertEqual((self.out / "receipt-13.json").read_bytes(), self.original)
        self.assertEqual((self.out / "receipt-not-a-number.json").read_bytes(), b"unrelated")

    def test_non_followup_behavior_is_unchanged(self):
        existing = self.out / "receipt-1.json"
        existing.write_bytes(b"older history")
        for has_receipt in (True, False):
            with self.subTest(has_receipt=has_receipt):
                if not has_receipt:
                    self.receipt.unlink()
                self.invoke(followup=False)
                cmd = self.worker_calls[-1]
                self.assertNotIn("--resume", cmd)
                self.assertEqual(cmd[cmd.index("--task-file") + 1], str(self.order_dir / "task.md"))
                self.assertEqual(list(self.out.glob("receipt-*.json")), [existing])
                self.assertEqual(existing.read_bytes(), b"older history")

    def assert_stopped_without_changes(self):
        before_order = self.order_file.read_bytes()
        before_receipt = self.receipt.read_bytes()
        before_files = sorted(p.name for p in self.out.iterdir())
        result = self.invoke(success=False)
        self.assertIn("未启动车间", result["msg"])
        self.worker.assert_not_called()
        self.assertEqual(self.order_file.read_bytes(), before_order)
        self.assertEqual(self.receipt.read_bytes(), before_receipt)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), before_files)

    def test_invalid_receipts_stop_before_worker(self):
        for content in (b"not json", b"[]", b"null", b"", b"\xff"):
            with self.subTest(content=content):
                self.receipt.write_bytes(content)
                self.assert_stopped_without_changes()

    def test_unreadable_receipt_stops_before_worker(self):
        before_order = self.order_file.read_bytes()
        with patch.object(Path, "read_bytes", side_effect=PermissionError("模拟回执读取失败")):
            result = self.invoke(success=False)
        self.assertIn("未启动车间", result["msg"])
        self.worker.assert_not_called()
        self.assertEqual(self.receipt.read_bytes(), self.original)
        self.assertEqual(self.order_file.read_bytes(), before_order)
        self.assertEqual(list(self.out.iterdir()), [self.receipt])

    def test_archive_creation_sync_and_publish_failures_stop_before_worker(self):
        failures = (
            (dispatch.tempfile, "NamedTemporaryFile", PermissionError("模拟归档创建失败")),
            (dispatch.os, "fsync", OSError("模拟磁盘同步失败")),
            (dispatch.os, "link", OSError("模拟发布失败")),
        )
        for target, name, error in failures:
            with self.subTest(stage=name), patch.object(target, name, side_effect=error):
                self.assert_stopped_without_changes()

    def test_partial_archive_write_is_cleaned_and_worker_is_not_started(self):
        real_tempfile = dispatch.tempfile.NamedTemporaryFile

        @contextlib.contextmanager
        def failing_tempfile(*args, **kwargs):
            with real_tempfile(*args, **kwargs) as backup:
                def fail_write(content):
                    backup.write(content[:5])
                    backup.flush()
                    raise OSError("模拟写入一部分后磁盘已满")
                yield SimpleNamespace(write=fail_write)

        with patch.object(dispatch.tempfile, "NamedTemporaryFile", failing_tempfile):
            self.assert_stopped_without_changes()

    def test_history_created_during_publish_is_not_overwritten(self):
        real_link = dispatch.os.link
        archive = self.out / "receipt-1.json"
        before_order = self.order_file.read_bytes()

        def occupied_link(source, target):
            self.assertEqual(Path(target), archive)
            archive.write_bytes(b"another archive won the race")
            return real_link(source, target)

        with patch.object(dispatch.os, "link", side_effect=occupied_link):
            result = self.invoke(success=False)
        self.assertIn("未启动车间", result["msg"])
        self.worker.assert_not_called()
        self.assertEqual(self.order_file.read_bytes(), before_order)
        self.assertEqual(self.receipt.read_bytes(), self.original)
        self.assertEqual(archive.read_bytes(), b"another archive won the race")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["receipt-1.json", "receipt.json"])

    def test_missing_receipt_keeps_existing_fresh_session_behavior(self):
        self.receipt.unlink()
        self.invoke()
        self.assertNotIn("--resume", self.worker_calls[-1])
        self.assertEqual(list(self.out.iterdir()), [self.receipt])

    def test_missing_session_id_still_archives_receipt(self):
        self.receipt.write_bytes(b'{"session_id": null, "summary": "failed before session"}')
        original = self.receipt.read_bytes()
        self.invoke()
        self.assertNotIn("--resume", self.worker_calls[-1])
        self.assertEqual((self.out / "receipt-1.json").read_bytes(), original)

    def test_receipt_symlink_or_directory_cannot_bypass_archiving(self):
        self.receipt.unlink()
        before_order = self.order_file.read_bytes()
        target = self.root / "elsewhere.json"
        for kind in ("dangling symlink", "valid symlink", "directory"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    self.receipt.mkdir()
                else:
                    if kind == "valid symlink":
                        target.write_bytes(self.original)
                    self.receipt.symlink_to(target)
                result = self.invoke(success=False)
                self.assertIn("未启动车间", result["msg"])
                self.worker.assert_not_called()
                self.assertEqual(self.order_file.read_bytes(), before_order)
                self.assertEqual(list(self.out.iterdir()), [self.receipt])
                if kind == "directory":
                    self.receipt.rmdir()
                else:
                    self.assertTrue(self.receipt.is_symlink())
                    self.receipt.unlink()
        self.assertEqual(target.read_bytes(), self.original)

    def test_running_order_is_rejected_without_archiving(self):
        order = json.loads(self.order_file.read_bytes())
        order["status"] = "running"
        self.order_file.write_text(json.dumps(order))
        before_order = self.order_file.read_bytes()
        self.invoke(success=False)
        self.worker.assert_not_called()
        self.assertEqual(self.order_file.read_bytes(), before_order)
        self.assertEqual(self.receipt.read_bytes(), self.original)
        self.assertEqual(list(self.out.iterdir()), [self.receipt])


if __name__ == "__main__":
    unittest.main(verbosity=2)
