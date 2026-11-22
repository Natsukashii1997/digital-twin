#!/usr/bin/env python3
"""演练用的假 gh（批 0D）：PR 和合并记在 DRILL_GH_STATE 的 JSON 里，每次真正创建 / 合并都记一笔，用来数有没有重复。
DRILL_GH_MODE 文件内容：空 = 正常；down = 连不上（退出码 1）；hang-before = 先卡住再做；hang-after = 做完再卡住。
DRILL_GH_HEAD：pr create 时这条分支的头提交（真 gh 从远端读，这里由调用方给）。"""
import json
import os
import sys
import time

state_file, mode_file = os.environ["DRILL_GH_STATE"], os.environ["DRILL_GH_MODE"]
try:
    mode = open(mode_file).read().strip()
except OSError:
    mode = ""
if mode == "down":
    sys.stderr.write("error connecting to api.github.com\n")
    sys.exit(1)
if mode == "hang-before":
    time.sleep(60)
try:
    st = json.load(open(state_file))
except (OSError, ValueError):
    st = {"prs": [], "creates": 0, "merges": 0}
a = sys.argv[1:]
arg = lambda k: a[a.index(k) + 1]                      # noqa: E731
if a[:2] == ["pr", "list"]:
    print(json.dumps([p for p in st["prs"] if p["headRefName"] == arg("--head")]))
elif a[:2] == ["pr", "view"]:
    p = next(p for p in st["prs"] if p["number"] == int(a[2]))
    print(json.dumps(p))
elif a[:2] == ["pr", "create"]:
    n = len(st["prs"]) + 1
    st["prs"].append({"number": n, "url": f"https://github.com/{arg('-R')}/pull/{n}", "state": "OPEN",
                      "headRefName": arg("-H"), "headRefOid": os.environ["DRILL_GH_HEAD"], "baseRefName": arg("-B"),
                      "mergeCommit": None, "isCrossRepository": False,             # 来源本仓库（对账按 pr_is_ours 核）
                      "headRepository": {"name": arg("-R").split("/")[1]},
                      "headRepositoryOwner": {"login": arg("-R").split("/")[0]}})
    st["creates"] += 1
    json.dump(st, open(state_file, "w"))
    print(st["prs"][-1]["url"])
elif a[:2] == ["pr", "merge"]:
    p = next(p for p in st["prs"] if p["number"] == int(a[2]))
    if p["state"] != "OPEN" or p["headRefOid"] != arg("--match-head-commit"):
        sys.stderr.write("not mergeable\n")
        sys.exit(1)
    p.update(state="MERGED", mergeCommit={"oid": "m" * 40})
    st["merges"] += 1
    json.dump(st, open(state_file, "w"))
else:
    sys.exit(2)
if mode == "hang-after":
    time.sleep(60)
