---
name: codex
description: "Delegate coding to the OpenAI Codex CLI (implement features, fix bugs, refactor, write tests, open PRs) by driving `codex exec` non-interactively through the bash tool. Use whenever a bounded, self-contained engineering task can be handed off end-to-end to an autonomous coding agent — especially larger multi-file changes, or when you want a second agent to do the编码 while you orchestrate. Covers install/auth, the exec flag set (model, sandbox, cwd, add-dir, output-last-message, json), resuming a session, and the sandbox/approval safety model."
category: autonomous-ai-agents
---

# Codex CLI 委派编码（`codex exec`）

用本 skill 把一段**有边界、能独立完成**的编码任务（加功能、修 bug、重构、写测试、开 PR）
整段交给 OpenAI 的 **Codex CLI** 跑完再拿回结果。Codex 是外部 Rust/npm CLI，所有操作都通过
`bash` 工具跑 `codex exec`（非交互模式）——本 skill **不封装 Python tool**，就是一套调用配方。

Reply in Chinese unless the user clearly uses another language.

## 何时使用

- 任务是**自包含的工程活**：实现一个功能、修一个 bug、重构一个模块、补测试、开 PR，
  且你能把「做什么、改哪些文件、怎么验证」讲清楚。
- 你想让**另一个自主编码 agent** 去干活、自己专注编排；或者改动跨多文件、体量偏大，
  适合整段委派而不是逐行自己写。

**不用本 skill：**
- 小改动 / 你自己能直接改 → 用 `read` / `write` / `edit`。
- 只是跑命令、看输出 → 用 `bash` / `powershell`。
- 想委派给 Anthropic 的 Claude Code 而非 OpenAI Codex → 用 `claude_code` tool。

## 安全模型（最高优先级）

`codex exec` 是**非交互**的：它跑到底、中途不会停下来问你要不要批准某个操作。因此
「它能碰什么」完全由 **sandbox** 决定，务必按任务最小授权：

- **`--sandbox workspace-write`（默认，首选）**：只能改工作目录内的文件，不能联网。
  纯本地编辑/重构/写测试用这个就够。
- **`--sandbox read-only`**：只读。让 Codex 分析/审查代码而不改动时用。
- **`--sandbox danger-full-access`**：解除限制（可联网、可跑任意命令）。**仅**在任务确实需要
  网络或无人值守跑 git/gh（如推分支、开 PR）时才用，且目标仓库必须可信。
- **`--dangerously-bypass-approvals-and-sandbox`**：完全绕过审批与沙箱，比上一条更危险，
  只在完全受控的 CI/一次性场景用。**用它就别再带 `--sandbox`**（冲突）。

其它安全约定：
- **凭据是 CLI 自己的事**，跟 `gh` 一样：Codex 用它自己存的 ChatGPT/Codex 登录，或环境变量
  `OPENAI_API_KEY`。本 skill **不打印、不写入、不提交任何 token**。
- **委派 ≠ 免检**：Codex 跑完后**先 `git diff` 复查它产生的改动**再决定是否提交/推送，
  别盲目信任。开 PR/推分支这类外发操作，按仓库规矩确认后再让它做。
- 破坏性/大范围操作（删文件、force-push、改生产配置）不要直接丢给 `danger-full-access`
  让它无人值守跑——先让它在 workspace-write 下产出改动，你复查后再动手。

## Setup（首次或报错时）

Codex 是外部 CLI，不是 pip 包。缺失时 `bash` 会报 `codex: command not found`——照实转达用户，
别假装成功。安装 + 认证是**用户级**操作，agent 只引导：

```bash
codex --version 2>&1 || echo "codex 未安装"     # 先探一下装没装
npm install -g @openai/codex                     # 安装（需 Node.js）；或 brew install codex
codex login                                       # 首次认证：走 ChatGPT 登录，或设 OPENAI_API_KEY
codex exec --help 2>&1 | head -40                 # 查当前版本的确切 flag（见下方版本差异）
```

Windows 上可执行是 `codex.CMD` 之类的 shim，`bash` 工具能正常找到并调用（走 shell）。

**stdin 提示**：`codex exec` 即使已用位置参数给了 prompt，检测到 stdin 非 tty 时仍会打印
`Reading additional input from stdin...` 并尝试读取。通过 `bash` 工具调用时给命令重定向
`</dev/null`（如 `codex exec ... "prompt" </dev/null`）可避免它挂着等输入。

## 核心工作流

1. **想清楚再委派**：把任务写成一段明确的 prompt——目标、要改哪些文件、约束、**怎么验证**
   （跑哪个测试 / build）。边界越清楚，Codex 一趟跑完的成功率越高。
