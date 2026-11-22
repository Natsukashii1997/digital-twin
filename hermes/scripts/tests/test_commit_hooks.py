"""提交检查 scripts/hooks/commit-msg（复盘 N2：把 P4 的提交检查装进本仓库，常浩 2026-11-20 批）：修复类提交的正文没写「同类：」一行就拒绝提交。

钩子照抄 my-skills 骨架 project-starter（commit-msg 自 0.5.0 起未变，2026-11-22 核对到 0.5.2），规则的完整用例在骨架的 tests/hooks.test.mjs；这里只守本仓库这一份：
- 规则被改松（返修、回滚这类不在开头的漏拦），或被改严（CURRENT、交接、写明 feat/docs 的也拦）；
- 钩子没接上：node 找不到、包装脚本路径错、装上后不拦。
CI 机器没有 node 时这里失败、不跳过（钩子靠 node 跑，没有 node 就等于没装）。
本仓库只装 commit-msg、不装 pre-push：全量测试要好几分钟，靠 CI 和合并前跑（常浩 2026-11-20 回分批计划「其他按推荐」，见 docs/DECIDED.md 同日条目）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOKS = ROOT / "scripts" / "hooks"

# 被拒的样本：标题来自骨架用例里的真实提交（含本仓库的「#172 返修」）
FIXES = [
    "fix: 截断",
    "修：页面时间少了 8 小时",
    "#172 返修：backend 所在默认网络断宿主和外网、关 VCS 集成",
    "安全审计 11-18：修复通道越权、确认误判、日志泄露和并发配置写入",
    "回滚 #88：撤掉会丢草稿的自动保存",
    "hotfix: 登录页白屏",
    "第 3 轮返工：卡片按钮错位",
    "热修 日报漏发",
    "合并官：修正一致率算法",
    "bugfix: 时区",
]
# 不查的样本
NOT_FIXES = [
    "feat: 加导出",
    "修改 README",
    "docs(dt-orc): V4.0 按第 1 轮审查返修（#60）",
    "CURRENT：0B-4a 第 2 轮返修完成，第 3 轮待常浩授权",
    "交接：#348 第 4 轮返修",
    "Merge branch 'main' into claude/x",
    'Revert "fix: x"',
    "修订 PRD 第 3 节",
    "fixup! 第 2 轮返修：截断",
    "squash! 回滚 #88",
]


def clean_env():
    """不带本仓库的 GIT_DIR 等：测试若在 git 钩子里被跑到，临时仓库的 git 操作不会落到本仓库上（全局坑 G-47）。"""
    local = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True).stdout.split()
    env = {k: v for k, v in os.environ.items() if k not in local}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return env


class CommitMsgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="commit-hooks-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def check(self, message):
        f = Path(self.tmp) / "COMMIT_EDITMSG"
        f.write_text(message, encoding="utf-8")
        return subprocess.run(["node", str(HOOKS / "commit-msg.mjs"), str(f)], capture_output=True, text=True)

    def test_fix_without_same_kind_line_is_rejected(self):
        for subject in FIXES:
            with self.subTest(subject):
                r = self.check(subject + "\n")
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("缺一行「同类：", r.stderr)

    def test_fix_with_same_kind_line_passes(self):
        for subject in FIXES:
            with self.subTest(subject):
                r = self.check(subject + "\n\n同类：搜了同一写法，命中 0 处\n")
                self.assertEqual(r.returncode, 0, r.stderr)
        r = self.check("修复：x\n\n同类：\n")
        self.assertEqual(r.returncode, 1, "同类后面没写内容也不算")

    def test_other_commits_are_not_checked(self):
        for subject in NOT_FIXES:
            with self.subTest(subject):
                r = self.check(subject + "\n")
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_installed_hook_rejects_then_accepts_a_real_commit(self):
        repo = Path(self.tmp) / "repo"
        env = clean_env()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
        shutil.copytree(HOOKS, repo / "scripts" / "hooks")
        git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=env)
        git("config", "core.hooksPath", "scripts/hooks")
        (repo / "a.txt").write_text("1", encoding="utf-8")
        git("add", "-A")
        bad = git("commit", "-q", "-m", "#172 返修：x")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("缺一行「同类：", bad.stderr)
        good = git("commit", "-q", "-m", "#172 返修：x", "-m", "同类：搜了 x，命中 1 处")
        self.assertEqual(good.returncode, 0, good.stderr)


if __name__ == "__main__":
    unittest.main()
