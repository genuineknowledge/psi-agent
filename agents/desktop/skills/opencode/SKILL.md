---
name: opencode
description: "Delegate coding to the OpenCode CLI (implement features, fix bugs, refactor, write tests, review PRs) by driving `opencode run` non-interactively through the bash tool. Use whenever a bounded, self-contained engineering task — or a read-only PR / code review — can be handed off end-to-end to an autonomous coding agent, especially larger multi-file changes or when you want a second agent to do the coding while you orchestrate. Covers install/auth, the `run` flag set (model, agent, format, session/continue/fork, auto, dir), the permission (allow/ask/deny) safety model, PR review via `opencode pr` and a read-only review agent, resuming a session, and troubleshooting."
category: autonomous-ai-agents
---

# OpenCode CLI 委派编码（`opencode run`）

用本 skill 把一段**有边界、能独立完成**的编码任务（加功能、修 bug、重构、写测试）
或一次**只读的 PR / 代码审查**整段交给 **OpenCode CLI** 跑完再拿回结果。OpenCode 是
SST 开源的 AI coding agent（终端 / 桌面 app / IDE 扩展，同 Claude Code、Codex 一类），
所有操作都通过 `bash` 工具跑 `opencode run`（非交互模式）——本 skill **不封装 Python
tool**，就是一套调用配方。

Reply in Chinese unless the user clearly uses another language.

## 何时使用

- 任务是**自包含的工程活**：实现一个功能、修一个 bug、重构一个模块、补测试，
  且你能把「做什么、改哪些文件、怎么验证」讲清楚。
- 要做**代码审查 / PR review**：让 OpenCode 拉一个 GitHub PR 分支、分析改动、
  给出意见，且**不落任何编辑**（用只读权限的审查 agent，见下）。
- 你想让**另一个自主编码 agent** 去干活、自己专注编排；或改动跨多文件、体量偏大，
  适合整段委派而不是逐行自己写。

**不用本 skill：**
- 小改动 / 你自己能直接改 → 用 `read` / `write` / `edit`。
- 只是跑命令、看输出 → 用 `bash` / `powershell`。
- 想委派给 OpenAI Codex → 用 `codex` skill；委派给 Anthropic Claude Code → 用 `claude_code` tool。

## 安全模型（最高优先级）

`opencode run` 是**非交互**的：它跑到底、不会停下来当面问你要不要批准某个操作。因此
「它能碰什么」由 **permission 配置**（每条规则解析成 `allow` / `ask` / `deny`）加上
命令行的 `--auto` 共同决定，务必按任务最小授权：

- **`--auto`**：自动批准所有**没被显式 `deny`** 的权限请求。非交互脚本里几乎必须带它，
  否则遇到 `ask` 的操作会卡住等不到人批。**它只影响本会**「否则要问」的请求，
  显式 `deny` 规则始终生效——所以真正的护栏是 permission 里的 `deny`。
- **permission 三态**（配在 `opencode.json` 或 agent frontmatter）：
  `edit`（改文件：edit/write/patch）、`bash`（跑 shell）、`webfetch`（抓 URL）
  各自可设 `allow` / `ask` / `deny`；`"*"` 设全局默认。规则**最后匹配的胜出**，
  常把 catch-all `"*"` 放最前、具体规则放后。
- **只读审查**：审查/分析不许改动时，用 `edit: deny`（配合 `bash: ask`、`webfetch: deny`），
  或直接跑一个 `mode: subagent` 的只读 review agent（见「PR / 代码审查」）。

其它安全约定：
- **凭据是 CLI 自己的事**，跟 `gh` 一样：OpenCode 把 provider key 存在
  `~/.local/share/opencode/auth.json`（`opencode auth login` 管）。本 skill
  **不打印、不写入、不提交任何 token**。
- **委派 ≠ 免检**：OpenCode 跑完后**先 `git diff` 复查它产生的改动**再决定是否提交/推送，
  别盲目信任。开 PR / 推分支这类外发操作，按仓库规矩确认后再让它做。
- 破坏性 / 大范围操作（删文件、force-push、改生产配置）不要直接丢给一个 `edit: allow`
  的 agent 无人值守跑——先让它在只读或受限权限下产出方案 / diff，你复查后再动手。

## Setup（首次或报错时）

OpenCode 是外部 CLI，不是 pip 包。缺失时 `bash` 会报 `opencode: command not found`——
照实转达用户，别假装成功。安装 + 认证是**用户级**操作，agent 只引导：

```bash
opencode --version 2>&1 || echo "opencode 未安装"     # 先探一下装没装
curl -fsSL https://opencode.ai/install | bash          # 官方安装脚本（最省事）
# 或：npm install -g opencode-ai / brew install anomalyco/tap/opencode
opencode auth login                                    # 配 provider + API key（存进 auth.json）
opencode run --help 2>&1 | head -40                    # 查当前版本的确切 flag（见下方排错）
```

Windows 官方建议在 **WSL** 里跑体验最好；原生装可用 npm / scoop / choco。
`bash` 工具调用 CLI 走 shell，能正常找到 shim。

## 核心工作流

1. **想清楚再委派**：把任务写成一段明确的 prompt——目标、要改哪些文件、约束、**怎么验证**
   （跑哪个测试 / build）。边界越清楚，OpenCode 一趟跑完的成功率越高。
2. **在仓库根跑 `opencode run`**，非交互务必带 `--auto`，用 `permission` 卡住不该做的事：

   ```bash
   # 纯本地编辑：--auto 自动过审批，--dir 指仓库根，--format json 便于脚本读回结果
   opencode run --dir /path/to/repo --auto --format json \
     "在 src/api.py 加一个 GET /health 端点返回 {status:'ok'}，并在 tests/test_api.py 补一个测试，最后跑 pytest 确认通过" \
     </dev/null
   ```
   不加 `--format json` 则输出人类可读的格式化文本；`json` 是原始 JSON 事件流，
   脚本里更好解析最终结果。