2. **在仓库根跑 `codex exec`**，按最小授权选 sandbox：

   ```bash
   # 纯本地编辑：默认 workspace-write，把最终消息落到文件方便读回
   codex exec -C /path/to/repo --sandbox workspace-write \
     -o /tmp/codex_out.txt \
     "在 src/api.py 加一个 GET /health 端点返回 {status:'ok'}，并在 tests/test_api.py 补一个测试，最后跑 pytest 确认通过" </dev/null
   cat /tmp/codex_out.txt        # 读回 Codex 的最终消息
   ```
3. **复查再落地**：`git -C /path/to/repo diff` 看它改了什么，满意再 commit（或让它开 PR）。
4. **要开 PR / 推分支**（需要网络 + git）：升到 `danger-full-access` 并按需 `--skip-git-repo-check`：

   ```bash
   codex exec -C /path/to/repo --sandbox danger-full-access \
     "实现 X、跑测试通过后，新建分支 feat/x、提交、推送并用 gh 开一个 PR" </dev/null
   ```

## Flag 速查（`codex exec [OPTIONS] [PROMPT]`）

Prompt 是**最后的位置参数**，放在所有 flag 之后（用 `-` 可从 stdin 读）。

| Flag | 作用 |
|------|------|
| `-C, --cd <dir>` | 工作目录（一般是仓库根）。 |
| `-s, --sandbox <mode>` | `read-only` / `workspace-write`（默认）/ `danger-full-access`。 |
| `--dangerously-bypass-approvals-and-sandbox` | 完全绕过沙箱+审批（最危险，别再带 `--sandbox`）。 |
| `-m, --model <name>` | 指定模型（如 `gpt-5.2`、`o4-mini`）；不给用 CLI 默认。 |
| `--skip-git-repo-check` | 目录不是 git 仓库时也允许运行。 |
| `--add-dir <dir>` | 额外可写目录（可重复）。 |
| `-i, --image <file>...` | 给 prompt 附图（可给多个，空格分隔）。 |
| `-o, --output-last-message <file>` | 把**最终消息**写到文件——取结果最干净的方式。 |
| `--output-schema <file>` | 用 JSON schema 约束最终输出结构。 |
| `--json` | 输出 **JSONL 事件流**（`AgentMessage` / `FileChange` / `TurnComplete` 每行一个 JSON），供程序解析。 |

**取结果建议**：优先 `-o <file>` 拿最终消息（干净）；需要逐事件解析进度/改动时才 `--json`
（注意它是多行 JSONL，不是单个 JSON，要逐行 parse）。

## 迭代同一个任务（resume）

Codex 的续跑是**子命令**，不是 flag：

```bash
codex exec resume --last "继续把边界情况的单测补上"     # 续最近一次 session
codex exec resume <session-id> "改用参数化测试重写"      # 按 session id 续
```

`--last` / `<session-id>` 后可以直接跟新 prompt，在同一 session 上下文里接着干。
（续跑时别再带一堆配置 flag，除非确实要改——继承原 session 的设置更稳。）

## 版本差异与排错

Codex CLI 迭代快，flag 名/子命令可能随版本变。动手前用 `codex exec --help` 核对当前版本，
别照记忆硬套。

| 症状 | 原因 / 处理 |
|------|-------------|
| `codex: command not found` | 未安装 → 走 Setup，引导用户 `npm i -g @openai/codex`。 |
| `401 Unauthorized` (websocket) / 反复 `Reconnecting...` | 未登录或 key 失效 → 用户跑 `codex login`（浏览器 OAuth），或设 `OPENAI_API_KEY`；`codex login status` 查状态。本 skill 不代管凭据。 |
| 命令挂着不动 / `Reading additional input from stdin...` | stdin 非 tty 时会等输入 → 命令末尾加 `</dev/null`。 |
| `not a git repository` 类报错 | 目录非 git 仓库 → 加 `--skip-git-repo-check`。 |
| 改动没落盘 / 说没权限写 | sandbox 太紧（如 read-only）→ 升到 `workspace-write`。 |
| 联网/推送被拒 | workspace-write 不给网络 → 需要外发时才升 `danger-full-access` 并复查。 |
| 拿不到结果文本 | 忘了 `-o <file>` 再 `cat`；或想要事件流却没加 `--json`。 |

## 与 `claude_code` 的区别

两者都是「委派编码给外部 CLI」，选型看你要哪个供应商：`codex`=OpenAI Codex（本 skill，
`codex exec`，权限靠 sandbox），`claude_code`=Anthropic Claude Code（那个是 tool，`claude -p`，
权限靠 permission-mode）。功能定位一致，都归 autonomous-ai-agents。

