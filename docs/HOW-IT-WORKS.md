# 原理细节

本文记录实测数据，供后续适配 Desktop 版本升级时对照。

## 1. 两份数据，一份内容

| 路径 | 是什么 | 谁写 |
|---|---|---|
| `~/.claude/projects/<proj>/<session>.jsonl` | 真正的对话转录 | 终端 CLI、Desktop 引擎都会写 |
| `~/Library/Application Support/Claude-3p/claude-code-sessions/<account>/<ws>/local_<uuid>.json` | 侧边栏的**登记表**（索引），一行一个会话 | 只有 Desktop 写 |

侧边栏只渲染登记表里的条目。终端会话的转录一直在，但没有对应的登记行 → 不显示。

## 2. 登记文件的字段

实测一条 Desktop 自建的 Code 会话记录（`local_07bb64e9-*.json`）有 18 个字段，关键几个：

```json
{
  "sessionId": "local_07bb64e9-…",     // 登记 id，local_ 前缀
  "cliSessionId": "7a6442ff-…",        // ★ 指向真实转录的 session id
  "title": "随意闲聊",                  // 侧边栏显示的标题
  "cwd": "/Users/gm/workspace/shdlh",  // 归属项目/workspace
  "model": "sonnet-mm-2.5",            // 宿主侧的模型位（不是真实模型名，见 §11）
  "createdAt": 1795656000000,
  "lastActivityAt": 1795656000000,
  "permissionMode": …,
  "originCwd": …,
  "isArchived": …,
  "enabledMcpTools": …,
  "sessionPermissionUpdates": …,
  "alwaysAllowedReasons": …,
  "remoteMcpServersConfig": …,
  "steeredByRemoteClient": …,
  "interruptedAwaitingPerson": …,
  "completedTurns": …,
  "titleSource": …
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

- 那个 `CLAUDE_CONFIG_DIR` 实测是**空目录**，没有 `settings.json`（只有 plugins/projects/sessions 之类的运行时目录）
- 全局 `~/.claude/settings.json` 里只放 `SessionStart/SessionEnd`，且 Desktop 的 `--settings` 不包含 hooks

所以：Desktop 的会话不会执行本 hook；它只是被登记的对象。

`--setting-sources user` 看着像会读 `~/.claude/settings.json`，但 `CLAUDE_CONFIG_DIR` 被改到临时目录后，「user 级配置」的落点也跟着换了目录 —— 这是它读不到你全局配置的根因。

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

实测（同一台机器、同一 workspace，2026-11-26）：

| 会话 | entrypoint | 处理 |
|---|---|---|
| `684974f1` | `claude-desktop-3p` | ⛔ Desktop 自己登记 |
| `7a6442ff` | `claude-desktop-3p` | ⛔ Desktop 自己登记 |
| `0f83f6f9` | `cli` | ✅ 需要本工具（终端交互） |
| `2dbe94b3` | `cli` | ✅ 需要本工具（终端交互） |
| `c24b684f` | `cli` | ✅ 需要本工具（终端交互） |
| `4bc0adb2` | `sdk-cli` | ⛔ 跳过（`claude -p` 跑批，不是人开的会话） |

守卫是**黑名单**：`entrypoint` 以 `claude-desktop` 开头、或等于 `sdk-cli`，就跳过。

> 早期版本写的是「只认 `cli`」，结果 `claude -p`（`sdk-cli`）全被挡掉 —— 而 print 模式恰恰是脚本/CI 里最常用的调用方式。这是真机跑出来才发现的。
> 后来反过来：print 模式是脚本跑批，一天几十条塞进侧边栏纯噪音，于是显式跳过 `sdk-cli`（`--force` 仍可强登）。

`entrypoint` 不是每行都有，`csess-sync` 扫前 40 行取第一个非空值；取不到才退回
`CLAUDE_CONFIG_DIR`（Desktop 引擎指向临时目录，CLI 用 `~/.claude`）。

为什么不只靠去重：Desktop 的记录都带 `cliSessionId`，去重能挡掉大部分；
但 Desktop 先开 hook 后写登记表时，去重来不及，就会多出一条重复条目。守卫在写之前，确定性更高。

日志里守卫的样子：

```
2026-11-26 10:41:07 [40121] skip 684974f1: Desktop 会话（entrypoint=claude-desktop-3p），单向只同步 CLI
2026-11-26 10:52:31 [40877] IN event=SessionStart sid=0f83f6f9 cwd=/Users/gm/workspace/shdlh dry=False
2026-11-26 10:52:31 [40877] registered 0f83f6f9 -> local_c91ab08a-….json
```

## 10. 时序：SessionStart 往往还没有转录

hook 在会话启动的瞬间被拉起，但 `claude` 是**先起进程、后写 jsonl**。实测 `SessionStart` 到达时
`transcript_path` 指向的文件还不存在（延迟 5–8 秒），早期版本只记一行 `skip …: 转录不存在` 就退出。

代价比看上去严重：**会话只要还开着就永远看不到**，因为 `SessionEnd` 要等退出才触发 ——
越是「正在用的会话」越不在侧边栏里。

现在是三级处理：

1. 有转录 → 直接登记。
2. 没转录 → 轮询等最多 8 秒（40 × 0.2s，测试里用 `CSESS_SYNC_WAIT_STEPS=0` 关掉），等到就正常登记。
3. 还是没等到 → 用 hook 带来的 `cwd` **兜底登记**：标题先写 `CLI session <sid 前 8 位>`，
   轮数与标题留给随后的 `SessionStart` / `SessionEnd` 补全。

```python
have = bool(jsonl and os.path.exists(jsonl))
if sid and not have:
    for _ in range(WAIT_STEPS):          # 每轮 0.2s
        time.sleep(0.2)
        ...                              # 补找 transcript_path / 按 sid 前缀找
