# claude-session-bridge

**让 Claude Code 的终端会话出现在 Claude Desktop 侧边栏里。**

Claude Desktop 的侧边栏不扫 `~/.claude/projects`，只读它自己的一张会话登记表；而终端 `claude` 写转录、从不登记。
结果就是：同一台机器、同一份对话文件，Desktop 里却看不到终端开过的会话。

这个小工具在 `SessionStart` / `SessionEnd` 时补上那行登记。**不复制对话、不调模型、不占 token。**

```
$ ./install.sh
✓ macOS 15.x / Python 3.12.4
✓ 已安装 ~/bin/csess-sync / ~/bin/csess-push
✓ 已备份 ~/.claude/settings.json.bak-20261126-102507
✓ hooks 已写入 SessionStart + SessionEnd
✓ stdout 字节数 = 0
```

---

## 它到底是什么

一句话：**会话内容本来就是共享的，缺的只是一张对照表；本工具替 Desktop 把表里漏掉的那一行补上。**

```
                ~/.claude/projects/<proj>/<session>.jsonl
                   ↑                          ↑
          终端 claude 写                  Desktop 引擎读
                   │                          │
        需要一行登记才可见 ←────────┐         │
                                   │         │
   ~/Library/Application Support/Claude-3p/
     claude-code-sessions/<account>/<ws>/local_<uuid>.json
```

登记文件只是一个 16 字段的 JSON，`cliSessionId` 指向真实的转录。工具用你机器上已存在的一条记录当模板，保证字段结构完全一致。

## 安装

```bash
git clone https://github.com/9500770/claude-session-bridge
cd claude-session-bridge
./install.sh
```

或一行（无本地 checkout）：

```bash
curl -fsSL https://raw.githubusercontent.com/9500770/claude-session-bridge/main/install.sh | bash
```

脚本做的事，全部幂等、可重复运行：

1. 检查 macOS + python3
2. 把 `bin/csess-sync`、`bin/csess-push` 装到 `~/bin`
3. **备份** `~/.claude/settings.json`，再合并 hook（只动自己那两条，你已有的 hook 原样保留）
4. 自检：喂一条假事件，确认退出码正常、stdout 为 0 字节

## 验证

```bash
# 1. hook 真的被调用了吗
tail -f ~/.csess/hook.log

# 2. 在另一个终端跑一次 claude，随便问一句，然后看日志
#    IN SessionStart session=xxxxxxxx cwd=/path   ← 出现就说明接上了

# 3. 重启 Claude Desktop，侧边栏切到 Code 视图
```

补历史会话（hook 装之前开过的）：

```bash
csess-push --list          # 列出尚未登记的会话
csess-push 0f83f6f9        # 登记指定会话（前缀匹配）
```

## hook 配置长什么样

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command",
        "command": "/Users/you/bin/csess-sync", "args": [], "async": true, "timeout": 15 }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command",
        "command": "/Users/you/bin/csess-sync", "args": ["--end"], "timeout": 15 }] }]
  }
}
```

`SessionStart` 用 `async: true`（不阻塞你的第一次输入）；`SessionEnd` 同步但 timeout 15s（默认预算只有 1.5s，脚本实测 20–30ms 完成）。

## 已验证

macOS 27.2 / Python 3.12.4 上实测：

| 操作 | 结果 |
|---|---|
| `./install.sh` 重复运行两次 | hooks 仍是 2 条，不重复追加 |
| `./uninstall.sh` | `hooks: {}`，`~/bin` 下两个脚本已删 |
| 卸载后再 `./install.sh` | 完整恢复，2 条 hook |
| 自检 | 退出码 0，stdout = 0 字节 |
| 单次执行耗时 | 20–30 ms（`SessionEnd` 默认预算 1.5 s，余量充足）|

## 疑难

**`git clone` 报 `RPC failed; curl 28` / `Error in the HTTP2 framing layer`**

某些代理/VPN 下 GitHub 的 HTTP2 会断。加一条：

```bash
git -c http.version=HTTP/1.1 clone --depth 1 https://github.com/9500770/claude-session-bridge
```

或者干脆不用 git，走 `curl ... | bash` 那行。

**装完 `tail -f ~/.csess/hook.log` 一直空**

说明没有 CLI 会话在跑（hook 只在 `claude` 启动/结束时触发）。确认 `which -a claude` 有输出。

## 常见疑问

**Q：花 token 吗？**
不花。hook 是 harness 拉起的子进程，读 stdin JSON、写一个文件、退出，不进对话、不发给模型。
唯一会污染上下文的坑是 **stdout 会被注入模型上下文**，所以脚本把 stdout 重定向到 `/dev/null`，实测 0 字节；安装脚本会替你验这一条。

**Q：Desktop 和 CLI 会互相触发、形成循环吗？**
不会。Desktop 用独立的 `CLAUDE_CONFIG_DIR`（`/var/folders/.../T/claude-hostloop-plugins/<hash>`）和 inline `--settings`，**不读 `~/.claude/settings.json`**，所以 Desktop 侧会话不会执行这个 hook；它只是被登记的对象。
即便将来两边都执行，登记前按 `cliSessionId` 去重，同一会话只会有一行。

**Q：同时开多个 CLI 会话会打架吗？**
不会。每个会话的 hook 拿自己的 `session_id`，各写各的 `local_*.json`，文件名含随机 uuid，不会互相覆盖；读改写 `managed.json` 和清理动作都在 `~/.csess/lock` 的 `fcntl.flock` 里串行。

**Q：会删我的会话吗？**
只删**自己建的**、且**指向的转录已经消失**、且**存在超过 10 分钟**的记录（`--reconcile`）。你原有的记录一律不碰，记账在 `~/.csess/managed.json`。

**Q：`/bin/zsh` 里 `claude: command not found`？**
那是没装 CLI，跟本工具无关。本工具只在**有终端 `claude` 会话**时才有活干。确认：`which -a claude`。

## 卸载

```bash
./uninstall.sh            # 摘掉 hook + 删 ~/bin 下两个脚本
./uninstall.sh --purge    # 连同本工具建的登记记录一并删除
```

## 已知限制

- **只在 macOS** 上验证：路径依赖 `~/Library/Application Support/Claude-3p/`
- 新登记/删除后**需要重启 Claude Desktop**（或等它重读登记表）才会刷新侧边栏
- 侧边栏有 **cowork / Code 两种视图**，CLI 会话登记在 Code 视图下；`sidebar_mode: 'cowork'` 时看不到
- Desktop 版本升级可能改动登记表 schema；本工具用「已有记录当模板」的方式降低风险，但字段变更仍需适配
- 只**补登记**，不做跨机同步；两台机器之间要共享会话，得另行同步 `~/.claude/projects` 和登记表

## 原理细节

见 [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)：登记表字段、Desktop 引擎的启动参数、hook 输入样例、并发与清理规则。

## License

MIT