3. **复查再落地**：`git -C /path/to/repo diff` 看它改了什么，满意再 commit（或让它开 PR）。
4. **指定模型 / agent**：`-m provider/model` 选模型（`opencode models` 列可用的），
   `--agent <name>` 选 agent（如内置 `build` 或你定义的 review agent）。

## PR / 代码审查（只读）

两条路子：

**A. 拉现成的 GitHub PR 再审**——`opencode pr <number>` 会 checkout 该 PR 分支并跑 OpenCode：

```bash
# 先切到只读审查 agent，避免它动手改代码
opencode pr 123 --agent review --auto --format json \
  "审查这个 PR：找出 bug、安全问题、缺失的测试，按文件给出具体行级意见，不要改任何代码" \
  </dev/null
```

**B. 审当前工作区 / 某段 diff**——直接 `opencode run` 配只读 agent：

```bash
opencode run --dir /path/to/repo --agent review --auto \
  "审查 HEAD~1..HEAD 的改动，指出问题与改进点，不要编辑文件" </dev/null
```

只读 review agent 用一个 markdown agent 定义（放 `~/.config/opencode/agent/review.md`
或项目内 `.opencode/agent/review.md`）：

```markdown
---
description: Code review without edits
mode: subagent
permission:
  edit: deny
  bash: ask
  webfetch: deny
---
只分析代码、指出问题并给出修改建议，绝不修改任何文件。
```

`edit: deny` 是硬护栏——即便带了 `--auto` 也不会被放开（`--auto` 只放开「本会问」的，
不碰显式 `deny`）。也可临时用 `opencode agent create` 非交互建一个受限 agent。

## Flag 速查（`opencode run [message..]`）

Prompt 作为 message 位置参数传入。

| Flag | 作用 |
|------|------|
| `--dir <dir>` | 运行目录（一般是仓库根）。 |
| `--auto` | 自动批准非 `deny` 的权限请求（非交互脚本几乎必带）。 |
| `-m, --model <provider/model>` | 选模型（`opencode models` 列可用）。 |
| `--agent <name>` | 选 agent（如 `build` / 自定义 `review`）。 |
| `--format <default\|json>` | `default`=格式化文本，`json`=原始事件流（脚本用）。 |
| `-c, --continue` | 续最近一次 session。 |
| `-s, --session <id>` | 按 session id 续。 |
| `--fork` | 续 session 时 fork 一份（配合 `-c` / `-s`）。 |
| `-f, --file <path>` | 附带文件（可重复）。 |
| `--title <t>` | 设 session 标题。 |
| `--share` | 分享该 session（生成分享链接，注意别泄私有代码）。 |
| `--thinking` | 显示思考块。 |
| `--attach <url>` | 附到已跑的 `opencode serve` 实例，省 MCP 冷启动。 |

相关子命令：`opencode models [provider]`（列模型）、`opencode agent list`、
`opencode session list`、`opencode auth login/list/logout`、`opencode serve`（无头 HTTP 服务）。

## 迭代同一个任务（resume）

OpenCode 的续跑是 `run` 的 **flag**（不像 codex 是子命令）：

```bash
opencode run -c --auto "继续把边界情况的单测补上" </dev/null          # 续最近 session
opencode run -s <session-id> --auto "改用参数化测试重写" </dev/null    # 按 id 续
opencode run -c --fork --auto "在不改动原会话的前提下试另一种实现" </dev/null  # fork 一份
```

`opencode session list --format json` 可查历史 session id。续跑继承原 session 上下文，
别再重复贴一遍背景。

## 版本差异与排错

OpenCode CLI 迭代快，flag 名 / 子命令可能随版本变。动手前用 `opencode run --help` 核对
当前版本，别照记忆硬套。

| 症状 | 原因 / 处理 |
|------|-------------|
| `opencode: command not found` | 未安装 → 走 Setup，引导用户装（curl 脚本 / npm / brew）。 |
| 认证失败 / 没有可用模型 | 未配 provider → `opencode auth login`；`opencode auth list` 查状态、`opencode models` 看有没有模型。本 skill 不代管凭据。 |
| 命令挂着不动、等输入 | 通过 `bash` 管道调用时 stdin 非 tty 会等 → 命令末尾加 `</dev/null`。 |
| 一直卡在「等待批准」 | 非交互没带 `--auto` → 加 `--auto`（但真正该拦的用 permission `deny`）。 |
| 改动没落盘 / 说没权限写 | permission 里 `edit` 被 `deny`（或审查 agent） → 换 `build` agent 或把 `edit` 放开。 |
| 联网 / webfetch 被拒 | `webfetch: deny` → 需要时在 config / agent 里放开。 |
| 想省 MCP 冷启动 | 先 `opencode serve`，再 `opencode run --attach http://localhost:PORT ...`。 |

## 与 `codex` / `claude_code` 的区别

三者都是「委派编码给外部 CLI」，选型看你要哪个：`opencode`=SST OpenCode（本 skill，
`opencode run`，权限靠 permission `allow/ask/deny` + `--auto`，且原生有 `opencode pr` 做 PR review），
`codex`=OpenAI Codex（skill，`codex exec`，权限靠 sandbox），
`claude_code`=Anthropic Claude Code（tool，`claude -p`，权限靠 permission-mode）。
功能定位一致，都归 autonomous-ai-agents。
