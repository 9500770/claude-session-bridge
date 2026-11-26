#!/usr/bin/env python3
"""在假 HOME 里验证 csess-sync 的登记判定（不碰真实机器）

重点覆盖两个真机踩出来的时序坑：
  1. SessionStart 早于转录落盘 → 应改用 hook 传入的 cwd 兜底登记，而不是直接跳过
  2. 模板里的 folderPlace / originCwd 属于别的目录 → 必须按真实 cwd 重建，否则会话
     会落进 Desktop 的"缺失文件夹"分组
顺带确认单向守卫：Desktop 自建会话、`claude -p`（sdk-cli）都不登记。
"""
import glob, json, os, shutil, subprocess, sys, tempfile

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
hook = os.path.join(repo, "bin/csess-sync")
fails = 0


def fresh_home():
    """造一个只有模板记录的假 HOME。"""
    home = tempfile.mkdtemp(prefix="csb-sync-")
    reg = os.path.join(home, "Library/Application Support/Claude-3p/claude-code-sessions/acct/ws")
    os.makedirs(reg, exist_ok=True)
    os.makedirs(os.path.join(home, ".claude/projects/-tmp-proj"), exist_ok=True)
    tpl = {"sessionId": "local_tpl", "cliSessionId": "deadbeef", "title": "模板",
           "cwd": "/tmp/other", "originCwd": "/tmp/other",
           "folderPlace": {"cwd": "/tmp/other"},
           "model": "sonnet-mm-2.5", "createdAt": 1, "lastActivityAt": 1}
    json.dump(tpl, open(os.path.join(reg, "local_tpl.json"), "w"))
    return home, reg


def transcript(home, sid, entrypoint):
    p = os.path.join(home, ".claude/projects/-tmp-proj", sid + ".jsonl")
    with open(p, "w") as f:
        for rec in ({"type": "user", "cwd": "/tmp/proj", "entrypoint": entrypoint,
                     "message": {"content": "hello from " + entrypoint}},
                    {"type": "assistant", "message": {"content": "ok"}}):
            f.write(json.dumps(rec) + "\n")
    return p


def run(home, payload, args=(), extra_env=None):
    env = dict(os.environ, HOME=home, CSESS_SYNC_WAIT_STEPS="0")
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, hook] + list(args), input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, (r.stdout + r.stderr).strip()


def registered(reg):
    """返回 cliSessionId -> 记录（排除模板自身）。"""
    out = {}
    for p in glob.glob(os.path.join(reg, "local_*.json")):
        d = json.load(open(p))
        if d.get("cliSessionId") and d.get("sessionId") != "local_tpl":
            out[d["cliSessionId"]] = d
    return out


def case(name, cond, extra=""):
    global fails
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fails += 1


# 1) 转录未落盘 + cwd → 兜底登记，且文件夹位置按真实 cwd
home, reg = fresh_home()
sid = "11110000-0000-0000-0000-000000000001"
code, out = run(home, {"session_id": sid, "transcript_path": "/nonexistent/x.jsonl",
                       "cwd": "/tmp/proj/real", "hook_event_name": "SessionStart"})
recs = registered(reg)
print("\n== 1) 转录未落盘时要按 cwd 兜底登记")
print(f"    exit={code}  records={list(recs)}")
case("兜底登记成功", sid in recs)
if sid in recs:
    r = recs[sid]
    case("cwd == hook 给的 cwd", r.get("cwd") == "/tmp/proj/real", r.get("cwd"))
    case("originCwd 按真实 cwd 重建", r.get("originCwd") == "/tmp/proj/real", r.get("originCwd"))
    case("folderPlace 不继承模板", (r.get("folderPlace") or {}).get("cwd") == "/tmp/proj/real",
         json.dumps(r.get("folderPlace"), ensure_ascii=False))
    case("folderPlace 没有 lost 标记", not (r.get("folderPlace") or {}).get("lost"))
    case("临时标题可后续补全", str(r.get("title", "")).startswith("CLI session "), r.get("title"))
    case("lastActivityAt 已写入", bool(r.get("lastActivityAt")))
shutil.rmtree(home, ignore_errors=True)

# 2) 转录已落盘 entrypoint=cli → 正常登记，标题取首条用户消息
home, reg = fresh_home()
sid = "22220000-0000-0000-0000-000000000002"
p = transcript(home, sid, "cli")
print("\n== 2) 终端 cli 会话应登记")
code, out = run(home, {"session_id": sid, "transcript_path": p, "cwd": "/tmp/proj",
                       "hook_event_name": "SessionStart"})
