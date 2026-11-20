"""全量测试并行跑（常浩 2026-11-20 定提速 B）：按测试类分片、多进程同时跑，汇总成一个结果。

用法（仓库根目录）：
    /usr/bin/python3 -B scripts/test_all.py                 # 两套全量：hermes/scripts/tests、orca/tests
    /usr/bin/python3 -B scripts/test_all.py -j 4 --log-dir <目录> [测试目录 ...]

做法：
1. 每套测试先在子进程里按 unittest discover 的规则列出全部用例（和串行 `python -m unittest discover -s <目录>` 同一套），
   按测试类分组，大类再按原顺序切成每片最多 --chunk 条（默认 8；最慢的 MulticaNight 一类 112 条、每条都起子进程，
   不切就是整轮的长杆）；导入失败的模块单独成一片，到跑的时候照样报错（导入时 SkipTest 的照 discover 记一条跳过）。
   用了 load_tests 的模块按名字重载不回原用例，直接拒绝。
2. 每片起一个独立子进程跑（`--worker`）：先把测试目录放到 sys.path 最前面，有 `_isolation` 就先导入它（和 discover
   的顺序一样），每个子进程各有自己的隔离临时目录、环境变量和禁网，互不相踩。
3. 汇总：任何一片失败、子进程没交结果、实跑条数和列出的条数对不上（逐片对账：实跑 + setUpClass / setUpModule 里
   跳过而没开始的，必须正好等于列出的），整体失败（退出码 1）。每片的输出原样存一份日志，
   失败片的日志在最后原样打印；按估计耗时从长到短排队：上次各类每条平均耗时记在缓存（~/.cache/dt-test-all/），
   没记过的按条数估；本次各片耗时另存 <日志目录>/durations.json。

只用标准库，Python 3.9 可跑（和 Hermes、CI 同版本）。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITES = ["hermes/scripts/tests", "orca/tests"]
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dt-test-all" / "durations.json"


# ---------- 子进程：列用例、跑一片 ----------

def _prepare(suite):
    """sys.path 和串行一致：`python -m unittest` 放的是当前目录（仓库根），不是本脚本所在的 scripts/；
    再像 discover 一样把测试目录放在最前面；有统一隔离包就最先导入（#212）。"""
    if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
        sys.path[0] = str(ROOT)
    sys.path.insert(0, str(suite))
    if (Path(suite) / "_isolation").is_dir():
        import _isolation  # noqa: F401


