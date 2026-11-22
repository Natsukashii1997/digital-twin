#!/usr/bin/env python3
"""中台升级、回滚、搬家演练（批 0D 恢复门，#195；方案 docs/agent-autoloop-底座-方案.md 3.2「锁版本、验证后升级」、
第 9 节 0D 验收；ADR-015 生效顺序第 3 条「搬家后中台挂起任务照常恢复」；设计 docs/恢复门-0D-设计.md）。

**只在临时目录里演练，绝不碰本机正在运行的中台**（127.0.0.1:3350、~/.agent-autoloop 的数据）：
  - 引擎：从本机中台程序目录（默认 ~/.agent-autoloop/app，DT_DRILL_APP 可改）用 git clone --shared 只读地克隆到临时目录，
    再检出要演练的版本（例如 v1.8.4、v1.8.5）；原目录一个字节也不改；
  - 中台：每个演练自己的 AGENT_AUTOLOOP_HOME（临时目录）、随机端口、--no-service（不装 launchd）；用完停掉、删掉。
  - 动手前核对：中台目录不能是 ~/.agent-autoloop、端口不能是 3350，否则直接拒绝。

演练的「升级流程」（以后真升级照这个顺序，真升级要停正式服务，是 D 档，由常浩说了才做）：
  1. 停写（autoloop_recovery.hold，审查 P1-06）：恢复闸关上，发飞书、发布、回答、派单一律停；登记恢复点
     （快照目录、旧版本提交号、此刻中台里最大的卡片号）；
  2. 排空：等还在做的写（出口账本 executing、本人回答在途、派单事件在途）全部结束，排不空不快照；
  3. 记下挂起的东西（开着的卡片、等回答的轮次），中台开着做快照（agent-autoloop backup）；
  4. 停旧版、起新版；核对：/healthz 是新版本、挂起的卡片和轮次一个不少；
  5. 通过 → 改锁定版本（pin）、维护结束（release retired：升级前的快照作废，restorable 再也不认）；
     不通过 → 先核这个恢复点还能用（restorable），停新版、用旧版程序 restore 快照、起旧版、再核对挂起的东西，
     维护结束（release rolled_back：先核快照之后没有发出过卡片，卡片库、出口账本里有比快照时最大号还大的卡片就保持停写，交常浩）。
  快照之后、停旧版之前中台还可能出新卡片：停写期间桥接不领卡、不发，所以回滚丢掉这张卡时本地账本里也没有它，卡片号复用不会撞上旧消息。
  锁定新版本、维护结束以后，**不再用升级前的快照回滚**（之后的卡片、轮次、对外动作会丢，卡片号会被复用）；要退回旧版另做一次升级。

搬家演练（模拟 0E-2 把中台搬进 dt-gate）：停写 → 排空 → 快照 → 停 → 恢复到另一个目录 → 在新目录起 → 挂起的东西照常恢复 → 维护结束。
演练时恢复闸、出口账本、卡片库、凭证库、状态库都指到演练自己的临时目录（run_all 自己设好环境变量），不碰正式状态目录。
真跨账号（文件属主、权限、钥匙串）在 0E-2 用真实 dt-gate 账号再验一次，这里只验「换了目录、数据还能接着跑」。

  python3 ops/autoloop_drill.py run [--out <证据目录>]    全套演练一遍，结果写成 JSON（默认只打印）
不调用 AI。不签钥匙、不装服务、不碰 dt-gate / dt-secretary 账号。
"""
import argparse
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "hermes/scripts/tests/fixtures/ask_receipt.py"
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".agent-autoloop"
REAL_PORT = 3350


class DrillError(Exception):
    pass


def app_source():
    return Path(os.environ.get("DT_DRILL_APP") or REAL_HOME / "app").expanduser()


