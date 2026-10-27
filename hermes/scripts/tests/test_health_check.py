"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

用临时目录造假的 state.db、agent.log 和巡检日志；仓库自检与发信的子进程全部打桩，不联网、不碰真实仓库或 ~/.hermes、不发飞书。
"""
import datetime as dt
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[3] / "ops/health_check.py"
spec = importlib.util.spec_from_file_location("health_check", SCRIPT)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

DAY = dt.date(2000, 1, 2)


def ts(day, hour=12):
    return dt.datetime.combine(day, dt.time(hour)).timestamp()


class HealthCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "logs").mkdir()
        self.old_home, hc.HERMES = hc.HERMES, self.home
        con = sqlite3.connect(self.home / "state.db")
        con.executescript(
            "create table sessions (id text primary key, source text);"
            "create table session_model_usage (session_id text, model text, billing_provider text,"
            " task text not null default '', api_call_count integer, last_seen real);")
        con.close()

    def tearDown(self):
        hc.HERMES = self.old_home
        self.tmp.cleanup()

    def usage(self, sid, source, provider, task, n, day=DAY):
        con = sqlite3.connect(self.home / "state.db")
        con.execute("insert or ignore into sessions values (?, ?)", (sid, source))
        con.execute("insert into session_model_usage values (?, 'm', ?, ?, ?, ?)",
                    (sid, provider, task, n, ts(day)))
        con.commit()
        con.close()

    def log(self, *lines):
        (self.home / "logs/agent.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_main_fallback_keeps_original_wording(self):
        self.usage("a", "feishu", "deepseek", "", 8)
        self.assertEqual(hc.check_fallback(DAY),
                         ["昨天有 8 次主对话调用走了回退模型 deepseek-flash（Codex 额度或登录可能有问题）"])

    def test_aux_fallback_listed_by_task(self):
        self.usage("a", "feishu", "deepseek", "title_generation", 2)
        self.usage("b", "feishu", "deepseek", "title_generation", 1)
        self.usage("b", "feishu", "deepseek", "compression", 1)
        self.usage("b", "feishu", "openai-codex", "background_review", 5)
        self.assertEqual(hc.check_fallback(DAY),
                         ["昨天辅助任务走了回退模型 deepseek-flash：compression 1 次、title_generation 3 次"])

    def test_oneshot_and_other_days_and_codex_ignored(self):
        self.usage("a", "oneshot", "deepseek", "", 3)
        self.usage("a", "oneshot", "deepseek", "title_generation", 1)
        self.usage("b", "feishu", "deepseek", "title_generation", 1, day=DAY - dt.timedelta(days=1))
        self.usage("c", "feishu", "openai-codex", "", 9)
        self.assertEqual(hc.check_fallback(DAY), [])

    def test_aux_log_errors_counted_for_that_day_only(self):
        d, prev = DAY.isoformat(), (DAY - dt.timedelta(days=1)).isoformat()
        self.log(
            f"{d} 10:00:00,1 WARNING agent.context_compressor: Failed to generate context summary: x",
            f"{d} 10:00:01,1 WARNING agent.auxiliary_client: resolve_provider_client: openai-codex requested but no Codex OAuth token found (run: hermes model)",
            f"{d} 10:00:02,1 WARNING agent.auxiliary_client: resolve_provider_client: openai-codex requested but no Codex OAuth token found (run: hermes model)",
            f"{prev} 23:59:59,1 WARNING agent.context_compressor: Failed to generate context summary: x",
            "    continuation line mentioning no Codex OAuth token found",
        )
        self.assertEqual(hc.check_aux_errors(DAY), [
            "昨天压缩失败 1 次（长会话会越积越长）",
            "昨天 2 次取不到 Codex 凭证，辅助任务会回退或失败；先查凭证冷却，见 agent-kit G-13",
        ])

    def test_clean_log_reports_nothing(self):
        self.log(f"{DAY.isoformat()} 10:00:00,1 INFO agent.auxiliary_client: Auxiliary compression: using openai-codex")
        self.assertEqual(hc.check_aux_errors(DAY), [])

    def test_missing_log_reported_not_crashed(self):
        issues = hc.check_aux_errors(DAY)
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].startswith("读不了 Hermes 日志"))


class RepositoryHealthCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-health-test-")
        self.addCleanup(tmp.cleanup)
        self.logs = Path(tmp.name) / "health"
        self.patch(hc, "LOG_DIR", self.logs)
        self.gateway = self.patch(hc, "check_gateway", return_value=[])
        for name in ("check_fallback", "check_aux_errors", "check_cron"):
            self.patch(hc, name, return_value=[])
        self.sandbox = self.patch(hc, "check_sandbox", return_value=[])
        self.failed = ["a：不是私有仓库", "b：主目录检出的是 dev，不是 main"]
        self.warnings = ["a：名册没写 test，合并前不跑测试"]
        self.result(self.failed, self.warnings)
        self.repo_error = None
        self.sent = []
        # 保留真实 run()/send()，在最底层拦住所有子进程；意外命令立即让测试失败。
        self.process = self.patch(hc.subprocess, "run", side_effect=self.fake_process)

    def patch(self, target, name, *args, **kwargs):
        p = patch.object(target, name, *args, **kwargs)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def result(self, failed, warnings):
        summary = {"ok": not failed, "failed": failed, "warnings": warnings}
        self.repo_result = subprocess.CompletedProcess(
            [], int(bool(failed)),
            "【a】虚构项目\n  通过  有 PR 模板\n  提示  不应发送的说明\n\n"
            + json.dumps(summary, ensure_ascii=False) + "\n\n", "")

    def fake_process(self, cmd, **kwargs):
        if cmd == [sys.executable, "-B", str(hc.CHECK_REPOS_SCRIPT)]:
            if self.repo_error:
                raise self.repo_error
            return self.repo_result
        if cmd[:1] == ["lark-cli"]:
            self.sent.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"不允许执行外部命令：{cmd}")

    def main(self, day="2000-01-02", *args):
        stamp = dt.datetime.fromisoformat(day + " 09:45:00")
        output = io.StringIO()
        with patch.object(hc.dt, "datetime") as clock, \
                patch.object(sys, "argv", [str(SCRIPT), *args]), redirect_stdout(output):
            clock.now.return_value = stamp
            hc.main()
        return output.getvalue()

    def repo_calls(self):
        return [c for c in self.process.call_args_list if c.args[0][0] == sys.executable]

    @staticmethod
    def arg(cmd, flag):
        return cmd[cmd.index(flag) + 1]

    def test_first_of_month_runs_both_even_with_empty_sandbox(self):
        self.main("2000-01-01")
        self.sandbox.assert_called_once_with()
        self.assertEqual(len(self.repo_calls()), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.arg(self.sent[0], "--idempotency-key"), "repos-2000-01")

    def test_other_days_skip_both_monthly_checks(self):
        self.main()
        self.sandbox.assert_not_called()
        self.process.assert_not_called()

    def test_repos_flag_runs_only_repos_on_other_days(self):
        self.main("2000-01-02", "--repos")
        self.sandbox.assert_not_called()
        self.assertEqual(len(self.repo_calls()), 1)
        self.assertEqual(len(self.sent), 1)

    def test_sandbox_flag_runs_only_sandbox_on_other_days(self):
        self.sandbox.return_value = [Path("/虚构沙盒/旧目录")]
        self.main("2000-01-02", "--sandbox")
        self.sandbox.assert_called_once_with()
        self.assertEqual(self.repo_calls(), [])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.arg(self.sent[0], "--idempotency-key"), "sandbox-2000-01")

    def test_manual_flags_send_separate_messages_and_only_repo_failures(self):
        self.gateway.return_value = ["网关离线"]
        self.sandbox.return_value = [Path("/虚构沙盒/旧目录")]
        self.main("2000-01-02", "--sandbox", "--repos")
        self.assertEqual([self.arg(cmd, "--idempotency-key") for cmd in self.sent],
                         ["health-2000-01-02 09:45", "sandbox-2000-01", "repos-2000-01"])
        msg = self.arg(self.sent[2], "--text")
        self.assertEqual(msg.splitlines()[1:], [f"- {item}" for item in self.failed])
        for word in self.warnings + ["通过", "虚构项目", "旧目录", "网关离线"]:
            self.assertNotIn(word, msg)
        for cmd in self.sent:
            self.assertEqual(self.arg(cmd, "--profile"), hc.LARK_PROFILE)
            self.assertEqual(self.arg(cmd, "--as"), "bot")
            self.assertEqual(self.arg(cmd, "--chat-id"), hc.DM_CHAT)
        self.assertIn("repos-2000-01 发送 成功", (self.logs / "2000-01.log").read_text())

    def test_all_pass_or_warnings_only_do_not_send(self):
        for warnings in ([], self.warnings):
            with self.subTest(warnings=warnings):
                self.result([], warnings)
                output = self.main("2000-01-02", "--repos")
                self.assertEqual(self.sent, [])
                self.assertNotIn("仓库自检提醒", output)
                self.assertIn("仓库自检：OK", (self.logs / "2000-01.log").read_text())
        self.assertEqual(len(self.repo_calls()), 2)

    def test_month_key_stays_same_within_month_and_changes_across_year(self):
        for day in ("2000-12-01", "2000-12-31", "2001-01-01"):
            self.main(day, "--repos")
        self.assertEqual([self.arg(cmd, "--idempotency-key") for cmd in self.sent],
                         ["repos-2000-12", "repos-2000-12", "repos-2001-01"])

    def test_dry_run_prints_failures_without_sending_any_message(self):
        self.gateway.return_value = ["网关离线"]
        self.sandbox.return_value = [Path("/虚构沙盒/旧目录")]
        output = self.main("2000-01-01", "--dry-run")
        self.assertEqual(len(self.repo_calls()), 1)
        self.assertEqual(self.sent, [])
        for word in self.failed + ["网关离线", "旧目录"]:
            self.assertIn(word, output)
        self.assertNotIn(self.warnings[0], output)
        self.assertNotIn("发送", (self.logs / "2000-01.log").read_text())

    def test_manual_repos_dry_run_also_does_not_send(self):
        output = self.main("2000-01-02", "--repos", "--dry-run")
        self.assertEqual(len(self.repo_calls()), 1)
        self.assertEqual(self.sent, [])
        self.assertIn(self.failed[0], output)
        self.sandbox.assert_not_called()

    def test_child_uses_sibling_script_same_python_and_longer_timeout(self):
        with patch.dict(hc.os.environ, {"DT_ROSTER": "/虚构名册/projects.json"}):
            self.assertEqual(hc.check_repos(), self.failed)
        call = self.repo_calls()[0]
        self.assertEqual(call.args[0], [sys.executable, "-B", str(SCRIPT.with_name("check_repos.py"))])
        self.assertEqual(call.kwargs["timeout"], hc.CHECK_REPOS_TIMEOUT)
        self.assertGreater(call.kwargs["timeout"], 60)
        self.assertEqual(call.kwargs["env"]["DT_ROSTER"], "/虚构名册/projects.json")
        self.assertEqual(call.kwargs["env"]["PATH"], hc.PATH)

    def test_empty_invalid_or_inconsistent_summary_is_not_success(self):
        cases = [(0, ""), (1, "Traceback: fake"), (0, "null"), (0, "[]"), (0, "{}"),
                 (0, '{"ok":true,"failed":"不是列表"}'),
                 (1, '{"ok":false,"failed":[1]}'),
                 (0, '{"ok":false,"failed":[]}'),
                 (1, '{"ok":true,"failed":[]}'),
                 (0, '{"ok":false,"failed":["失败项"]}'),
                 (0, '{"ok":true,"failed":[]}\n其他尾行')]
        for code, output in cases:
            with self.subTest(code=code, output=output):
                self.repo_result = subprocess.CompletedProcess([], code, output, "")
                issues = hc.check_repos()
                self.assertEqual(len(issues), 1)
                self.assertIn("仓库自检未完成", issues[0])

    def test_unexpected_exit_code_is_not_success_even_with_valid_json(self):
        self.result([], [])
        self.repo_result.returncode = 2  # Python 找不到脚本等执行失败
        self.assertEqual(hc.check_repos(), ["仓库自检未完成（退出码 2）"])

    def test_timeout_and_launch_error_are_reported(self):
        for error, detail in ((subprocess.TimeoutExpired("fake", hc.CHECK_REPOS_TIMEOUT), "超过"),
                              (FileNotFoundError("fake python"), "无法启动")):
            with self.subTest(error=error):
                self.repo_error = error
                issues = hc.check_repos()
                self.assertEqual(len(issues), 1)
                self.assertIn("仓库自检未完成", issues[0])
                self.assertIn(detail, issues[0])

    def test_incomplete_check_sends_failure_reminder(self):
        self.repo_result = subprocess.CompletedProcess([], 1, "invalid", "fake traceback")
        self.main("2000-01-01")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("仓库自检未完成", self.arg(self.sent[0], "--text"))
        self.assertNotIn("fake traceback", self.arg(self.sent[0], "--text"))


if __name__ == "__main__":
    unittest.main()