recs = registered(reg)
case("登记成功", sid in recs, str(list(recs)))
if sid in recs:
    case("标题取转录内容", "hello from cli" in str(recs[sid].get("title")), recs[sid].get("title"))
shutil.rmtree(home, ignore_errors=True)

# 3) 转录已落盘 entrypoint=sdk-cli → 不登记
home, reg = fresh_home()
sid = "33330000-0000-0000-0000-000000000003"
p = transcript(home, sid, "sdk-cli")
print("\n== 3) claude -p（sdk-cli）不该进 Desktop 列表")
run(home, {"session_id": sid, "transcript_path": p, "cwd": "/tmp/proj",
           "hook_event_name": "SessionStart"})
recs = registered(reg)
case("未登记", not recs, str(list(recs)))
log = open(os.path.join(home, ".csess/hook.log")).read()
case("日志说明原因", "sdk-cli" in log)
shutil.rmtree(home, ignore_errors=True)

# 4) 转录未落盘但环境变量自报是 Desktop → 不登记（防反向污染）
home, reg = fresh_home()
sid = "44440000-0000-0000-0000-000000000004"
print("\n== 4) Desktop 会话（env 自报）不该被反向登记")
run(home, {"session_id": sid, "transcript_path": "/nonexistent/y.jsonl",
           "cwd": "/tmp/proj", "hook_event_name": "SessionStart"},
    extra_env={"CLAUDE_CODE_ENTRYPOINT": "claude-desktop-3p"})
recs = registered(reg)
case("未登记", not recs, str(list(recs)))
shutil.rmtree(home, ignore_errors=True)

# 5) SessionEnd 在对账时补全已登记记录的标题与轮数
home, reg = fresh_home()
sid = "55550000-0000-0000-0000-000000000005"
print("\n== 5) SessionEnd 应补全标题与轮数")
run(home, {"session_id": sid, "transcript_path": "/nonexistent/z.jsonl",
           "cwd": "/tmp/proj/late", "hook_event_name": "SessionStart"})
p = transcript(home, sid, "cli")
run(home, {"session_id": sid, "transcript_path": p, "cwd": "/tmp/proj/late",
           "hook_event_name": "SessionEnd"}, args=["--end"])
recs = registered(reg)
case("记录还在（没被误清）", sid in recs)
if sid in recs:
    case("标题补全", "hello from cli" in str(recs[sid].get("title")), recs[sid].get("title"))
    case("轮数 >= 1", (recs[sid].get("completedTurns") or 0) >= 1, str(recs[sid].get("completedTurns")))
shutil.rmtree(home, ignore_errors=True)

# 6) install.sh 的自检事件（--dry）不能留下登记，否则每条机器都多一条垃圾会话
home, reg = fresh_home()
sid = "install-selftest-0000"
print("\n== 6) 安装自检（--dry）不该写任何登记")
code, out = run(home, {"session_id": sid, "transcript_path": "/nonexistent.jsonl",
                       "cwd": "/tmp/proj", "hook_event_name": "SessionStart"}, args=["--dry"])
recs = registered(reg)
case("没有留下登记", not recs, str(list(recs)))
log = open(os.path.join(home, ".csess/hook.log")).read()
case("日志里是 DRY 而不是 registered", "DRY" in log and "registered" not in log)
shutil.rmtree(home, ignore_errors=True)

# 7) 兜底登记后才发现是跑批会话 → SessionEnd 要撤掉自己建的那条
home, reg = fresh_home()
sid = "77770000-0000-0000-0000-000000000007"
print("\n== 7) 兜底登记后发现是 sdk-cli，应撤掉")
run(home, {"session_id": sid, "transcript_path": "/nonexistent/w.jsonl",
           "cwd": "/tmp/proj", "hook_event_name": "SessionStart"},
    extra_env={"CLAUDE_CODE_ENTRYPOINT": ""})   # 模拟 CLI：env 没自报
case("先兜底登记成功", sid in registered(reg))
p = transcript(home, sid, "sdk-cli")
run(home, {"session_id": sid, "transcript_path": p, "cwd": "/tmp/proj",
           "hook_event_name": "SessionEnd"}, args=["--end"])
case("SessionEnd 后已撤掉", sid not in registered(reg), str(list(registered(reg))))
managed = json.load(open(os.path.join(home, ".csess/managed.json"))) if os.path.exists(
    os.path.join(home, ".csess/managed.json")) else {}
case("记账也清干净", sid not in managed)
shutil.rmtree(home, ignore_errors=True)

print("\n结果:", "ALL PASS" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
