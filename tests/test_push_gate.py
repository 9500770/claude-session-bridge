#!/usr/bin/env python3
"""在假 HOME 里验证 csess-push 的 entrypoint 守卫（不碰真实机器）"""
import json, os, subprocess, sys, tempfile, shutil

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="csb-test-")
proj = os.path.join(tmp, ".claude/projects/-tmp-proj")
reg = os.path.join(tmp, "Library/Application Support/Claude-3p/claude-code-sessions/acct/ws")
os.makedirs(proj, exist_ok=True)
os.makedirs(reg, exist_ok=True)

# 模板记录（Desktop 自建样式）
tpl = {"sessionId": "local_tpl", "cliSessionId": "deadbeef", "title": "模板",
       "cwd": "/tmp", "model": "sonnet-mm-2.5", "createdAt": 1, "lastActivityAt": 1}
json.dump(tpl, open(os.path.join(reg, "local_tpl.json"), "w"))


def transcript(sid, entrypoint):
    p = os.path.join(proj, sid + ".jsonl")
    with open(p, "w") as f:
        for rec in ({"type": "user", "cwd": "/tmp/proj", "entrypoint": entrypoint,
                     "message": {"content": "hello from " + entrypoint}},
                    {"type": "assistant", "message": {"content": "ok"}}):
            f.write(json.dumps(rec) + "\n")
    return p


transcript("aaaa1111-0000-0000-0000-000000000001", "claude-desktop-3p")
transcript("bbbb2222-0000-0000-0000-000000000002", "sdk-cli")
transcript("cccc3333-0000-0000-0000-000000000003", "cli")

env = dict(os.environ, HOME=tmp)
push = os.path.join(repo, "bin/csess-push")
fails = 0


def run(args):
    r = subprocess.run([sys.executable, push] + args, capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


print("== 1) --list 应只列 sdk-cli / cli 两条")
code, out = run(["--list"])
print(out)
if "aaaa1111" in out:
    print("FAIL: Desktop 会话没被过滤"); fails += 1
if "bbbb2222" not in out or "cccc3333" not in out:
    print("FAIL: CLI 会话被误杀"); fails += 1

print("\n== 2) 登记 Desktop 会话应被拒绝（退出码非 0）")
code, out = run(["aaaa1111"])
print(f"exit={code}  {out}")
if code == 0:
    print("FAIL: Desktop 会话没被拒绝"); fails += 1

print("\n== 3) --force 应放行")
code, out = run(["aaaa1111", "--force", "--dry"])
print(f"exit={code}  {out.splitlines()[-1] if out else ''}")
if code != 0:
    print("FAIL: --force 没放行"); fails += 1

print("\n== 4) 登记 cli 会话应成功（--dry）")
code, out = run(["cccc3333", "--dry"])
print(f"exit={code}  {out.splitlines()[-1] if out else ''}")
if code != 0 or "未写入" not in out:
    print("FAIL: cli 会话登记失败"); fails += 1

shutil.rmtree(tmp, ignore_errors=True)
print("\n结果:", "ALL PASS" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
