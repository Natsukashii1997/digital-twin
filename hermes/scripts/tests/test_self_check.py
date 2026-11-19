"""隔离自测：ops/self_check.py（安装副本漂移、Tidings 健康检查、每月沙盒和仓库自检）。

漂移检查用临时 git 仓库当 DT_REPO、临时目录当 HERMES_HOME 和 DT_BIN，HOME 也换成临时目录；git 用临时全局配置。
仓库自检的子进程打桩或用空名册跑真的 check_repos.py。Tidings 的 node 换成当前 python、health.js 换成临时脚本。不联网，不碰真实仓库、~/.hermes、安装副本和 Vault。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[3] / "ops/self_check.py"
spec = importlib.util.spec_from_file_location("self_check", SCRIPT)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

# 仓库路径 → 安装路径（相对临时 HOME：hermes 是 HERMES_HOME，bin 是 DT_BIN）；None 表示不安装
SOURCES = {
    "hermes/scripts/a.py": "hermes/scripts/a.py",
    "hermes/scripts/notes.md": None,
    "hermes/scripts/rules.json": "hermes/scripts/rules.json",
    "hermes/scripts/tests/fixture.json": None,
    "hermes/scripts/assets/banner.png": "hermes/scripts/assets/banner.png",
    "hermes/scripts/tests/test_a.py": None,
    "hermes/skills/workshop/SKILL.md": "hermes/skills/autonomous-ai-agents/workshop/SKILL.md",
    "hermes/skills/workshop/references/r.md": "hermes/skills/autonomous-ai-agents/workshop/references/r.md",
    "hermes/prompts/p.md": "hermes/prompts/p.md",
    "hermes/scripts/memdaily_job.py": "hermes/profiles/agumon/scripts/memdaily_job.py",   # 亚古兽的（#182）
    "hermes/prompts/memdaily_report.md": "hermes/profiles/agumon/prompts/memdaily_report.md",
    "hermes/scripts/sidebar_reports.py": "hermes/profiles/agumon/scripts/sidebar_reports.py",   # 亚古兽的销售侧边栏汇报
    "hermes/skills/sales-sidebar/SKILL.md": "hermes/profiles/agumon/skills/productivity/sales-sidebar/SKILL.md",
    "hermes/skills/sales-sidebar/references/feedback.md":
        "hermes/profiles/agumon/skills/productivity/sales-sidebar/references/feedback.md",
    "ops/health_check.py": "bin/health_check.py",
    "ops/check_repos.py": "bin/check_repos.py",
    "ops/self_check.py": "hermes/scripts/self_check.py",
    "ops/other.py": None,
}


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


class ScriptTest(unittest.TestCase):
    """跑真实入口：python3 self_check.py。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-self-check-test-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.repo = self.home / "repo"
        gitconfig = self.home / "gitconfig"
        gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
        self.env = {**os.environ, "HOME": str(self.home), "DT_REPO": str(self.repo),
                    "HERMES_HOME": str(self.home / "hermes"), "DT_BIN": str(self.home / "bin"),
                    "DT_TODAY": "2000-01-02", "DT_ROSTER": str(self.home / "projects.json"),
                    "TIDINGS_REPO": str(self.home / "tidings"), "TIDINGS_NODE": sys.executable,
                    "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        p = patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.repo.mkdir()
        sh(self.repo, "init", "-q", "-b", "main")
        for src, dst in SOURCES.items():
            self.write(self.repo / src, f"源 {src}\n")
            if dst:
                self.write(self.home / dst, f"源 {src}\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "起步")
        self.write(self.home / "hermes/scripts/check_repos.py", "源 ops/check_repos.py\n")   # check_repos.py 装两处
        self.write(self.home / "hermes/scripts/安装处多出的.py", "不管\n")
        self.write(self.home / "tidings/bin/health.js", "")   # 健康：什么都不打印

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_script(self, *args, **env):
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True,
                           env={**self.env, **env}, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        return r.stdout

    def test_all_consistent_prints_nothing(self):
        self.assertEqual(self.run_script(), "")

    def fake_ai_access(self, body):
        self.write(self.home / "hermes/scripts/ai_access.py", body)   # 安装处多出的文件不算不一致

    def test_ai_access_default_available_prints_nothing(self):
        self.fake_ai_access('import json\nprint(json.dumps({"default_worker": "codex", '
                            '"workers": {"codex": {"name": "Codex", "state": "可用"}}}))\n')
        self.assertEqual(self.run_script(), "")

    def test_ai_access_default_unavailable_is_reported(self):
        self.fake_ai_access('import json\nprint(json.dumps({"default_worker": "codex", "workers": {"codex": '
                            '{"name": "Codex", "state": "未登录", "detail": "codex login status 退出码 1"}}}))\n')
        out = self.run_script()
        self.assertIn("🤖 AI 接入提醒", out)
        self.assertIn("车间默认执行方 Codex 现在未登录（codex login status 退出码 1）", out)
        self.assertIn("不会换成另一家", out)

    def fake_hermes_on(self, provider, claude_state, detail="claude auth status 退出码 1"):
        st = {"default_worker": "codex",
              "workers": {"codex": {"name": "Codex", "state": "可用"},
                          "claude": {"name": "Claude Code", "state": claude_state, "detail": detail}},
              "hermes": {"models": {"model": {"default": "claude-opus-5-5[1m]", "provider": provider}}}}
        self.fake_ai_access(f"print({json.dumps(st, ensure_ascii=False)!r})\n")

    def test_hermes_on_claude_logged_out_is_reported(self):
        """11-12 起 Claude 登录失效，Hermes 回退 deepseek 三天、日报三期降级都没人提醒（#180，G-27）。"""
        self.fake_hermes_on("claude-subscription-directsdk-experimental", "未登录")
        out = self.run_script()
        self.assertIn("🤖 AI 接入提醒", out)
        self.assertIn("Hermes 主模型 claude-opus-5-5[1m] 走本机 Claude，但 Claude Code 现在未登录（claude auth status 退出码 1）", out)
        self.assertIn("回退到 deepseek", out)
        self.assertIn("HTTPS_PROXY=", out)
        self.assertIn("claude auth login", out)
        self.assertIn("Login successful.", out)

    def test_hermes_on_claude_available_or_not_on_claude_prints_nothing(self):
        self.fake_hermes_on("claude-subscription-directsdk-experimental", "可用")
        self.assertEqual(self.run_script(), "")
        self.fake_hermes_on("openai-codex", "未登录")
        self.assertEqual(self.run_script(), "")

    def test_ai_access_broken_config_is_reported(self):
        self.fake_ai_access('import sys\nprint("AI 接入：读不了 AI 接入配置", file=sys.stderr)\nsys.exit(2)\n')
        self.assertIn("AI 接入自检未完成：AI 接入：读不了 AI 接入配置", self.run_script())

    # ---- 托管状态（#131）：装真的 custody.py，状态文件按它的格式手写 ----
    def install_custody(self, doc=None, policy=None):
        scripts = Path(__file__).parents[1]
        for name in ("custody.py", "twin_identity.py", "twin_state.py"):
            self.write(self.home / "hermes/scripts" / name, (scripts / name).read_text(encoding="utf-8"))
        dt_dir = self.home / "Library/Application Support/DigitalTwin"
        if doc is not None:
            self.write(dt_dir / "custody.json", doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False))
        if policy is not None:
            self.write(dt_dir / "custody/policy.md", policy)

    @staticmethod
    def custody_doc(state="off", session=None, signature=None):
        return {"schema": "dt-custody/1", "state": state, "updated_at": None, "session": session, "signature": signature,
                "signed_versions": [signature["version"]] if signature else [], "used_messages": {}, "consumed_windows": {},
                "history": []}

    @staticmethod
    def custody_session(**kw):
        import datetime as dt
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        s = {"id": "c-20000101-233000-0123456789ab", "started_at": now.isoformat(), "until": (now + dt.timedelta(hours=10)).isoformat(),
             "source": {"kind": "feishu", "message_id": "om_fixture0001", "open_id": "ou_secret_fixture"},
             "policy": {"version": 3, "sha256": "0" * 64}, "drain_minutes": 120, "window_def": {"start": "23:30", "end": "08:00"},
             "drain": None, "frozen": None, "events": []}
        s.update(kw)
        return s

    def test_custody_off_or_never_used_prints_nothing(self):
        self.install_custody()
        self.assertEqual(self.run_script(), "")
        self.install_custody(self.custody_doc())
        self.assertEqual(self.run_script(), "")

    def test_custody_on_is_reported(self):
        self.install_custody(self.custody_doc("on", self.custody_session()))
        out = self.run_script()
        self.assertIn("🛡️ 托管", out)
        self.assertIn("托管开着（c-20000101-233000-0123456789ab", out)
        self.assertNotIn("ou_secret_fixture", out)

    def test_custody_frozen_is_reported_with_items(self):
        frozen = {"at": "2026-11-12T09:00:00+08:00", "reason": "排空截止已过，没有收完", "items": ["dt-sandbox#7 对账不清"],
                  "source": "到点（排空截止）"}
        drain = {"started_at": "2026-11-12T07:00:00+08:00", "deadline": "2026-11-12T09:00:00+08:00", "source": "到点（until）"}
        self.install_custody(self.custody_doc("frozen", self.custody_session(drain=drain, frozen=frozen)))
        out = self.run_script()
        self.assertIn("托管已冻结（11-12 09:00：排空截止已过，没有收完）", out)
        self.assertIn("未解决：dt-sandbox#7 对账不清", out)
        self.assertIn("「解除冻结」", out)

    def test_custody_corrupt_file_is_reported(self):
        self.install_custody("{bad")
        self.assertIn("托管状态文件读不了", self.run_script())

    def test_custody_policy_invalid_or_expiring_is_reported(self):
        import datetime as dt
        import hashlib
        policy = ("| 版本 | 3 |\n| 有效期 | 签署日起 30 天 |\n\n## 1. 默认托管时段\n\n- 每晚 23:30 到次日 08:00\n- 排空截止：2 小时\n"
                  "\n## 2. 托管期间允许的动作\n\n| 回复受限会话标为 blocked 的待决事项 | 是 | 每张子单 2 次，每次托管 10 次 |\n"
                  "| 托管合并 | 是 | 每次托管 5 个 |\n")
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        sig = {"version": 3, "sha256": hashlib.sha256(policy.encode()).hexdigest(), "message_id": "om_fixture0002", "open_id": "ou_x",
               "signed_at": (now - dt.timedelta(days=28)).isoformat(), "expires_at": (now + dt.timedelta(days=2)).isoformat(),
               "valid_days": 30}
        self.install_custody(self.custody_doc(signature=sig), policy)
        self.assertIn("托管策略 v3 将于", self.run_script())
        self.install_custody(self.custody_doc(signature={**sig, "signed_at": (now - dt.timedelta(days=20)).isoformat(),
                                                         "expires_at": (now + dt.timedelta(days=10)).isoformat()}))
        self.assertEqual(self.run_script(), "")
        self.install_custody(policy=policy + "改了一个字")
        self.assertIn("托管策略失效：策略文件内容和签署 v3 时不一样", self.run_script())
        revoked = {**sig, "signed_at": (now - dt.timedelta(days=20)).isoformat(),
                   "expires_at": (now + dt.timedelta(days=10)).isoformat(),
                   "revoked": {"at": now.isoformat(), "source": "feishu", "reason": "先停用"}}
        self.install_custody(self.custody_doc(signature=revoked), policy)   # 撤销也要报（第 2 轮审查 P2-05）
        self.assertIn("托管策略失效：托管策略 v3 已于", self.run_script())

    def test_missing_installed_files_listed_by_path(self):
        for dst in ("hermes/skills/autonomous-ai-agents/workshop/references/r.md", "bin/health_check.py",
                    "hermes/scripts/self_check.py"):
            (self.home / dst).unlink()
        out = self.run_script()
        self.assertEqual(out.splitlines()[:3], ["🔧 工程自检", "", "🔁 安装副本不一致：3 项和仓库 main 对不上"])
        self.assertIn("- ~/bin/health_check.py 缺失（源：ops/health_check.py）", out)
        self.assertIn("- ~/hermes/skills/autonomous-ai-agents/workshop/references/r.md 缺失"
                      "（源：hermes/skills/workshop/references/r.md）", out)
        self.assertIn("- ~/hermes/scripts/self_check.py 缺失（源：ops/self_check.py）", out)
        self.assertNotIn("多出", out)

    def test_script_assets_checked(self):
        (self.home / "hermes/scripts/assets/banner.png").unlink()
        self.assertIn("- ~/hermes/scripts/assets/banner.png 缺失（源：hermes/scripts/assets/banner.png）", self.run_script())

    def test_script_rules_json_checked(self):
        self.write(self.home / "hermes/scripts/rules.json", "装的是旧规则\n")
        out = self.run_script()
        self.assertIn("- ~/hermes/scripts/rules.json 和 hermes/scripts/rules.json 内容不同", out)
        self.assertNotIn("fixture.json", out)                     # tests/ 下的 json 不安装、不比对
        (self.home / "hermes/scripts/rules.json").unlink()
        self.assertIn("- ~/hermes/scripts/rules.json 缺失（源：hermes/scripts/rules.json）", self.run_script())

    def test_memdaily_files_checked_in_agumon_profile(self):
        """记忆日报是亚古兽的，装在 profiles/agumon 下；不在猛鬼兽目录下报缺失（#182）。"""
        self.assertEqual(self.run_script(), "")
        self.write(self.home / "hermes/profiles/agumon/scripts/memdaily_job.py", "旧的\n")
        out = self.run_script()
        self.assertIn("- ~/hermes/profiles/agumon/scripts/memdaily_job.py 和 hermes/scripts/memdaily_job.py 内容不同", out)
        self.assertNotIn("~/hermes/scripts/memdaily", out)

    def test_check_repos_checked_in_both_install_places(self):
        (self.home / "hermes/scripts/check_repos.py").unlink()
        self.assertIn("- ~/hermes/scripts/check_repos.py 缺失（源：ops/check_repos.py）", self.run_script())

    def test_changed_content_reports_path_not_content(self):
        self.write(self.home / "hermes/prompts/p.md", "改过的秘密内容\n")
        self.write(self.home / "hermes/scripts/a.py", "源 hermes/scripts/a.py\n\n")
        out = self.run_script()
        self.assertIn("- ~/hermes/prompts/p.md 和 hermes/prompts/p.md 内容不同", out)
        self.assertIn("- ~/hermes/scripts/a.py 和 hermes/scripts/a.py 内容不同", out)
        self.assertNotIn("秘密", out)

    def test_install_hold_skips_missing_but_not_stale(self):
        """#236：main 上 hermes/install_hold.txt 列着的「按计划先不装」，没装不报；装了但和 main 不同照报；没提交的清单不算。"""
        (self.home / "hermes/scripts/a.py").unlink()
        self.write(self.home / "hermes/prompts/p.md", "旧的\n")
        self.write(self.repo / "hermes/install_hold.txt", "# 说明\nhermes/scripts/a.py  # 等批 0E\n")
        self.assertIn("a.py 缺失", self.run_script())                      # 清单还没提交到 main：照报
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-qm", "清单")
        out = self.run_script()
        self.assertNotIn("a.py", out)
        self.assertIn("- ~/hermes/prompts/p.md 和 hermes/prompts/p.md 内容不同", out)
        self.write(self.repo / "hermes/install_hold.txt", "hermes/prompts/p.md\n")
        sh(self.repo, "commit", "-qam", "清单 2")
        self.assertIn("p.md 和 hermes/prompts/p.md 内容不同", self.run_script())   # 装了但不同：照报

    def test_uncommitted_repo_changes_are_not_drift(self):
        self.write(self.repo / "hermes/scripts/a.py", "工作区改了没提交\n")
        self.write(self.repo / "hermes/prompts/新提示词.md", "没跟踪\n")
        sh(self.repo, "checkout", "-q", "-b", "dev")
        self.write(self.repo / "hermes/skills/workshop/SKILL.md", "别的分支提交了\n")
        sh(self.repo, "commit", "-qam", "dev 上的改动")
        self.assertEqual(self.run_script(), "")

    def test_tidings_problem_printed_as_is(self):
        self.write(self.home / "tidings/bin/health.js",
                   "import pathlib\nprint('🌟 Github·Tidings 自检\\n- 在 ' + pathlib.Path.cwd().name + ' 下跑的')\n")
        self.assertEqual(self.run_script(), "🔧 工程自检\n\n🌟 Github·Tidings 自检\n- 在 tidings 下跑的\n")

    def test_tidings_not_runnable_is_one_line(self):
        out = self.run_script(TIDINGS_NODE=str(self.home / "没有这个node"))
        self.assertEqual(out, "🔧 工程自检\n\nTidings 自检未完成（无法启动：No such file or directory）\n")

    def test_unreadable_repo_is_one_issue(self):
        for repo in (self.home / "没有这个仓库", self.home / "hermes"):
            with self.subTest(repo=repo):
                out = self.run_script(DT_REPO=str(repo))
                lines = out.splitlines()
                self.assertEqual(lines[2], "🔁 安装副本不一致：1 项和仓库 main 对不上")
                self.assertTrue(lines[3].startswith(f"- 取不到仓库 main（~/{repo.name}："), out)

    def test_other_days_skip_monthly(self):
        self.write(self.home / "Sandbox/旧目录/x", "")
        os.utime(self.home / "Sandbox/旧目录", (0, 0))
        self.assertEqual(self.run_script(), "")

    def test_monthly_end_to_end_with_real_check_repos(self):
        self.write(self.home / "Sandbox/旧目录/x", "")
        os.utime(self.home / "Sandbox/旧目录", (0, 0))
        self.write(self.home / "projects.json", json.dumps({"projects": []}))
        for args, env in ((["--monthly"], {}), ([], {"DT_TODAY": "2000-02-01"})):
            with self.subTest(args=args, env=env):
                out = self.run_script(*args, **env)
                self.assertEqual(out.splitlines(), [
                    "🔧 工程自检", "",
                    "🧹 沙盒提醒（每月 1 号）：~/Sandbox 和 ~/Documents/Codex 里有 1 项超过 30 天没动",
                    "- ~/Sandbox/旧目录",
                    "只是提醒，不会自动删；要清理就让 Claude 或 Codex 列清单给你定。"])

    def test_monthly_reports_broken_roster(self):
        out = self.run_script("--monthly")
        self.assertIn("📦 仓库自检提醒（每月 1 号）\n- 读不了名册（", out)


class SandboxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-sandbox-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sandbox, self.codex = self.root / "Sandbox", self.root / "Codex"
        p = patch.object(sc, "SANDBOX_DIRS", [self.sandbox, self.codex])
        p.start()
        self.addCleanup(p.stop)

    def make(self, path, old):
        path.mkdir(parents=True)
        if old:
            os.utime(path, (0, 0))

    def test_only_old_visible_top_level_entries(self):
        self.make(self.sandbox / "旧", True)
        self.make(self.sandbox / "新", False)
        self.make(self.sandbox / ".隐藏", True)
        self.assertEqual(sc.check_sandbox(), ([self.sandbox / "旧"], []))

    @unittest.skipIf(os.geteuid() == 0, "root 不受目录权限限制")
    def test_unscannable_dir_is_an_issue_not_a_crash(self):
        self.make(self.sandbox, False)
        self.make(self.codex / "旧", True)
        self.sandbox.chmod(0)
        self.addCleanup(self.sandbox.chmod, 0o755)
        stale, errors = sc.check_sandbox()
        self.assertEqual(stale, [self.codex / "旧"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith(f"扫不了 {self.sandbox}（"), errors)


class MonthlyGateTest(unittest.TestCase):
    """main() 的日期判断和输出拼装；三项检查都打桩。"""

    def setUp(self):
        self.drift = self.patch("check_drift", return_value=[])
        self.sandbox = self.patch("check_sandbox", return_value=([], []))
        self.repos = self.patch("check_repos", return_value=[])
        self.tidings = self.patch("check_tidings", return_value="")

    def patch(self, name, **kwargs):
        p = patch.object(sc, name, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def main(self, day="2000-01-02", *args):
        output = io.StringIO()
        with patch.dict(os.environ, {"DT_TODAY": day}), \
                patch.object(sys, "argv", [str(SCRIPT), *args]), redirect_stdout(output):
            sc.main()
        return output.getvalue()

    def test_other_days_only_check_drift(self):
        self.assertEqual(self.main(), "")
        self.drift.assert_called_once_with()
        self.tidings.assert_called_once_with()
        self.sandbox.assert_not_called()
        self.repos.assert_not_called()

    def test_first_of_month_or_flag_runs_both_monthly_checks(self):
        for day, args in (("2000-01-01", ()), ("2000-12-01", ()), ("2000-01-15", ("--monthly",))):
            with self.subTest(day=day, args=args):
                self.sandbox.reset_mock()
                self.repos.reset_mock()
                self.assertEqual(self.main(day, *args), "")
                self.sandbox.assert_called_once_with()
                self.repos.assert_called_once_with()

    def test_sections_and_truncation(self):
        self.drift.return_value = [f"漂移{i}" for i in range(12)]
        self.sandbox.return_value = ([], ["扫不了 ~/Documents/Codex（Operation not permitted）"])
        self.repos.return_value = ["a：不是私有仓库"]
        self.tidings.return_value = "🌟 Github·Tidings 自检\n- 虚构问题"
        out = self.main("2000-01-01")
        self.assertEqual(out.split("\n\n"), [
            "🔧 工程自检",
            "\n".join(["🔁 安装副本不一致：12 项和仓库 main 对不上", *[f"- 漂移{i}" for i in range(10)],
                       "- ……另有 2 项", "处理：从仓库 main 复制到安装位置；技能、提示词改了要重启网关。"]),
            "🌟 Github·Tidings 自检\n- 虚构问题",
            "🧹 沙盒提醒（每月 1 号）\n- 扫不了 ~/Documents/Codex（Operation not permitted）",
            "📦 仓库自检提醒（每月 1 号）\n- a：不是私有仓库\n",
        ])

    def test_sandbox_stale_keeps_original_wording_and_limit(self):
        self.sandbox.return_value = ([Path(f"/虚构沙盒/目录{i:02}") for i in range(11)], [])
        out = self.main("2000-01-01")
        self.assertIn("🧹 沙盒提醒（每月 1 号）：~/Sandbox 和 ~/Documents/Codex 里有 11 项超过 30 天没动\n"
                      "- /虚构沙盒/目录00\n", out)
        self.assertIn("- /虚构沙盒/目录09\n- ……另有 1 项\n只是提醒，不会自动删；要清理就让 Claude 或 Codex 列清单给你定。", out)
        self.assertNotIn("目录10", out)


class TidingsTest(unittest.TestCase):
    """check_tidings() 的调用方式和失败分支；子进程打桩。"""

    def setUp(self):
        self.outcome = subprocess.CompletedProcess([], 0, "", "")
        p = patch.object(sc.subprocess, "run", side_effect=self.fake_process)
        self.process = p.start()
        self.addCleanup(p.stop)

    def fake_process(self, cmd, **kwargs):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def test_runs_health_js_in_repo_with_absolute_node(self):
        self.assertEqual(sc.check_tidings(), "")
        call = self.process.call_args
        self.assertEqual(call.args[0], [sc.TIDINGS_NODE, "bin/health.js"])
        self.assertTrue(Path(sc.TIDINGS_NODE).is_absolute())
        self.assertEqual(call.kwargs["cwd"], sc.TIDINGS_REPO)
        self.assertEqual(call.kwargs["timeout"], 120)
        self.assertEqual(call.kwargs["env"]["PATH"], sc.PATH)

    def test_output_returned_as_is(self):
        self.outcome = subprocess.CompletedProcess([], 0, "🌟 Github·Tidings 自检\n- 问题一\n", "")
        self.assertEqual(sc.check_tidings(), "🌟 Github·Tidings 自检\n- 问题一")

    def test_failures_are_one_line_without_stderr(self):
        cases = [(subprocess.CompletedProcess([], 1, "半截输出", "fake traceback"), "Tidings 自检未完成（退出码 1）"),
                 (subprocess.TimeoutExpired("fake", 120), "Tidings 自检未完成（超过 120 秒）"),
                 (FileNotFoundError(2, "No such file or directory"), "Tidings 自检未完成（无法启动：No such file or directory）")]
        for outcome, expected in cases:
            with self.subTest(outcome=outcome):
                self.outcome = outcome
                self.assertEqual(sc.check_tidings(), expected)


class RepoCheckTest(unittest.TestCase):
    """从 test_health_check.py 迁来：check_repos() 解析同目录 check_repos.py 的末行 JSON。"""

    def setUp(self):
        self.failed = ["a：不是私有仓库", "b：主目录检出的是 dev，不是 main"]
        self.warnings = ["a：名册没写 test，合并前不跑测试"]
        self.result(self.failed, self.warnings)
        self.repo_error = None
        p = patch.object(sc.subprocess, "run", side_effect=self.fake_process)
        self.process = p.start()
        self.addCleanup(p.stop)

    def result(self, failed, warnings):
        summary = {"ok": not failed, "failed": failed, "warnings": warnings}
        self.repo_result = subprocess.CompletedProcess(
            [], int(bool(failed)),
            "【a】虚构项目\n  通过  有 PR 模板\n  提示  不应发送的说明\n\n"
            + json.dumps(summary, ensure_ascii=False) + "\n\n", "")

    def fake_process(self, cmd, **kwargs):
        if cmd == [sys.executable, "-B", str(sc.CHECK_REPOS_SCRIPT)]:
            if self.repo_error:
                raise self.repo_error
            return self.repo_result
        raise AssertionError(f"不允许执行外部命令：{cmd}")

    def test_only_failures_returned(self):
        self.assertEqual(sc.check_repos(), self.failed)

    def test_all_pass_or_warnings_only_return_nothing(self):
        for warnings in ([], self.warnings):
            with self.subTest(warnings=warnings):
                self.result([], warnings)
                self.assertEqual(sc.check_repos(), [])

    def test_child_uses_sibling_script_same_python_and_longer_timeout(self):
        with patch.dict(sc.os.environ, {"DT_ROSTER": "/虚构名册/projects.json"}):
            self.assertEqual(sc.check_repos(), self.failed)
        call = self.process.call_args
        self.assertEqual(call.args[0], [sys.executable, "-B", str(SCRIPT.with_name("check_repos.py"))])
        self.assertEqual(call.kwargs["timeout"], sc.CHECK_REPOS_TIMEOUT)
        self.assertGreater(call.kwargs["timeout"], 60)
        self.assertEqual(call.kwargs["env"]["DT_ROSTER"], "/虚构名册/projects.json")
        self.assertEqual(call.kwargs["env"]["PATH"], sc.PATH)

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
                self.repo_result = subprocess.CompletedProcess([], code, output, "fake traceback")
                issues = sc.check_repos()
                self.assertEqual(len(issues), 1)
                self.assertIn("仓库自检未完成", issues[0])
                self.assertNotIn("fake traceback", issues[0])

    def test_unexpected_exit_code_is_not_success_even_with_valid_json(self):
        self.result([], [])
        self.repo_result.returncode = 2  # Python 找不到脚本等执行失败
        self.assertEqual(sc.check_repos(), ["仓库自检未完成（退出码 2）"])

    def test_timeout_and_launch_error_are_reported(self):
        for error, detail in ((subprocess.TimeoutExpired("fake", sc.CHECK_REPOS_TIMEOUT), "超过"),
                              (FileNotFoundError("fake python"), "无法启动")):
            with self.subTest(error=error):
                self.repo_error = error
                issues = sc.check_repos()
                self.assertEqual(len(issues), 1)
                self.assertIn("仓库自检未完成", issues[0])
                self.assertIn(detail, issues[0])


if __name__ == "__main__":
    unittest.main()
