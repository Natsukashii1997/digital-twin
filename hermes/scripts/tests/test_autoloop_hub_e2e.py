"""端到端自测（#195 批 0B-2）：在临时目录起一个真的 agent-autoloop 中台（--no-service，随机端口），按顺序验
装配置 → 派单规则载入并暂停 → 签 hermes 来源钥匙 → 网关发的事件被记下、同一 id 只记一次 → 别的来源发同类型事件派单规则不接
→ 重复装不变 → 钥匙在时不许新增派单规则 → 只读窗口不漏字段。
本机没有 agent-autoloop 命令行（如 CI）时跳过；DT_HUB_E2E=0 也跳过。不碰 ~/.agent-autoloop 和正式中台。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_readonly as R  # noqa: E402

CLI = os.environ.get("DT_AUTOLOOP_CLI", "agent-autoloop")
OWNER = "ou_fixture_owner_0001"
SKIP = os.environ.get("DT_HUB_E2E") == "0" or not shutil.which(CLI)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def raw_post(url, key, body):
    req = urllib.request.Request(url + "/v1/events", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
        return json.loads(r.read())


@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class HubEndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-hub-e2e-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root / "hub"
        self.home.mkdir()
        self.port = free_port()
        env = {**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}
        r = subprocess.run([CLI, "init", "--no-service", "--port", str(self.port), "--command", "echo {prompt}"],
                           capture_output=True, text=True, timeout=60, env=env, cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log = open(root / "up.log", "w")
        self.addCleanup(self.log.close)
        self.up = subprocess.Popen([CLI, "up"], env=env, cwd=root, stdout=self.log, stderr=subprocess.STDOUT)
        self.addCleanup(self._stop)
        cfg = json.loads((self.home / "config.json").read_text())
        self.url, self.local_key = cfg["url"].rstrip("/"), cfg["source"]["key"]
        self._wait_up()
        self.roster = root / "projects.json"
        self.write_roster(["o/dt-sandbox"])
        self.key_file = root / "dt/autoloop/hermes-source.key"
        for p in (mock.patch.dict(os.environ, {"AGENT_AUTOLOOP_HOME": str(self.home), "DT_OWNER_OPEN_ID": OWNER,
                                               "DT_HERMES_SOURCE_KEY_FILE": str(self.key_file), "DT_HUB": self.url,
                                               "DT_AUTOLOOP_EVIDENCE": str(root / "evidence"), "DT_AUTOLOOP_CLI": CLI,
                                               "DT_READONLY_TOKEN_FILE": str(root / "dt/autoloop/readonly.token")}),
                  mock.patch.object(G, "ROSTER", self.roster)):
            p.start()
            self.addCleanup(p.stop)

    def _stop(self):
        self.up.terminate()
        try:
            self.up.wait(10)
        except subprocess.TimeoutExpired:
            self.up.kill()

    def _wait_up(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open(self.url + "/healthz", timeout=2) as r:
                    if json.loads(r.read()).get("ok"):
                        return
            except (OSError, ValueError):
                time.sleep(0.5)
        self.fail("临时中台没起来")

    def write_roster(self, sandboxes):
        self.roster.write_text(json.dumps({"projects": [], "inventory": [
            {"path": f"github:{gh}", "category": "沙盒", "sandbox": True} for gh in sandboxes]}), encoding="utf-8")

    def test_install_key_dedupe_and_isolation(self):
        with self.assertRaisesRegex(C.Rejected, "先 install"):
            C.issue_source_key(confirm=True)                               # 规则还没装，不签钥匙

        out = C.install(confirm=True)
        self.assertTrue(out["changed"])
        st = C.status()
        self.assertEqual(st["dispatch_rules"], {"dispatch-dt-sandbox-claude": {"loaded": True, "paused": True},
                                                "dispatch-dt-sandbox-codex": {"loaded": True, "paused": True}})
        self.assertTrue(all(st["channels"][c]["in_rules"] for c in C.NEW_CHANNELS))
        self.assertFalse(st["source_hermes"]["key"])

        rules_text = C.rules_file().read_text(encoding="utf-8")       # 文件改了但中台没载入（文件里有错规则）：签钥匙拒绝
        broken = json.loads(rules_text)
        for r in broken["rules"]:
            if r["name"] == "dispatch-dt-sandbox-codex":
                r["when"]["types"] = ["dispatch.dt-sandbox.codex.other"]
        broken["rules"].append({"name": "坏规则", "when": {"source": "local", "types": ["x"]}, "to": "agent", "title": "{坏}"})
        C.rules_file().write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        time.sleep(1.5)
        with self.assertRaisesRegex(C.Rejected, "不一致"):
            C.issue_source_key(confirm=True)
        C.rules_file().write_text(rules_text, encoding="utf-8")
        time.sleep(1.5)

        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-codex/resume", {})
        with self.assertRaisesRegex(C.Rejected, "暂停"):
            C.issue_source_key(confirm=True)                               # 有一条没暂停就不签
        self.assertFalse(self.key_file.exists())
        C.install(confirm=True)                                            # 重装把它重新暂停

        self.assertTrue(C.issue_source_key(confirm=True)["ok"])
        self.assertEqual(self.key_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(C.status()["source_hermes"]["key"])

        row = {"task_id": "t-20261117-abcdef", "repo": "dt-sandbox", "executor": "claude"}
        body = G.event_body({"event_key": "t-20261117-abcdef:g1:start", "kind": "start", "generation": 1}, row)
        first, again = G.post_event(body), G.post_event(body)
        self.assertEqual(first, again)                                     # 同一 id 只记一次，补发拿回原事件号
        key = self.key_file.read_text().strip()
        dup = raw_post(self.url, key, body)
        self.assertEqual((dup["event"], dup["duplicate"]), (first, True))
        fresh = raw_post(self.url, key, {**body, "id": "t-20261117-abcdef:g1:rework:1"})
        self.assertEqual(fresh["routed"], [])                              # 暂停中：记下但不派
        self.assertIn("dispatch-dt-sandbox-claude", fresh.get("paused", []))

        other = raw_post(self.url, self.local_key, {**body, "id": "local-1"})
        self.assertEqual(other["routed"], [])                              # 别的来源发同类型：派单规则不接
        self.assertNotIn("dispatch-dt-sandbox-claude", other.get("paused", []))

        self.assertFalse(C.install(confirm=True)["changed"])               # 重复装不变、仍暂停
        with self.assertRaisesRegex(C.Rejected, "已经有了"):
            C.issue_source_key(confirm=True)
        self.write_roster(["o/dt-sandbox", "o/dt-sandbox2"])
        with self.assertRaisesRegex(C.Rejected, "不许新增"):
            C.install(confirm=True)                                        # 钥匙在时新规则会有未暂停的空隙
        self.write_roster(["o/dt-sandbox"])
        with mock.patch.object(C, "PROMPT", "换了提示词"), self.assertRaisesRegex(C.Rejected, "不许新增"):
            C.install(confirm=True)                                        # 钥匙在时同名规则也不许改
        with mock.patch.object(C, "AGENTS", {**C.AGENTS, "codex": ("dt-claude", "x")}), \
                self.assertRaisesRegex(C.Rejected, "不许新增"):
            C.install(confirm=True)                                        # 换执行方同样拒绝

        snap = R.read()
        lines = {line["id"]: line for line in snap["lines"]}
        self.assertTrue(lines["dispatch-dt-sandbox-claude"]["paused"])
        self.assertNotIn(key, json.dumps(snap))


    def test_pause_failure_leaves_no_key(self):
        real = C.hub

        def flaky(method, path, body=None):
            if path.endswith("/pause"):
                raise C.Rejected("中台回 HTTP 500")
            return real(method, path, body)
        with mock.patch.object(C, "hub", side_effect=flaky), self.assertRaisesRegex(C.Rejected, "500"):
            C.install(confirm=True)
        st = C.status()
        self.assertTrue(all(v["loaded"] and not v["paused"] for v in st["dispatch_rules"].values()))
        with self.assertRaisesRegex(C.Rejected, "暂停"):
            C.issue_source_key(confirm=True)                               # 规则开着就签不出钥匙
        self.assertFalse(self.key_file.exists())
        C.install(confirm=True)                                            # 重装补上暂停
        self.assertTrue(C.issue_source_key(confirm=True)["ok"])


if __name__ == "__main__":
    unittest.main()
