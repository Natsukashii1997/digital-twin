"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

用临时目录造假的 state.db 和 agent.log 跑 ops/health_check.py 的回退检查，不碰真实 ~/.hermes，不发飞书。
"""
import datetime as dt
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
