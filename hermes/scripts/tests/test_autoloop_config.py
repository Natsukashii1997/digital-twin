"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

autoloop_config.py（中台配置，#195 批 0B-2）的生成规则和 autoloop_readonly.py（只读窗口）的白名单、只读接口。
不连中台、不碰 ~/.agent-autoloop；真中台的端到端在 test_autoloop_hub_e2e.py。

中台目录、钥匙文件、备份目录、锁、令牌、网络由统一测试隔离（_isolation，#212）在导入前指到临时目录、默认禁网；
本模块另把名册换成空名册、命令行换成一定失败的 false、中台请求换成抛 Rejected：哪个测试或「故意改坏」漏过了某道闸，
也碰不到本机正式中台（2026-11-17 一次反向验证漏了隔离，误装到正式中台，已恢复）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_readonly as R  # noqa: E402
import prod_guard  # noqa: E402

_ISOLATION = []


def setUpModule():
    prod_guard.setUpModule()                     # 批 0D：整体指到临时目录，前后核对正式环境
    tmp = tempfile.TemporaryDirectory(prefix="dt-config-test-")
    _ISOLATION.append(tmp)
    root = Path(tmp.name)
    roster = root / "projects.json"
    roster.write_text(json.dumps({"projects": [], "inventory": []}), encoding="utf-8")

    def no_network(*a, **kw):
        raise C.Rejected("单测不连中台")
    for p in (mock.patch.dict(os.environ, {"DT_AUTOLOOP_CLI": "/usr/bin/false", "DT_OWNER_OPEN_ID": OWNER}),
              mock.patch.object(G, "ROSTER", roster), mock.patch.object(C, "hub", side_effect=no_network)):
        p.start()
        _ISOLATION.append(p)


def tearDownModule():
    while _ISOLATION:
        x = _ISOLATION.pop()
        (x.cleanup if isinstance(x, tempfile.TemporaryDirectory) else x.stop)()
    prod_guard.tearDownModule()

OWNER = "ou_fixture_owner_0001"
CURRENT = {
    "sources": {"local": {"name": "本机"}},
    "agents": {"claude": {"name": "Claude Code", "worker": "mac", "use": "claude"}},
    "channels": {"desktop": {"name": "桌面通知"}},
    "rules": [{"name": "试一下", "when": {"source": "local", "types": ["try.*"]}, "to": "claude"},
              {"name": "dispatch-old-claude", "when": {"source": "hermes", "types": ["x"]}, "to": "dt-claude"}],
    "elsewhere": [],
}
REPOS = {"dt-sandbox": "o/dt-sandbox"}


