"""假 gh（执行器侧，#174 身份闸采集层集成测试）：真跑 IDENT_SCRIPT 时代替容器里的 gh。
FAKE_GH_SCENARIO 指向的 JSON：
  version         gh --version 的第一行
  auth_json       gh auth status --json hosts 的输出；null 时模仿 gh 2.23 报 unknown flag
  user / repo     gh api user、gh api repos/<repo> 的输出
  rulesets_pages  规则集列表，每页一个数组；带 --paginate 时各页依次拼接输出（页间无分隔），不带只给第一页
  rulesets        id → 规则集详情（只用 current_user_can_bypass）
  repos_pages     user/repos，同上分页
  fail            路径 → [退出码, stderr]
每次调用的参数记进 calls。"""
import json
import os
import sys
from pathlib import Path

p = Path(os.environ["FAKE_GH_SCENARIO"])
w = json.loads(p.read_text(encoding="utf-8"))
args = sys.argv[1:]
w.setdefault("calls", []).append(args)
p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")


def done(out="", err="", code=0):
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(code)


if args == ["--version"]:
    done(w.get("version", "gh version 2.23.0 (2023-02-07)") + "\nhttps://github.com/cli/cli/releases/tag/v2.23.0\n")
if args[:2] == ["auth", "status"]:
    if w.get("auth_json") is None:
        done(err="unknown flag: --json\n\nUsage:  gh auth status [flags]\n", code=1)
    done(json.dumps(w["auth_json"]) + "\n")
if args[:1] != ["api"] or len(args) < 2:
    done(err=f"假 gh 不认识：{args}\n", code=2)
path, paginate = args[1], "--paginate" in args[2:]
if path in w.get("fail", {}):
    code, err = w["fail"][path]
    done(err=err + "\n", code=code)


def listed(pages):
    return "".join(json.dumps(pg) for pg in (pages if paginate else pages[:1])) + "\n"


if path == "user":
    done(json.dumps(w["user"]) + "\n")
if path.endswith("/rulesets"):
    done(listed(w["rulesets_pages"]))
if "/rulesets/" in path:
    rid = path.rsplit("/", 1)[1]
    if rid not in w.get("rulesets", {}):
        done(err="gh: Not Found (HTTP 404)\n", code=1)
    done(json.dumps(w["rulesets"][rid]) + "\n")
if path == "user/repos":
    done(listed(w["repos_pages"]))
if path.startswith("repos/"):
    done(json.dumps(w["repo"]) + "\n")
done(err=f"假 gh 不认识路径：{path}\n", code=2)
