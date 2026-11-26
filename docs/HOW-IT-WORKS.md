# 原理细节

本文记录实测数据，供后续适配 Desktop 版本升级时对照。

## 1. 两份数据，一份内容

| 路径 | 是什么 | 谁写 |
|---|---|---|
| `~/.claude/projects/<proj>/<session>.jsonl` | 真正的对话转录 | 终端 CLI、Desktop 引擎都会写 |
| `~/Library/Application Support/Claude-3p/claude-code-sessions/<account>/<ws>/local_<uuid>.json` | 侧边栏的**登记表**（索引），一行一个会话 | 只有 Desktop 写 |

侧边栏只渲染登记表里的条目。终端会话的转录一直在，但没有对应的登记行 → 不显示。

## 2. 登记文件的字段

实测一条 Desktop 自建的 Code 会话记录（`local_07bb64e9-*.json`）有 25 个字段，关键几个：

```json
{
  "sessionId": "local_07bb64e9-…",     // 登记 id，local_ 前缀
  "cliSessionId": "7a6442ff-…",        // ★ 指向真实转录的 session id
  "title": "随意闲聊",                  // 侧边栏显示的标题
  "cwd": "/Users/gm/workspace/shdlh",  // 归属项目/workspace
  "createdAt": 1795656000000,
  "lastActivityAt": 1795656000000,
  "folderPlace": …,
  "startedFromEnvironmentId": …,
  "gitAnchors": …,
  "turnBoxMounted": …,
  "turnBoxDeclared": …
}
```

结论：**`cliSessionId` 是桥的接点**。补一行带正确 `cliSessionId` 的记录，Desktop 就会把它当自己的会话列出并续接。

关键验证：手动补登记一条 `cliSessionId = 0f83f6f9` 后，Desktop 引擎进程的启动参数变成了 `--resume 0f83f6f9-678d-4024-9445-bdc35bc4ca09`，并在 `lastActivityAt` 上回写了 mtime —— 说明它确实打开了这条 CLI 会话。

为降低 schema 漂移风险，`csess-sync` / `csess-push` **不硬编码字段**，而是复制机器上最新一条真实记录当模板，只覆盖 `sessionId`、`cliSessionId`、`title`、`cwd`、`createdAt`、`lastActivityAt`。

## 3. Desktop 引擎的配置隔离（为什么不会双向冲突）

实测进程参数（Claude Desktop 6.28899.6，内置 claude 2.1.330）：

```
CLAUDE_CONFIG_DIR = /var/folders/36/.../T/claude-hostloop-plugins/<hash>
--setting-sources = user,project,local
--settings        = {"deniedMcpServers":[...]}     ← inline JSON，无 hooks
```

- 那个 `CLAUDE_CONFIG_DIR` 实测是**空目录**，没有 `settings.json`
- 全局 `~/.claude/settings.json` 里只放 `SessionStart/SessionEnd`，且 Desktop 的 `--settings` 不包含 hooks

所以：Desktop 的会话不会执行本 hook；它只是被登记的对象。

## 4. hook 输入

Claude Code 通过 stdin 传 JSON：

```json
{
  "session_id": "0f83f6f9-…",
  "transcript_path": "/Users/…/.claude/projects/-Users-gm-workspace-shdlh/0f83f6f9-….jsonl",
  "cwd": "/Users/gm/workspace/shdlh",
  "hook_event_name": "SessionStart"
}
```

脚本只依赖 `session_id` / `transcript_path` / `cwd`，缺失时从 `~/.claude/projects` 按前缀兜底查找。

## 5. stdout 与 token

`SessionStart` 的 stdout 会被注入模型上下文——那就是花 token。所以脚本第一件事：

```python
sys.stdout = open(os.devnull, "w")
```

安装脚本用一条假事件把 stdout 计入 `wc -c` 验证是否为 0。

## 6. 并发与清理

- 登记：`~/.csess/lock` 上 `fcntl.flock` 排他锁内完成「查重 + 读模板 + 写文件」，多进程并发安全
- 去重：按 `cliSessionId` 查已登记表，命中就跳过
- 清理（`--end` 会顺带跑）：只处理 `~/.csess/managed.json` 里记录的、自己建的 id；要求转录已消失且记录存在 ≥ 600 秒（`PRUNE_MIN_AGE`）才删，避免刚启动、转录还没落盘的会话被误删
- 异常：任何异常都写 `~/.csess/hook.log` 并 `exit 0`，绝不让 hook 拖挂用户会话

## 7. 实测性能

```
$ /usr/bin/time -p csess-sync --dry < event.json
real 0.03 / 0.02 / 0.02
```

`SessionEnd` 默认共享 1.5s 预算，本工具单次 20–30ms，留足余量；配置里仍显式写 `timeout: 15` 以覆盖默认限制。

## 8. 仍待解决的问题

- **侧边栏视图模式**：日志里 `sidebar_mode: 'cowork'` 时，在 cowork 视图看不到 Code 会话，需要手动切到 Code 视图
- **刷新时机**：新登记后必须重启 Desktop（或触发它重读登记表）才刷新
- **跨机**：本工具不解决 MacBook ↔ Mac mini 的会话共享；那需要同步 `~/.claude/projects` 与登记表两处

## 9. 单向守卫：靠 entrypoint 判来源

目标只有 CLI → Desktop 一个方向。判据不是猜环境变量，而是转录自己写的字段：

```
$ python3 -c "import json;print(json.loads(open(F).readline()).get('entrypoint'))"
```

实测（同一台机器、同一 workspace）：

| 会话 | entrypoint | 谁登记 |
|---|---|---|
| `684974f1` | `claude-desktop-3p` | Desktop 自己 |
| `7a6442ff` | `claude-desktop-3p` | Desktop 自己 |
| `0f83f6f9` | `cli` | **需要本工具** |
| `2dbe94b3` | `cli` | **需要本工具** |
| `c24b684f` | `cli` | **需要本工具** |

`entrypoint` 不是每行都有，`csess-sync` 扫前 40 行取第一个非空值；取不到才退回
`CLAUDE_CONFIG_DIR`（Desktop 引擎指向临时目录，CLI 用 `~/.claude`）。

为什么不只靠去重：Desktop 的记录都带 `cliSessionId`，去重能挡掉大部分；
但 Desktop 先开 hook 后写登记表时，去重来不及，就会多出一条重复条目。守卫在写之前，确定性更高。