if sid and have:                         # 正常路径：登记 + 刷新
    ...
elif sid and ev.get("cwd") and not desktop_by_env():
    register(sid, None, dry, ev.get("cwd"))   # 兜底登记
```

兜底分支必须先挡掉 Desktop：转录没落盘就读不到 `entrypoint`，这时看环境变量
`CLAUDE_CODE_ENTRYPOINT`（Desktop 侧是 `claude-desktop*`），否则会把 Desktop 自建会话反向登记成重复条目。

兜底登记的代价是**那一刻还看不出会话类型**（转录没落盘，读不到 `entrypoint`）。所以补了一条自愈：
等 `SessionEnd` 读到 `entrypoint=sdk-cli`，把本工具建的那条撤掉 —— `drop_managed()` 只动
`~/.csess/managed.json` 里记着自己的记录，Desktop 自建的记录一律不碰。日志：`drop 7a6442ff: 撤掉本工具建的登记`。

`SessionEnd` 仍然做兜底登记 + 刷新（`csess-sync --end`），两条 hook 都要装，缺哪条都会漏。
副作用是 `--end` 会顺带跑 `reconcile`，清理逻辑见 §6。

## 10.1 模板里的 folderPlace 不能照抄

登记记录靠「拿一条已有记录当模板」保证 schema 一致，但 `cwd` / `originCwd` / `folderPlace`
是**属于那条记录的目录的**。照抄的后果：新会话被塞进模板会话的目录；那个目录不存在的机器上，
侧边栏会把它归到「缺失文件夹」分组里（记录里 `lost: true`）。

修法：`build()` 按真实 `cwd` 重建这三个字段。

```python
real_cwd = cwd or cwd_hint or tmpl.get("cwd") or HOME
r["cwd"] = real_cwd
r["originCwd"] = real_cwd
r["folderPlace"] = {"cwd": real_cwd}
```

真机上还遇到过「模板文件夹存在但 `lost: true` 残留」的历史记录，用同样的判据就地修回即可。

## 11. 为什么单向足够，以及 Desktop 的「模型位」去哪了

**方向不对称的根源是共享目录。** 两份数据里转录是共享的（§1），只有登记表是 Desktop 私有的：

- 终端 → Desktop：缺登记行 → **看不到**，需要本工具
- Desktop → 终端：`claude --resume` 直接扫 `~/.claude/projects`，**本来就看得到**，不需要任何工具

所以「双向同步」既无必要，又会给同一会话制造两条记录。

**同一个隔离机制也解释了模型位。** `~/.claude/settings.json` 的 `env` 里配了：

```json
"ANTHROPIC_BASE_URL": "http://127.0.0.1:3425",
"ANTHROPIC_DEFAULT_SONNET_MODEL": "zcode/GLM-5.4[1m]",
"ANTHROPIC_DEFAULT_OPUS_MODEL":  "zcode/GLM-5.4[1m]",
"ANTHROPIC_DEFAULT_HAIKU_MODEL": "zcode/GLM-5.4[1m]"
```

但 Desktop 拉起的 CLI 进程（实测 `ps eww` 读环境）是：

```
ANTHROPIC_BASE_URL          = http://127.0.0.1:3425     ← 继承了
ANTHROPIC_AUTH_TOKEN        = ***                        ← 继承了（host auth）
ANTHROPIC_DEFAULT_HAIKU_MODEL  = (空)
ANTHROPIC_DEFAULT_SONNET_MODEL = (空)
ANTHROPIC_DEFAULT_OPUS_MODEL   = (空)
CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST = 1                 ← ★ 声明 provider 由宿主管
CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP = 1
CLAUDE_CODE_MODEL_CATALOG = 0
```

即 Desktop **有意清空三个模型位**，模型改由界面选择器决定，选择结果落盘在登记记录的
`model` 字段（例：`sonnet-mm-2.5`，这是宿主侧的模型位名，不是真实模型名）。

结论：模型位在 Desktop 里不是「没配」，而是「不由 settings.json 配」。实际请求仍然走同一个网关——
实测 Desktop 会话转录里的 `message.model` 是 `GLM-5.4`（CLI 会话是 `zcode/GLM-5.4`），说明路由后落到同一个后端，
只是模型名由宿主在请求时改写。想在 Desktop 里换模型，改界面选择器或网关侧映射，而不是 `~/.claude/settings.json`。
