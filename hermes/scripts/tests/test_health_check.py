"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

用临时目录造假的 state.db、agent.log 和巡检日志；发信的子进程打桩，不联网、不碰 ~/.hermes、不发飞书。
每月的沙盒和仓库自检在 test_self_check.py。
"""
import datetime as dt
import importlib.util
import io
import json
import os
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
                         ["昨天有 8 次主对话调用走了回退模型 deepseek-flash（主模型额度、登录或网络可能有问题）"])

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


class BotsTest(unittest.TestCase):
    """#147：猛鬼兽、亚古兽各查一遍；24 小时内异常退出；飞书技能源头比副本新时提醒。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.hermes = self.root / "hermes"
        self.agumon = self.hermes / "profiles/agumon"
        for home in (self.hermes, self.agumon):
            (home / "logs").mkdir(parents=True)
        for target, name, value in ((hc, "HERMES", self.hermes), (hc, "AGENT_SKILLS", self.root / "agents")):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def errors(self, home, *lines):
        (home / "logs/errors.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def unclean(at, hb="2026-11-08T20:33:53.713463+00:00"):
        return (f"{at},807 WARNING gateway.lifecycle_ledger: Previous gateway life (pid=1, started_at=x) "
                f"exited UNCLEANLY (no exit path ran) last_heartbeat_at={hb} last_mem=None suspected_oom=False")

    def test_unclean_exit_within_24h_reported_with_gap(self):
        self.errors(self.agumon, self.unclean("2026-11-07 07:00:00"), self.unclean("2026-11-09 07:59:58"),
                    "2026-11-09 08:00:00,1 WARNING gateway.run: something else")
        hb = dt.datetime.fromisoformat("2026-11-08T20:33:53+00:00").astimezone()
        self.assertEqual(hc.check_unclean_exits(self.agumon, dt.datetime(2026, 11, 9, 9, 45)),
                         [f"过去 24 小时网关异常退出 1 次（最近一次心跳停在 {hb:%m-%d %H:%M}，11-09 07:59 重新拉起）"])

    def test_unclean_exit_older_or_missing_log_is_quiet(self):
        self.assertEqual(hc.check_unclean_exits(self.agumon, dt.datetime(2026, 11, 9, 9, 45)), [])
        self.errors(self.agumon, self.unclean("2026-11-07 07:00:00"))
        self.assertEqual(hc.check_unclean_exits(self.agumon, dt.datetime(2026, 11, 9, 9, 45)), [])

    def skill(self, base, name, mtime):
        f = base / name / "SKILL.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
        os.utime(f, (mtime, mtime))

    def test_skill_source_newer_than_copy_reported_per_bot(self):
        src = self.root / "agents"
        self.skill(src, "lark-base", 2000)
        self.skill(src, "lark-doc", 1000)
        self.skill(self.hermes / "skills/lark", "lark-base", 1000)     # 源头更新过：报
        self.skill(self.agumon / "skills/lark", "lark-base", 3000)     # 机器人自己改过、比源头新：不报
        self.skill(self.agumon / "skills/lark", "lark-doc", 500)       # 报
        self.skill(self.agumon / "skills/lark", "lark-only-copy", 500)  # 源头没有：不报
        self.assertEqual(hc.check_skill_sync(), [
            "飞书技能源头（~/.agents/skills）比机器人副本新，核对后同步（副本里机器人自己加的内容先留存）："
            "猛鬼兽 lark-base；亚古兽 lark-doc"])

    def test_skill_sync_quiet_when_in_step(self):
        self.skill(self.root / "agents", "lark-base", 1000)
        self.skill(self.agumon / "skills/lark", "lark-base", 1000)
        self.assertEqual(hc.check_skill_sync(), [])

    def test_gateway_checked_by_label_and_home(self):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        (self.agumon / "gateway_state.json").write_text(json.dumps(
            {"gateway_state": "running", "updated_at": now, "platforms": {"feishu": {"state": "disconnected"}}}))
        listing = "PID\tStatus\tLabel\n123\t0\tai.hermes.gateway\n-\t75\tai.hermes.gateway-agumon\n"
        with patch.object(hc, "run", return_value=subprocess.CompletedProcess([], 0, listing, "")):
            self.assertEqual(hc.check_gateway("ai.hermes.gateway-agumon", self.agumon), [
                "Hermes 网关进程没在运行（上次退出码 75）", "飞书连接状态是 disconnected（无错误信息）"])
        with patch.object(hc, "run", return_value=subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n", "")):
            self.assertEqual(hc.check_gateway("ai.hermes.gateway-agumon", self.agumon),
                             ["Hermes 网关常驻任务没有加载（ai.hermes.gateway-agumon）"])

    def test_check_bots_runs_each_bot_with_its_home_and_prefix(self):
        seen = []

        def gateway(label, home):
            seen.append((label, home))
            return ["网关离线"] if home == self.agumon else []
        with patch.object(hc, "check_gateway", side_effect=gateway), \
                patch.object(hc, "check_unclean_exits", return_value=[]), \
                patch.object(hc, "check_fallback", return_value=[]), \
                patch.object(hc, "check_aux_errors", side_effect=lambda home: ["压缩失败"] if home == self.hermes else []):
            self.assertEqual(hc.check_bots(), ["猛鬼兽：压缩失败", "亚古兽：网关离线"])
        self.assertEqual(seen, [("ai.hermes.gateway", self.hermes), ("ai.hermes.gateway-agumon", self.agumon)])


class CronIncidentTest(unittest.TestCase):
    """只算 detected、alerted；已 ack（closed）的不再告警。"""

    NONE = "No cron failure incidents recorded.\n  (filtered by state '{}')\n"
    ONE = ("  abc_123  detected\n    Job:        abc\n    Type:       config\n"
           "    Error:      Script exited with code 1 stdout: 取数失败\n")

    def run_with(self, outputs):
        calls = []

        def fake(cmd, timeout=60):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, outputs.get(cmd[-1], self.NONE.format(cmd[-1])), "")
        with patch.object(hc, "run", side_effect=fake):
            return hc.check_cron(), calls

    def test_no_open_incidents(self):
        issues, calls = self.run_with({})
        self.assertEqual(issues, [])
        self.assertEqual([c[-2:] for c in calls], [["--state", "detected"], ["--state", "alerted"]])

    def test_open_incident_reported_with_error(self):
        issues, _ = self.run_with({"detected": self.ONE})
        self.assertEqual(len(issues), 1)
        self.assertIn("1 条未处理的失败记录", issues[0])
        self.assertIn("Script exited with code 1", issues[0])


class DailySendTest(unittest.TestCase):
    """每月部分已搬到 self_check.py：巡检只发日常异常，哪天跑都不再起别的子进程。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-health-test-")
        self.addCleanup(tmp.cleanup)
        self.logs = Path(tmp.name) / "health"
        self.patch(hc, "LOG_DIR", self.logs)
        self.gateway = self.patch(hc, "check_gateway", return_value=[])
        for name in ("check_fallback", "check_aux_errors", "check_cron", "check_unclean_exits", "check_skill_sync",
                     "check_egress", "check_pf"):
            self.patch(hc, name, return_value=[])
        self.sent = []
        # 保留真实 run()/send()，在最底层拦住所有子进程；意外命令立即让测试失败。
        self.process = self.patch(hc.subprocess, "run", side_effect=self.fake_process)

    def patch(self, target, name, *args, **kwargs):
        p = patch.object(target, name, *args, **kwargs)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def fake_process(self, cmd, **kwargs):
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

    @staticmethod
    def arg(cmd, flag):
        return cmd[cmd.index(flag) + 1]

    def test_issues_send_one_message_with_original_identity(self):
        self.gateway.return_value = ["网关离线"]
        self.main("2000-01-02")
        self.assertEqual(len(self.sent), 1)
        cmd = self.sent[0]
        self.assertEqual(self.arg(cmd, "--idempotency-key"), "health-2000-01-02 09:45")
        self.assertEqual(self.arg(cmd, "--profile"), hc.LARK_PROFILE)
        self.assertEqual(self.arg(cmd, "--as"), "bot")
        self.assertEqual(self.arg(cmd, "--chat-id"), hc.DM_CHAT)
        self.assertIn("网关离线", self.arg(cmd, "--text"))
        self.assertIn("health-2000-01-02 09:45 发送 成功", (self.logs / "2000-01.log").read_text())

    def test_dry_run_prints_without_sending(self):
        self.gateway.return_value = ["网关离线"]
        output = self.main("2000-01-02", "--dry-run")
        self.assertEqual(self.sent, [])
        self.assertIn("网关离线", output)
        self.assertNotIn("发送", (self.logs / "2000-01.log").read_text())

    def test_first_of_month_no_longer_runs_monthly_checks(self):
        output = self.main("2000-01-01", "--sandbox", "--repos")
        self.process.assert_not_called()
        self.assertEqual(output.strip(), "2000-01-01 09:45 巡检正常")
        self.assertFalse(hasattr(hc, "check_sandbox") or hasattr(hc, "check_repos"))



class WorkerIsolation(unittest.TestCase):
    """工人账号的过滤代理和门卫规则（#190）：在临时端口起假代理、在临时目录写假状态文件。"""

    def serve_once(self, reply):
        import socket
        import threading
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)

        def answer():
            conn, _ = srv.accept()
            with conn:
                conn.recv(200)
                conn.sendall(reply)
        threading.Thread(target=answer, daemon=True).start()
        return srv.getsockname()[1]

    def test_egress_refusing_loopback_is_ok(self):
        self.assertEqual(hc.check_egress(self.serve_once(b"HTTP/1.1 403 Forbidden\r\n\r\n")), [])

    def test_egress_letting_loopback_through_is_reported(self):
        out = hc.check_egress(self.serve_once(b"HTTP/1.1 200 Connection established\r\n\r\n"))
        self.assertIn("没拦住", out[0])

    def test_egress_not_running_is_reported(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertIn("没在运行", hc.check_egress(port)[0])

    def status(self, text, age_min=1, accounts=("dt-worker",)):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "pf.status"
        p.write_text(text)
        return hc.check_pf(p, now=p.stat().st_mtime + age_min * 60, accounts=list(accounts))

    def test_pf_ok(self):
        self.assertEqual(self.status("at=x enabled=yes rules=6\n"), [])
        both = ("dt-worker", "dt-secretary")
        self.assertEqual(self.status("at=x enabled=yes rules=13 users=dt-worker,dt-secretary\n", accounts=both), [])

    def test_pf_secretary_not_managed(self):
        """秘书账号建了，门卫规则还是 0A 旧版（没有 users）或没管上秘书：要报。"""
        both = ("dt-worker", "dt-secretary")
        for text in ("at=x enabled=yes rules=6\n", "at=x enabled=yes rules=6 users=dt-worker\n"):
            issues = self.status(text, accounts=both)
            self.assertEqual(len(issues), 1)
            self.assertIn("没管上 dt-secretary", issues[0])

    def test_pf_missing_disabled_or_stale(self):
        self.assertIn("没装", hc.check_pf(Path("/nonexistent/pf.status"))[0])
        self.assertIn("没在生效", self.status("at=x enabled=no rules=6\n")[0])
        self.assertIn("没在生效", self.status("没有 dt-worker 这个账号\n")[0])
        self.assertIn("没刷新", self.status("at=x enabled=yes rules=6\n", age_min=60)[0])


if __name__ == "__main__":
    unittest.main()