class Render(unittest.TestCase):
    def setUp(self):
        self.new = C.render(CURRENT, OWNER, REPOS)

    def test_keeps_everything_else(self):
        self.assertEqual(self.new["sources"]["local"], CURRENT["sources"]["local"])
        self.assertEqual(self.new["agents"]["claude"], CURRENT["agents"]["claude"])
        self.assertEqual(self.new["channels"]["desktop"], CURRENT["channels"]["desktop"])
        self.assertIn("试一下", [r["name"] for r in self.new["rules"]])
        self.assertEqual(self.new["elsewhere"], [])
        self.assertEqual(CURRENT["rules"][1]["name"], "dispatch-old-claude")       # 不改传进来的原件

    def test_source_agents_and_channels(self):
        self.assertIn("hermes", self.new["sources"])
        for ex, (aid, _) in C.AGENTS.items():
            self.assertEqual((self.new["agents"][aid]["worker"], self.new["agents"][aid]["use"]), ("dt-worker", ex))
            self.assertEqual(self.new["agents"][aid]["program"], f"/Users/Shared/dt-autoloop/bin/dt-worktree-{ex}")  # 0C 启动脚本
        self.assertEqual(self.new["channels"]["feishu"]["answerers"], [OWNER])        # 飞书只认常浩
        self.assertEqual(self.new["channels"]["secretary"]["answerers"], ["秘书"])     # 秘书通道只认秘书
        self.assertEqual(self.new["defaultChannels"], ["desktop"])                    # 别处的卡片照旧只发原有通道

    def test_dispatch_rules(self):
        rules = {r["name"]: r for r in self.new["rules"]}
        self.assertNotIn("dispatch-old-claude", rules)                               # 名册里没有的旧派单规则去掉
        self.assertEqual(sorted(C.dispatch_names(self.new)), ["dispatch-dt-sandbox-claude", "dispatch-dt-sandbox-codex"])
        for ex in ("claude", "codex"):
            r = rules[G.rule_name("dt-sandbox", ex)]
            self.assertEqual(r["when"], {"source": "hermes", "types": [G.event_type("dt-sandbox", ex)]})
            self.assertEqual(r["to"], C.AGENTS[ex][0])
            self.assertNotIn("emits", r)                                              # 工人回执发不出事件
            self.assertEqual(r["channels"], ["feishu", "secretary"])
            self.assertIn("parallel", r)                                              # 按任务单号分事项

    def test_gateway_event_matches_rule(self):
        row = {"task_id": "t-20261117-abcdef", "repo": "dt-sandbox", "executor": "codex"}
        body = G.event_body({"event_key": "k", "kind": "start", "generation": 1}, row)
        r = {x["name"]: x for x in self.new["rules"]}[G.rule_name("dt-sandbox", "codex")]
        self.assertIn(body["type"], r["when"]["types"])

    def test_idempotent(self):
        self.assertEqual(C.render(self.new, OWNER, REPOS), self.new)

    def test_secretary_never_a_default_channel(self):
        cur = copy.deepcopy(CURRENT) | {"defaultChannels": ["desktop", "secretary"]}
        self.assertEqual(C.render(cur, OWNER, REPOS)["defaultChannels"], ["desktop"])

    def test_other_rules_cannot_route_to_secretary(self):
        cur = copy.deepcopy(CURRENT)
        cur["rules"][0]["channels"] = ["desktop", "secretary"]
        with self.assertRaisesRegex(C.Rejected, "试一下"):
            C.render(cur, OWNER, REPOS)

    def test_owner_must_be_configured(self):
        with self.assertRaises(C.Rejected):
            C.render(CURRENT, "", REPOS)

    def test_only_sandbox_repos(self):
        with tempfile.TemporaryDirectory() as d:
            roster = Path(d) / "projects.json"
            roster.write_text(json.dumps({
                "projects": [{"id": "digital-twin", "github": "o/digital-twin", "category": "活跃", "write_allowed": True}],
                "inventory": [{"path": "github:o/dt-sandbox", "sandbox": True}, {"path": "github:o/old"}]}), encoding="utf-8")
            with mock.patch.object(G, "ROSTER", roster):
                self.assertEqual(C.sandbox_repos(), {"dt-sandbox": "o/dt-sandbox"})


STATE = {
    "now": "2026-11-17T10:00:00+08:00",
    "cards": [{"id": 5, "version": 1, "ref": "om_secret", "kind": "ask", "state": "open", "title": "选哪个", "text": "A 还是 B",
               "options": ["A", "B"], "recommend": 0, "choice": None, "answer": "常浩写的话", "by": None, "result": "结案说明",
               "link": "http://127.0.0.1:3350/c/CAPABILITY", "rule": "dispatch-dt-sandbox-claude", "lane": "t-20261117-abcdef",
               "about": "留给下一轮", "createdAt": "x"}],
    "recentCards": [],
    "runs": [{"id": 9, "rule": "dispatch-dt-sandbox-claude", "agent": "dt-claude", "state": "running", "session": "sess-1",
              "usage": {"tokens": 1}, "summary": "回执摘要", "link": "https://x", "error": "",
              "events": [{"source": "hermes", "type": "dispatch.dt-sandbox.claude.write", "subject": "t-20261117-abcdef",
                          "summary": "s", "link": "l"}]}],
    "lines": [{"id": "dispatch-dt-sandbox-claude", "name": "dispatch-dt-sandbox-claude", "source": "hermes", "types": ["t"],
               "agent": "dt-claude", "paused": {"by": "owner", "reason": "r"}, "totals": {}}],
    "channels": [{"id": "secretary", "name": "秘书", "online": False, "lastFailure": {"error": "e"}}],
    "agents": [{"id": "dt-claude", "program": "/secret/path"}],
    "spending": {"today": {}},
}