def _flatten(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


def _unit_of(test):
    """(类名, 这条用例的名字)。导入失败（_FailedTest）按模块名整个重跑，原样报出导入错误。"""
    cls = type(test)
    if cls.__module__ == "unittest.loader":
        return test._testMethodName, test._testMethodName
    name = f"{cls.__module__}.{cls.__qualname__}"
    return name, f"{name}.{test._testMethodName}"


def cmd_list(suite):
    _prepare(suite)
    tests = unittest.TestLoader().discover(str(suite), top_level_dir=str(suite))
    here = Path(suite).resolve()
    custom = sorted(m.__name__ for m in list(sys.modules.values())
                    if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == here and hasattr(m, "load_tests"))
    if custom:                                        # load_tests 造的用例按名字重载不回来，切片后会少跑、误报通过
        sys.exit(f"这些测试模块用了 load_tests，按名字切片会丢用例，test_all.py 不支持，请改写法：{', '.join(custom)}")
    units = {}
    for t in _flatten(tests):
        cls, name = _unit_of(t)
        units.setdefault(cls, []).append(name)
    json.dump(units, sys.stdout, ensure_ascii=False)


class _Result(unittest.TextTestResult):
    """另记下真正开始跑的用例，好和列出的名字逐条对账。"""
    def startTest(self, test):
        self.started = getattr(self, "started", []) + [test.id()]
        super().startTest(test)


def cmd_worker(suite, names, result_path):
    _prepare(suite)
    loader, suite_obj = unittest.TestLoader(), unittest.TestSuite()
    for name in names:
        try:
            suite_obj.addTest(loader.loadTestsFromName(name))
        except unittest.SkipTest as e:               # 模块导入时就跳过：和 discover 一样记成一条跳过的用例
            suite_obj.addTest(unittest.loader._make_skipped_test(name, e, loader.suiteClass))
    r = unittest.TextTestRunner(stream=sys.stderr, verbosity=2, resultclass=_Result).run(suite_obj)
    started = set(getattr(r, "started", []))
    # setUpClass / setUpModule 里跳过（SkipTest）：这些用例不开始、不算 Ran，串行也一样，记成 unrun_skipped 对账
    holders = [t.description for t, _ in r.skipped if isinstance(t, unittest.suite._ErrorHolder)]
    unrun = [n for n in names if n not in started]
    unrun_skipped = sum(1 for n in unrun if any(f"({n.rpartition('.')[0]})" in h or f"({n.split('.')[0]})" in h
                                                 for h in holders))
    data = {"run": r.testsRun, "failures": len(r.failures), "errors": len(r.errors), "skipped": len(r.skipped),
            "expected_failures": len(r.expectedFailures), "unexpected_successes": len(r.unexpectedSuccesses),
            "unrun_skipped": unrun_skipped, "ok": r.wasSuccessful()}
    Path(result_path).write_text(json.dumps(data), encoding="utf-8")
    sys.stderr.flush()
    return 0 if data["ok"] else 1


# ---------- 主进程：排队、汇总 ----------

def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_units(py, suite):
    p = subprocess.run([py, "-B", __file__, "--list", str(suite)], cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        raise SystemExit(f"列用例失败：{suite}")
    return json.loads(p.stdout)


def split(units, chunk):
    """每类按原顺序切成每片最多 chunk 条；导入失败的模块（名字里没有类）整个一片。"""
    out = []
    for cls, names in units.items():
        if names == [cls]:
            out.append((cls, cls, names))
            continue
        n = -(-len(names) // chunk)
        for i in range(n):
            part = names[i * chunk:(i + 1) * chunk]
            out.append((cls, cls if n == 1 else f"{cls}#{i + 1}", part))
    return out


def _tag(suite):
    """日志文件名前缀：仓库里的测试目录用相对路径，仓库外的（绝对路径）用目录名加短哈希，免得撑爆文件名。"""
    if not os.path.isabs(suite):
        return suite.replace("/", "_")
    return f"{Path(suite).name}-{zlib.crc32(suite.encode()):08x}"


def run_unit(py, suite, cls, label, names, log_dir):
    safe = f"{_tag(suite)}__{label}"
    log, res = log_dir / f"{safe}.log", log_dir / f"{safe}.result.json"
    for f in (log, res):                             # 复用 --log-dir 时不能把上次的结果当成这次的
        if f.exists():
            f.unlink()
    start = time.monotonic()
    with open(log, "wb") as f:
        rc = subprocess.run([py, "-B", __file__, "--worker", str(suite), "--result", str(res), *names],
                            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
    result = _load_json(res) or None
    # 逐片对账：实跑 + 类 / 模块级跳过没开始的，必须正好是列出的条数
    counted = (result["run"] + result.get("unrun_skipped", 0)) if result else None
    return {"suite": suite, "cls": cls, "unit": label, "n": len(names), "rc": rc, "secs": time.monotonic() - start,
            "log": log, "result": result, "mismatch": counted != len(names)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="全量测试并行跑")
    ap.add_argument("suites", nargs="*", default=DEFAULT_SUITES)
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--chunk", type=int, default=8, help="每片最多几条用例（默认 8）")
    ap.add_argument("--log-dir", help="每片日志存这里（默认临时目录：全过就删，有失败就留着）")
    ap.add_argument("--python", default=sys.executable, help="跑测试用的解释器（默认就是跑本脚本的这个）")
    a = ap.parse_args(argv)
    if a.jobs < 1 or a.chunk < 1:
        ap.error("-j 和 --chunk 至少是 1")

    py = a.python
    log_dir = Path(a.log_dir or tempfile.mkdtemp(prefix="dt-test-all-")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    jobs, listed = [], {}
    for s in a.suites:
        suite = Path(s) if Path(s).is_absolute() else ROOT / s
        rel = os.path.relpath(suite, ROOT)
        rel = str(suite.resolve()) if rel.startswith("..") else rel
        units = list_units(py, suite)
        listed[rel] = sum(len(v) for v in units.values())
        jobs += [(rel, *u) for u in split(units, a.chunk)]
    cached = _load_json(CACHE)                       # {套::类: 每条平均秒数}；没记过的按每条 0.5 秒估
    jobs.sort(key=lambda j: cached.get(f"{j[0]}::{j[1]}", 0.5) * len(j[3]), reverse=True)
    print(f"共 {sum(listed.values())} 条用例、{len(jobs)} 片，{a.jobs} 路并行，解释器 {py}", flush=True)

    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        results = list(ex.map(lambda j: run_unit(py, *j, log_dir), jobs))

    durations = {f"{r['suite']}::{r['unit']}": round(r["secs"], 2) for r in results}
    (log_dir / "durations.json").write_text(json.dumps(durations, ensure_ascii=False, indent=1), encoding="utf-8")
    per_cls = {}
    for r in results:
        t = per_cls.setdefault(f"{r['suite']}::{r['cls']}", [0.0, 0])
        t[0] += r["secs"]
        t[1] += r["n"]
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({**cached, **{k: round(s / n, 3) for k, (s, n) in per_cls.items()}},
                                    ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    bad = [r for r in results if r["rc"] != 0 or not r["result"] or not r["result"].get("ok") or r["mismatch"]]
    for r in bad:
        why = "实跑条数和列出的对不上" if r["result"] and r["result"].get("ok") and r["rc"] == 0 else f"退出码 {r['rc']}"
        print(f"\n{'=' * 70}\n失败片：{r['suite']} {r['unit']}（{why}，列出 {r['n']} 条，日志 {r['log']}）\n{'=' * 70}")
        sys.stdout.write(r["log"].read_text(encoding="utf-8", errors="replace"))

    total, ok = {}, not bad
    keys = ("run", "failures", "errors", "skipped", "expected_failures", "unexpected_successes", "unrun_skipped")
    for r in results:
        t = total.setdefault(r["suite"], dict.fromkeys(keys, 0))
        for k in keys:
            t[k] += (r["result"] or {}).get(k, 0)
    print(f"\n{'-' * 70}")
    for suite, n in listed.items():
        t = total.get(suite, dict.fromkeys(keys, 0))
        extra = "，".join(f"{k}={t[k]}" for k in keys[1:] if t[k])
        same = "" if t["run"] + t["unrun_skipped"] == n else f"  ← 列出 {n} 条，实跑条数对不上"
        ok = ok and not same
        print(f"{suite}: Ran {t['run']} tests{'（' + extra + '）' if extra else ''}{same}")
    slow = sorted(results, key=lambda r: r["secs"], reverse=True)[:5]
    print("最慢的片：" + "；".join(f"{r['unit']} {r['secs']:.0f}s" for r in slow))
    print(f"合计 Ran {sum(t['run'] for t in total.values())} tests in {time.monotonic() - t0:.1f}s（{a.jobs} 路并行）")
    print("OK" if ok else f"FAILED（失败 {len(bad)} 片，日志在 {log_dir}）")
    if ok and not a.log_dir:
        for p in log_dir.iterdir():
            p.unlink()
        log_dir.rmdir()
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--list":
        cmd_list(sys.argv[2])
    elif len(sys.argv) >= 6 and sys.argv[1] == "--worker" and sys.argv[3] == "--result":
        sys.exit(cmd_worker(sys.argv[2], sys.argv[5:], sys.argv[4]))
    else:
        sys.exit(main())
