#!/usr/bin/env bash
# claude-session-bridge — 一键安装
#   ./install.sh            安装（幂等，可重复运行）
#   ./install.sh --dry      只检查环境，不改任何文件
#   curl -fsSL https://raw.githubusercontent.com/9500770/claude-session-bridge/main/install.sh | bash
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
BIN="${HOME}/bin"
HOOK_BIN="${BIN}/csess-sync"
PUSH_BIN="${BIN}/csess-push"
SETTINGS="${HOME}/.claude/settings.json"
DRY=0
[[ "${1:-}" == "--dry" ]] && DRY=1

say() { printf '%s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

say "claude-session-bridge installer"
say "--------------------------------"

# 1. 环境检查
[[ "$(uname -s)" == "Darwin" ]] || die "只支持 macOS（依赖 Claude Desktop 的登记表路径）"
command -v python3 >/dev/null || die "需要 python3（macOS 自带或 brew install python3）"
say "✓ macOS $(sw_vers -productVersion) / $(python3 -V 2>&1)"

DESKTOP_DIR="${HOME}/Library/Application Support/Claude-3p"
[[ -d "$DESKTOP_DIR" ]] || say "! 还没发现 Claude Desktop 的数据目录，装完首次用 Code 视图后才会生成：$DESKTOP_DIR"

if [[ $DRY -eq 1 ]]; then
  say "✓ 环境满足。--dry 结束，未改动任何文件。"
  exit 0
fi

# 2. 安装脚本本体
mkdir -p "$BIN" "${HOME}/.csess"
if [[ -f "${SRC}/bin/csess-sync" ]]; then
  install -m 755 "${SRC}/bin/csess-sync" "$HOOK_BIN"
  install -m 755 "${SRC}/bin/csess-push" "$PUSH_BIN"
  say "✓ 已安装 $HOOK_BIN / $PUSH_BIN"
else
  # curl | bash 场景：没有本地 checkout，直接取两个脚本
  RAW="${CSB_RAW:-https://raw.githubusercontent.com/9500770/claude-session-bridge/main/bin}"
  curl -fsSL "${RAW}/csess-sync" -o "$HOOK_BIN" || die "下载 csess-sync 失败"
  curl -fsSL "${RAW}/csess-push" -o "$PUSH_BIN" || die "下载 csess-push 失败"
  chmod 755 "$HOOK_BIN" "$PUSH_BIN"
  say "✓ 已下载并安装到 $BIN"
fi

# 3. 注册 hook（幂等：只动自己那两条，其余原样保留；先备份）
python3 - "$SETTINGS" "$HOOK_BIN" <<'PY'
import json, os, shutil, sys, time
settings, hook = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(settings), exist_ok=True)
d = {}
if os.path.exists(settings):
    try:
        d = json.load(open(settings))
    except Exception:
        sys.exit("✗ settings.json 不是合法 JSON，已中止（未改动）")
    bak = settings + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(settings, bak)
    print("✓ 已备份 " + bak)
hooks = d.setdefault("hooks", {})
def strip(lst):
    out = []
    for grp in lst or []:
        inner = [h for h in grp.get("hooks", []) if "csess-sync" not in str(h.get("command", ""))]
        if inner:
            grp["hooks"] = inner
            out.append(grp)
    return out
start = strip(hooks.get("SessionStart"))
end = strip(hooks.get("SessionEnd"))
start.append({"hooks": [{"type": "command", "command": hook, "args": [], "async": True, "timeout": 15}]})
end.append({"hooks": [{"type": "command", "command": hook, "args": ["--end"], "timeout": 15}]})
hooks["SessionStart"] = start
hooks["SessionEnd"] = end
tmp = settings + ".tmp"
json.dump(d, open(tmp, "w"), indent=2, ensure_ascii=False)
os.replace(tmp, settings)
print("✓ hooks 已写入 SessionStart + SessionEnd")
PY

# 4. 自检：喂一条假事件，确认脚本能跑、不输出、不报错
say ""
say "自检（--dry，不写任何登记）："
EV='{"session_id":"install-selftest-0000","transcript_path":"/nonexistent.jsonl","cwd":"'"$HOME"'","hook_event_name":"SessionStart"}'
# CSESS_SYNC_WAIT_STEPS=0：自检的 transcript 永远不存在，别让它空等 8 秒
if OUT="$(printf '%s' "$EV" | CSESS_SYNC_WAIT_STEPS=0 "$HOOK_BIN" --dry 2>&1)"; then
  say "✓ csess-sync 正常退出"
else
  say "! csess-sync 退出异常，请看 ${HOME}/.csess/hook.log"
fi
BYTES="$(printf '%s' "$EV" | CSESS_SYNC_WAIT_STEPS=0 "$HOOK_BIN" --dry | wc -c | tr -d ' ')"
say "✓ stdout 字节数 = ${BYTES}（必须为 0，否则会污染模型上下文）"

say ""
say "安装完成。接下来："
say "  1) 在终端跑一次 claude（随便问一句），会话结束/开始时 hook 会自动登记"
say "  2) 重启 Claude Desktop，切到侧边栏 Code 视图查看"
say "  3) 补历史会话：csess-push --list 然后 csess-push <前缀>"
say "  4) 看日志：tail -f ~/.csess/hook.log"
say ""
say "卸载：./uninstall.sh     连登记记录一起清：./uninstall.sh --purge"
