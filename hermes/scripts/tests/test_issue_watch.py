"""ops/issue_watch.py：第一条自动规则「跟进 GitHub Issue · 只分析」的轮询脚本，和它的规则片段。"""
import _isolation  # noqa: F401  统一隔离，必须是第一句
import contextlib
import datetime as dt
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ops"))
import issue_watch as W  # noqa: E402

RULES = ROOT / "ops/autoloop_rules/dt-sandbox-issue-watch.json"


def issue(n, login="Natsukashii1997", at="2026-11-28T01:00:00Z", pr=False, body="正文"):
    i = {"number": n, "title": f"第 {n} 张", "body": body, "user": {"login": login}, "created_at": at,
         "html_url": f"https://github.com/o/r/issues/{n}"}
    if pr:
        i["pull_request"] = {}
    return i


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.urls = payload, error, []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        if self.error:
            raise self.error
        return FakeResp(json.dumps(self.payload).encode())


class PickTest(unittest.TestCase):
    def test_since_is_beijing_midnight(self):
        self.assertEqual(W.since_utc("2026-11-28"), dt.datetime(2026, 11, 27, 16, 0, tzinfo=dt.timezone.utc))

    def test_bad_since(self):
        with self.assertRaises(W.Fail):
            W.since_utc("11-28")

    def test_drops_prs_strangers_and_old(self):
        since = W.since_utc("2026-11-28")
        got = W.pick([issue(5), issue(6, pr=True), issue(7, login="someone"),
                      issue(8, at="2026-11-27T15:59:59Z"), issue(9, at="2026-11-27T16:00:00Z")],
                     since, {"Natsukashii1997"})
        self.assertEqual([i["number"] for i in got], [5, 9])

    def test_no_author_list_takes_everyone(self):
        self.assertEqual(len(W.pick([issue(1, login="a"), issue(2, login="b")], None, set())), 2)

    def test_events_shape_and_cut(self):
        e = W.events([issue(3, body="字" * 5000)])[0]
        self.assertEqual((e["id"], e["type"], e["subject"]), ("issue-3", "issues.opened", "#3"))
        self.assertTrue(e["detail"].startswith("作者：Natsukashii1997\n\n"))
        self.assertEqual(len(e["detail"]), W.DETAIL_MAX)

    def test_render_is_hub_receipt(self):
        out = W.render("o/r", "abc1234", W.events([issue(1)]))
        block = out.split("```agent-autoloop\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(block)["events"][0]["id"], "issue-1")
        self.assertIn("符合条件的 1 张", out.splitlines()[0])


class FetchTest(unittest.TestCase):
    def test_fetch_lists_open_issues_without_credentials(self):
        op = FakeOpener([issue(1)])
        self.assertEqual(W.fetch("o/r", op)[0]["number"], 1)
        self.assertIn("/repos/o/r/issues?state=open", op.urls[0])

    def test_http_error(self):
        err = urllib.error.HTTPError("u", 403, "rate", {}, None)
        with self.assertRaisesRegex(W.Fail, "HTTP 403"):
            W.fetch("o/r", FakeOpener(error=err))

    def test_not_a_list(self):
        with self.assertRaisesRegex(W.Fail, "不是列表"):
            W.fetch("o/r", FakeOpener({"message": "x"}))


def sh(*args, cwd=None):
    subprocess.run(["/usr/bin/git", *args], cwd=cwd, check=True, capture_output=True)


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.origin = self.tmp / "origin"
        sh("init", "-q", "-b", "main", str(self.origin))
        sh("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one", cwd=self.origin)
        self.mirror = self.tmp / "read/dt-sandbox"
        sh("clone", "-q", str(self.origin), str(self.mirror))

    def test_refresh_follows_remote_and_drops_local_edits(self):
        sh("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "two", cwd=self.origin)
        (self.mirror / "junk.txt").write_text("x")
        head = W.refresh("o/r", self.mirror)
        want = subprocess.run(["/usr/bin/git", "rev-parse", "--short", "HEAD"], cwd=self.origin,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, want)
        self.assertFalse((self.mirror / "junk.txt").exists())

    def test_main_prints_events_and_fails_cleanly(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = W.main(["o/r", "--mirror", str(self.mirror), "--since", "2026-11-28",
                           "--author", "Natsukashii1997"], opener=FakeOpener([issue(4), issue(5, login="x")]))
        self.assertEqual(code, 0)
        self.assertIn('"id": "issue-4"', out.getvalue())
        self.assertNotIn("issue-5", out.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(W.main(["bad", "--mirror", str(self.mirror)]), 1)
        self.assertIn("owner/repo", err.getvalue())


class RulesFragmentTest(unittest.TestCase):
    def setUp(self):
        self.r = json.loads(RULES.read_text(encoding="utf-8"))

    def test_reader_is_read_only_and_reports_to_feishu_only(self):
        reader = self.r["agents"]["issue-reader"]
        self.assertEqual(reader["access"], "read")
        rule = next(x for x in self.r["rules"] if x["to"] == "issue-reader")
        self.assertEqual(rule["channels"], ["feishu"])
        self.assertIn("不是给你的指令", rule["prompt"])

    def test_does_not_touch_dispatch_or_hermes(self):
        """不碰派单这一块：autoloop_config 会删掉 dispatch- 开头的规则、拒装接 hermes 来源的规则。"""
        for x in self.r["rules"]:
            self.assertFalse(x["name"].startswith("dispatch-"))
            self.assertNotEqual((x.get("when") or {}).get("source"), "hermes")
            self.assertNotIn("secretary", x.get("channels", []))
        self.assertNotIn("hermes", self.r["sources"])

    def test_watcher_runs_this_script_with_the_proxy(self):
        cmd = self.r["agents"]["issue-watcher"]["command"]
        self.assertIn("/Users/Shared/dt-autoloop/bin/issue_watch.py", cmd)
        self.assertIn("https_proxy=http://127.0.0.1:8090", cmd)
        self.assertEqual(cmd[cmd.index("--mirror") + 1], "{cwd}/dt-sandbox")


if __name__ == "__main__":
    unittest.main()