class Readonly(unittest.TestCase):
    def test_whitelist(self):
        snap = R.snapshot(STATE)
        text = json.dumps(snap, ensure_ascii=False)
        for secret in ("om_secret", "CAPABILITY", "常浩写的话", "结案说明", "留给下一轮", "sess-1", "回执摘要", "/secret/path"):
            self.assertNotIn(secret, text)
        card = snap["cards"][0]
        self.assertEqual((card["lane"], card["rule"], card["options"], card["state"]),
                         ("t-20261117-abcdef", "dispatch-dt-sandbox-claude", ["A", "B"], "open"))
        self.assertEqual(snap["runs"][0]["events"], [{"source": "hermes", "type": "dispatch.dt-sandbox.claude.write",
                                                      "subject": "t-20261117-abcdef"}])
        self.assertIs(snap["lines"][0]["paused"], True)
        self.assertEqual(set(snap), {"now", "cards", "recentCards", "runs", "lines", "channels"})

    def test_unknown_upstream_fields_stay_out(self):
        s = copy.deepcopy(STATE)
        s["cards"][0]["newSecretField"] = "上游新加的"
        self.assertNotIn("上游新加的", json.dumps(R.snapshot(s), ensure_ascii=False))


class ReadonlyServer(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tf = Path(d.name) / "sub/readonly.token"
        p = mock.patch.dict(os.environ, {"DT_READONLY_TOKEN_FILE": str(tf)})
        p.start()
        self.addCleanup(p.stop)
        self.srv = R.serve(0, fetch=lambda: R.snapshot(STATE))
        self.addCleanup(self.srv.server_close)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.token = tf.read_text().strip()
        self.assertEqual(tf.stat().st_mode & 0o777, 0o600)

    def call(self, path, method="GET", token=None):
        req = urllib.request.Request(self.base + path, method=method, data=b"{}" if method != "GET" else None,
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_needs_token_and_is_read_only(self):
        self.assertEqual(self.call(R.PATH)[0], 401)
        self.assertEqual(self.call(R.PATH, token="wrong")[0], 401)
        code, body = self.call(R.PATH, token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(body["cards"][0]["id"], 5)
        self.assertNotIn("CAPABILITY", json.dumps(body))
        for path, method in (("/v1/cards/5/answer", "POST"), (R.PATH, "POST"), ("/v1/state", "GET"), (R.PATH, "DELETE")):
            with self.subTest(path=path, method=method):
                self.assertIn(self.call(path, method, token=self.token)[0], (404, 405))

    def test_bad_token_file_refused(self):
        tf = Path(os.environ["DT_READONLY_TOKEN_FILE"])
        for bad in ("", "   \n", "short"):
            with self.subTest(bad=bad):
                tf.write_text(bad)
                with self.assertRaisesRegex(C.Rejected, "令牌"):
                    R.load_token()

    def test_listens_on_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")


class LoadedCompare(unittest.TestCase):
    def setUp(self):
        self.file = C.render(CURRENT, OWNER, REPOS)

    def live(self, **change):
        v = copy.deepcopy(self.file)
        for r in v["rules"]:
            r.setdefault("group", r["name"])
            r.setdefault("tell", "needed")
        v["sources"]["schedule"] = {"name": "定时"}
        for k, f in change.items():
            f(v)
        return v

    def test_same_config_with_hub_defaults_matches(self):
        with mock.patch.object(C, "loaded_rules", return_value=self.live()):
            self.assertEqual(C.mismatches(self.file), [])

    def test_hidden_field_difference_is_caught(self):
        def prompt(v):
            next(r for r in v["rules"] if r["name"] == "dispatch-dt-sandbox-codex")["prompt"] = "旧的"

        def cwd(v):
            next(r for r in v["rules"] if r["name"] == "dispatch-dt-sandbox-claude")["cwd"] = "~/old"

        def emits(v):
            next(r for r in v["rules"] if r["name"] == "dispatch-dt-sandbox-claude")["emits"] = {"source": "hermes", "types": ["x"]}

        def extra(v):
            v["rules"].append({"name": "dispatch-old-claude", "when": {"source": "hermes", "types": ["x"]}, "to": "dt-claude"})

        def answerers(v):
            v["channels"]["secretary"]["answerers"].append(OWNER)

        def old_route(v):
            next(r for r in v["rules"] if r["name"] == "试一下")["channels"] = ["desktop", "secretary"]
        for name, f in (("prompt", prompt), ("cwd", cwd), ("emits", emits), ("extra", extra), ("answerers", answerers),
                        ("old_route", old_route)):
            with self.subTest(name=name), mock.patch.object(C, "loaded_rules", return_value=self.live(x=f)):
                self.assertTrue(C.mismatches(self.file))

    def test_other_rule_listening_to_hermes_is_caught_and_never_installed(self):
        """#369 第 4 轮 P1-01：hermes 来源只给派单规则用。中台在跑的配置里有别的规则接 hermes 事件，算不一致（钥匙在时
        install、签钥匙都拒）；文件里有这种规则，render 直接拒装。"""
        spy = {"name": "custom-watch", "when": {"source": "hermes", "types": ["dispatch.dt-sandbox.claude.write"]},
               "to": "claude", "cwd": "~/x", "prompt": "另一套"}
        with mock.patch.object(C, "loaded_rules", return_value=self.live(x=lambda v: v["rules"].append(spy))):
            self.assertIn("custom-watch←hermes", C.mismatches(self.file))
        with self.assertRaisesRegex(C.Rejected, "不是派单规则却接 hermes 来源的事件，不装：custom-watch"):
            C.render({**CURRENT, "rules": [*CURRENT["rules"], spy]}, OWNER, REPOS)
        other = {**spy, "name": "gh-watch", "when": {"source": "local", "types": ["*"]}}     # 别的来源的规则照常
        self.assertIn("gh-watch", [r["name"] for r in C.render({**CURRENT, "rules": [*CURRENT["rules"], other]}, OWNER, REPOS)["rules"]])

    def test_unreadable_hub_db_refuses(self):
        with self.assertRaisesRegex(C.Rejected, "读不到"):
            C.loaded_rules()


FRAG = {
    "sources": {"github-issues": {"name": "GitHub issue 轮询"}},
    "agents": {"issue-watcher": {"name": "看", "worker": "dt-worker", "use": "command", "command": ["/usr/bin/true"]},
               "issue-reader": {"name": "读", "worker": "dt-worker", "use": "codex", "access": "read"}},
    "rules": [{"name": "看新 issue", "when": {"cron": "*/5 * * * *"}, "to": "issue-watcher", "tell": "never",
               "emits": {"source": "github-issues", "types": ["issues.opened"]}},
              {"name": "分析 issue", "when": {"source": "github-issues", "types": ["issues.opened"]},
               "to": "issue-reader", "channels": ["feishu"]}],
}


class Fragments(unittest.TestCase):
    """add-rules / remove-rules：手写的规则片段并进、拿出中台规则，不碰数字分身这一块、不动暂停状态（2026-11-27）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-config-frag-")
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        (self.d / "rules").mkdir()
        self.write_frag("issue-watch", FRAG)
        self.home = self.d / "hub"
        self.home.mkdir()
        self.current = C.render(CURRENT, OWNER, REPOS)
        (self.home / "rules.json").write_text(C.dumps(self.current), encoding="utf-8")
        for p in (mock.patch.object(C, "RULES_DIR", self.d / "rules"),
                  mock.patch.dict(os.environ, {"AGENT_AUTOLOOP_HOME": str(self.home),
                                               "DT_AUTOLOOP_EVIDENCE": str(self.d / "ev")}),
                  mock.patch.object(C, "run_cli", return_value=(0, "", ""))):
            p.start()
            self.addCleanup(p.stop)

    def write_frag(self, name, frag):
        (self.d / "rules" / f"{name}.json").write_text(json.dumps(frag, ensure_ascii=False), encoding="utf-8")

    def on_disk(self):
        return json.loads((self.home / "rules.json").read_text(encoding="utf-8"))

    def live_follows_disk(self):
        """中台载入：在用的配置就是磁盘上的，另外补一个默认字段（比较时不算差别）。"""
        def loaded():
            live = self.on_disk()
            for r in live["rules"]:
                r.setdefault("group", r["name"])
            return live
        return mock.patch.object(C, "loaded_rules", side_effect=loaded)

    def test_real_fragment_in_repo_is_accepted(self):
        with mock.patch.object(C, "RULES_DIR", HERE.parents[2] / "ops/autoloop_rules"):
            self.assertEqual(len(C.fragment("dt-sandbox-issue-watch")["rules"]), 2)

    def test_only_names_in_the_rules_dir(self):
        for bad in ("../x", "/etc/passwd", "Issue", "", "a.json"):
            with self.assertRaisesRegex(C.Rejected, "片段名"):
                C.fragment(bad)
        with self.assertRaisesRegex(C.Rejected, "没有这个规则片段"):
            C.fragment("nope")

    def test_fragment_cannot_touch_the_dispatch_block(self):
        cases = [
            {**FRAG, "rules": FRAG["rules"] + [{"name": "dispatch-x-claude", "when": {"cron": "* * * * *"}, "to": "issue-reader"}]},
            {**FRAG, "rules": [{**FRAG["rules"][1], "when": {"source": "hermes", "types": ["x"]}}]},
            {**FRAG, "rules": [{**FRAG["rules"][1], "channels": ["feishu", "secretary"]}]},
            {**FRAG, "sources": {"hermes": {"name": "冒充"}}},
            {**FRAG, "agents": {**FRAG["agents"], "dt-claude": {"name": "x", "worker": "w", "use": "claude"}}},
            {**FRAG, "channels": {"feishu": {"name": "飞书"}}},
        ]
        for i, frag in enumerate(cases):
            self.write_frag("bad", frag)
            with self.subTest(i), self.assertRaises(C.Rejected):
                C.fragment("bad")

    def test_add_keeps_dispatch_block_and_never_calls_the_hub(self):
        """模块级把 C.hub 换成一调就抛：add-rules 不暂停、不恢复任何规则，也就不会调中台接口。"""
        with self.live_follows_disk():
            out = C.change_fragment("issue-watch", False, True, str(self.home))
        self.assertTrue(out["changed"])
        new = self.on_disk()
        self.assertEqual(C.dt_block(new), C.dt_block(self.current))
        self.assertEqual([r["name"] for r in new["rules"]][-2:], ["看新 issue", "分析 issue"])
        self.assertEqual(new["agents"]["issue-reader"]["access"], "read")
        self.assertTrue(Path(out["backup"]).is_file())
        with self.live_follows_disk():                                     # 再跑一次：原样，不写
            self.assertFalse(C.change_fragment("issue-watch", False, True, str(self.home))["changed"])

    def test_remove_takes_out_only_the_fragment(self):
        with self.live_follows_disk():
            C.change_fragment("issue-watch", False, True, str(self.home))
            C.change_fragment("issue-watch", True, True, str(self.home))
        self.assertEqual(self.on_disk(), self.current)

    def test_not_loaded_puts_the_old_file_back(self):
        before = (self.home / "rules.json").read_text(encoding="utf-8")
        with mock.patch.object(C, "loaded_rules", return_value=self.current), \
                self.assertRaisesRegex(C.Rejected, "换回原样"):
            C.change_fragment("issue-watch", False, True, str(self.home), timeout=0)
        self.assertEqual((self.home / "rules.json").read_text(encoding="utf-8"), before)

    def test_check_failure_writes_nothing(self):
        before = (self.home / "rules.json").read_text(encoding="utf-8")
        with mock.patch.object(C, "run_cli", return_value=(1, "", "规则有问题")), \
                self.assertRaisesRegex(C.Rejected, "check"):
            C.change_fragment("issue-watch", False, True, str(self.home))
        self.assertEqual((self.home / "rules.json").read_text(encoding="utf-8"), before)

    def test_needs_confirm_and_target(self):
        with self.assertRaisesRegex(C.Rejected, "--confirm"):
            C.change_fragment("issue-watch", False)
        with self.assertRaisesRegex(C.Rejected, "目标中台目录"):
            C.change_fragment("issue-watch", False, True, None)


class Guards(unittest.TestCase):
    def test_install_and_key_share_one_lock(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "sub/config.lock"
            reached = mock.Mock(side_effect=AssertionError("没拿到锁也跑进去了"))
            with mock.patch.dict(os.environ, {"DT_AUTOLOOP_LOCK": str(lock)}), mock.patch.object(C, "LOCK_WAIT", 0.3), \
                    mock.patch.object(C, "_install", reached), mock.patch.object(C, "_issue_source_key", reached):
                with C.config_lock():                              # 另一个配置操作拿着锁
                    for call in (lambda: C.install(confirm=True, target=C.home()),
                                 lambda: C.issue_source_key(confirm=True, target=C.home())):
                        with self.assertRaisesRegex(C.Rejected, "还在进行"):
                            call()
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_target_must_be_named_and_match(self):
        for call in (C.install, C.issue_source_key):
            with self.subTest(call=call.__name__):
                with self.assertRaisesRegex(C.Rejected, "写明目标中台目录"):
                    call(confirm=True)
                with self.assertRaisesRegex(C.Rejected, "不一致"):
                    call(confirm=True, target=Path.home() / ".agent-autoloop")      # 正式中台目录 ≠ 实际（临时）目录

    def test_install_and_key_need_confirm(self):
        with self.assertRaisesRegex(C.Rejected, "确认"):
            C.install(confirm=False)
        with self.assertRaisesRegex(C.Rejected, "确认"):
            C.issue_source_key(confirm=False)


if __name__ == "__main__":
    unittest.main()
