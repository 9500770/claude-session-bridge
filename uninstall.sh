#!/usr/bin/env bash
# claude-session-bridge — 卸载
#   ./uninstall.sh            移除 hook 与 ~/bin 下的脚本（保留日志）
#   ./uninstall.sh --purge    额外删除本工具建的 Desktop 登记记录
set -euo pipefail

SETTINGS="${HOME}/.claude/settings.json"
BIN="${HOME}/bin"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

say() { printf '%s\n' "$*"; }

# 1. 从 settings.json 摘掉自己那两条
if [[ -f "$SETTINGS" ]]; then
  python3 - "$SETTINGS" <<'PY'
import json, os, shutil, sys, time
settings = sys.argv[1]
d = json.load(open(settings))
hooks = d.get("hooks", {})
changed = False
for ev in list(hooks):
    kept = []
    for grp in hooks.get(ev) or []:
        inner = [h for h in grp.get("hooks", []) if "csess-sync" not in str(h.get("command", ""))]
        if len(inner) != len(grp.get("hooks", [])):
            changed = True
        if inner:
            grp["hooks"] = inner
            kept.append(grp)
    if kept:
        hooks[ev] = kept
    else:
        hooks.pop(ev, None)
if changed:
    shutil.copy2(settings, settings + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    tmp = settings + ".tmp"
    json.dump(d, open(tmp, "w"), indent=2, ensure_ascii=False)
    os.replace(tmp, settings)
    print("✓ 已从 settings.json 移除 csess-sync hook")
else:
    print("· settings.json 里没有 csess-sync hook")
PY
fi

# 2. 删除脚本
for f in "${BIN}/csess-sync" "${BIN}/csess-push"; do
  [[ -e "$f" ]] && rm -f "$f" && say "✓ 已删除 $f"
done

# 3. 可选：清掉自己建的登记记录
if [[ $PURGE -eq 1 ]]; then
  python3 - <<'PY'
import json, os
home = os.path.expanduser("~")
man = os.path.join(home, ".csess/managed.json")
if not os.path.exists(man):
    print("· 没有 managed.json，无事可清")
    raise SystemExit
try:
    ids = json.load(open(man)).get("ids", [])
except Exception:
    ids = []
reg_root = os.path.join(home, "Library/Application Support/Claude-3p/claude-code-sessions")
n = 0
for sid in ids:
    for acct in os.listdir(reg_root) if os.path.isdir(reg_root) else []:
        for ws in os.listdir(os.path.join(reg_root, acct)):
            p = os.path.join(reg_root, acct, ws, sid + ".json")
            if os.path.exists(p):
                os.remove(p)
                n += 1
os.remove(man)
print(f"✓ 已删除 {n} 条登记记录")
PY
fi

say ""
say "卸载完成。已备份的 settings.json 仍在 ~/.claude/ 下（.bak-*）。"
say "日志保留在 ~/.csess/hook.log，可以手动删。"