def available():
    """本机有没有能克隆的中台程序和 node（CI 上没有就跳过）。"""
    return (app_source() / ".git").exists() and shutil.which("node") is not None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _no_proxy():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Engine:
    """某个版本的中台程序：只读克隆到临时目录。"""

    def __init__(self, root, ref):
        self.dir = Path(root) / f"engine-{ref.replace('^', '').replace('{', '').replace('}', '').replace('/', '-')}"
        if not self.dir.exists():
            subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(app_source()), str(self.dir)],
                           check=True, capture_output=True, timeout=120)
            subprocess.run(["git", "-C", str(self.dir), "-c", "advice.detachedHead=false", "checkout", "-q",
                            f"{ref}^{{commit}}"], check=True, capture_output=True, timeout=120)
        self.commit = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"], check=True, capture_output=True,
                                     text=True).stdout.strip()

    def cli(self):
        return ["node", "--disable-warning=ExperimentalWarning", str(self.dir / "bin/agent-autoloop.ts")]

    def broken_copy(self, root):
        """一个起不来的「坏版本」：同一份程序，host/up.ts 一载入就抛错（模拟升级后启动失败）。"""
        bad = Engine.__new__(Engine)
        bad.dir = Path(root) / f"{self.dir.name}-broken"
        if not bad.dir.exists():
            shutil.copytree(self.dir, bad.dir, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            with open(bad.dir / "host/up.ts", "a", encoding="utf-8") as f:
                f.write('\nthrow new Error("drill: broken upgrade");\n')
        bad.commit = self.commit + "-broken"
        return bad


class Hub:
    """临时中台：自己的目录、随机端口、不装服务。"""

    def __init__(self, home, port=None):
        self.home = Path(home)
        self.port = port or free_port()
        if self.home.resolve() == REAL_HOME.resolve() or self.port == REAL_PORT:
            raise DrillError("演练不许用本机正式中台的目录或端口")
        self.proc, self.engine, self.log = None, None, None

    def env(self):
        return {**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home), "NO_PROXY": "127.0.0.1,localhost"}

    def run(self, engine, *args, timeout=120):
        return subprocess.run([*engine.cli(), *args], env=self.env(), capture_output=True, text=True, timeout=timeout,
                              cwd=self.home.parent)

    def init(self, engine, command=None):
        self.home.mkdir(parents=True, exist_ok=True)
        r = self.run(engine, "init", "--no-service", "--port", str(self.port), "--command",
                     command or f"{sys.executable} {FIXTURE} {{prompt}}")
        if r.returncode:
            raise DrillError(f"init 失败：{r.stderr[-300:]}")

    @property
    def url(self):
        return json.loads((self.home / "config.json").read_text(encoding="utf-8"))["url"].rstrip("/")

    def start(self, engine, wait=30):
        """起 up；等 /healthz 通且提交号是这个版本。起不来返回 False（进程会被停掉）。"""
        self.engine = engine
        self.log = open(self.home.parent / f"up-{self.home.name}.log", "a")
        self.proc = subprocess.Popen([*engine.cli(), "up"], env=self.env(), cwd=self.home.parent, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        end = time.time() + wait
        while time.time() < end:
            if self.proc.poll() is not None:
                break
            if self.healthz() == engine.commit:
                return True
            time.sleep(0.3)
        self.stop()
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.proc = None
        if self.log:
            self.log.close()
            self.log = None

    def healthz(self):
        try:
            with _no_proxy().open(self.url + "/healthz", timeout=2) as r:
                out = json.loads(r.read())
            return out.get("commit") if out.get("ok") else None
        except (OSError, ValueError):
            return None

    def api(self, method, path, body=None):
        owner = json.loads((self.home / "config.json").read_text(encoding="utf-8"))["owner"]
        req = urllib.request.Request(self.url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {owner}", "Content-Type": "application/json"})
        with _no_proxy().open(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")

    def state(self):
        return self.api("GET", "/v1/state")

    def pending(self):
        """挂起的东西：开着的卡片（号、标题、选项）。等回答的轮次就挂在这些卡片上。"""
        st = self.state()
        return sorted((c["id"], c.get("title"), tuple(c.get("options") or ())) for c in st.get("cards", [])
                      if c.get("state") == "open")

    def ask(self, engine=None, wait=30):
        """让试跑规则出一张提问卡（轮次挂起等回答），返回卡片号。"""
        r = self.run(engine or self.engine, "try", timeout=60)
        end = time.time() + wait
        while time.time() < end:
            opened = self.pending()
            if opened:
                return opened[-1][0]
            time.sleep(0.5)
        raise DrillError(f"中台没出提问卡（try：{r.stdout[-200:]}{r.stderr[-200:]}）")

    def answer_and_finish(self, card_id, choice=0, wait=60):
        """本人回答这张卡，等挂起的轮次续上、跑完；返回跑完的说明。"""
        self.api("POST", f"/v1/cards/{card_id}/answer", {"choice": choice})
        end = time.time() + wait
        while time.time() < end:
            st = self.state()
            for c in st.get("cards", []) + st.get("recentCards", []):     # 只认这张卡自己的结果
                if c["id"] == card_id and c.get("state") == "done" and "试跑通过" in (c.get("result") or ""):
                    return c["result"]
            time.sleep(0.5)
        raise DrillError("回答后挂起的轮次没有续上跑完")

    def backup(self, engine, dest):
        r = self.run(engine, "backup", str(dest))
        if r.returncode:
            raise DrillError(f"快照失败：{r.stderr[-300:]}")
        return dest

    def restore(self, engine, src):
        r = self.run(engine, "restore", str(src), "--yes")
        if r.returncode:
            raise DrillError(f"恢复失败：{r.stderr[-300:]}")


# ---------- 流程 ----------
def recovery():
    """恢复闸模块（hermes/scripts/autoloop_recovery）。路径由调用方的环境变量指定（演练里指到临时目录）。"""
    scripts = str(Path(__file__).resolve().parents[1] / "hermes/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import autoloop_recovery as R
    return R


def max_card(hub):
    st = hub.state()
    return max([c["id"] for c in st.get("cards", []) + st.get("recentCards", [])] or [0])


def begin_maintenance(hub, point, snapshot, commit, wait=60):
    """停写、登记恢复点、排空在做的写（审查 P1-06）。排不空就结束维护（retired 不会发生在这里：恢复点没用过）并报错。"""
    R = recovery()
    R.hold(point, snapshot, commit, max_card(hub))
    end = time.time() + wait
    while True:
        writes = R.in_flight_writes()
        if not writes:
            return {"point": point, "max_card": R.restorable(point)["max_card"]}
        if time.time() > end:
            raise DrillError(f"停写后还有在做的写，排不空，不快照：{writes[:5]}")
        time.sleep(0.5)


def upgrade(hub, old, new, snapshot, damage=None, window=None, point=None):
    """按「升级流程」做一次：停写 → 排空 → 快照 → 停旧 → 起新 → 核对挂起的东西 → 通过就锁定新版、维护结束；
    不通过核过恢复点后用快照回滚到旧版、核对卡片号、维护结束。damage(hub) 模拟坏升级把数据弄坏；
    window(hub) 在快照之后、停旧版之前跑（模拟这段时间里中台又出了新卡片）。返回过程记录。"""
    R = recovery()
    point = point or f"up-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    out = {"from": old.commit, "to": new.commit, **begin_maintenance(hub, point, snapshot, old.commit)}
    before = hub.pending()
    out["pending_before"] = before
    hub.backup(old, snapshot)
    if window:
        out["window"] = window(hub)
    hub.stop()
    if hub.start(new) and hub.pending() == before:
        R.pin(new.commit, confirm=True)
        R.release(point, "retired", "新版本核对通过、已锁定")
        out.update(ok=True, on=new.commit)
        return out
    hub.stop()
    R.restorable(point)                                    # 只用仍在维护的恢复点回滚
    if damage:
        damage(hub)
    hub.restore(old, snapshot)
    if not hub.start(old):
        raise DrillError("回滚后旧版本起不来")
    out.update(ok=False, rolled_back=True, on=old.commit, pending_after=hub.pending(),
               mismatched=R.mismatched_after(out["max_card"]))
    R.release(point, "rolled_back", "坏升级，用恢复点回滚")
    return out


def move(hub, engine, new_home, snapshot, point=None):
    """搬家：停写 → 排空 → 快照 → 停 → 恢复到新目录（同一个端口）→ 在新目录起 → 维护结束。返回新的 Hub。
    维护结束前调用方要把恢复闸指到新目录（中台地址不变，AGENT_AUTOLOOP_HOME 变）。"""
    point = point or f"mv-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    out = begin_maintenance(hub, point, snapshot, engine.commit)
    out["pending_before"] = hub.pending()
    hub.backup(engine, snapshot)
    hub.stop()
    moved = Hub(new_home, hub.port)
    moved.home.mkdir(parents=True, exist_ok=True)
    moved.restore(engine, snapshot)
    if not moved.start(engine):
        raise DrillError("搬家后中台起不来")
    out["pending_after"] = moved.pending()
    if out["pending_after"] == out["pending_before"]:
        recovery().release(point, "retired", "搬家核对通过")
    return moved, out


def corrupt(hub):
    """模拟坏升级写坏了数据库。"""
    with open(hub.home / "hub.db", "r+b") as f:
        f.seek(0)
        f.write(b"\0" * 4096)


def run_all(root, old_ref="v1.8.4", new_ref="v1.8.5"):
    """全套：升级成功、升级失败回滚、搬家，每一步都核对挂起的卡片和轮次能接着跑完。"""
    root = Path(root)
    state = root / "dt"                                    # 恢复闸和各账本都指到演练自己的目录
    os.environ.update({"DT_EXITS_DB": str(state / "exits.db"), "DT_HUB_PIN": str(state / "hub-pin.json"),
                       "DT_GRANTS_DB": str(state / "grants.db"), "DT_AUTOLOOP_CARDS_DB": str(state / "cards.db"),
                       "DT_GRANTS_STATUS": str(state / "status.json"), "DT_GRANTS_TASKS": str(state / "tasks"),
                       "DT_STATE_DB": str(state / "twin-state.db")})
    old, new = Engine(root, old_ref), Engine(root, new_ref)
    report = {}
    hub = Hub(root / "a/hub")
    hub.init(old)
    try:
        assert hub.start(old), "旧版本起不来"
        os.environ.update({"DT_HUB": hub.url, "AGENT_AUTOLOOP_HOME": str(hub.home)})
        R = recovery()
        R.pin(old.commit, confirm=True)
        R.ensure_open()
        card = hub.ask(old)
        up = upgrade(hub, old, new, root / "snap-1")
        R.ensure_open()
        up["resumed"] = hub.answer_and_finish(card) if up["ok"] else None
        report["upgrade"] = up
        card = hub.ask(new)
        bad = new.broken_copy(root)
        down = upgrade(hub, new, bad, root / "snap-2", damage=corrupt)
        R.ensure_open()
        down["resumed"] = hub.answer_and_finish(card)
        report["failed_upgrade"] = down
        R.ensure_open()
        card = hub.ask(new)
        os.environ["AGENT_AUTOLOOP_HOME"] = str(root / "b/hub")   # 恢复闸跟着搬到新目录
        hub, mv = move(hub, new, root / "b/hub", root / "snap-3")
        R.ensure_open()
        mv["resumed"] = hub.answer_and_finish(card)
        report["move"] = mv
    finally:
        hub.stop()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out")
    a = ap.parse_args(argv)
    if not available():
        print(json.dumps({"ok": False, "why": "本机没有可克隆的中台程序或 node"}, ensure_ascii=False))
        return 1
    with tempfile.TemporaryDirectory(prefix="dt-drill-") as tmp:
        rep = run_all(tmp)
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out:
        out = Path(a.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / f"drill-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
