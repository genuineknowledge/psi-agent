---
name: claude-code
description: "Delegate a self-contained coding task to Anthropic's Claude Code CLI running headless (`claude -p`): implement a feature, fix a bug, refactor, write tests, or open a pull request end-to-end, then hand the result back. Use whenever the user asks to build/fix code in a repo and wants it done autonomously by a coding agent, to offload a bounded engineering task, or to iterate on a previous delegated task via its session id. Covers install/auth, the headless print-mode flags, permission modes (acceptEdits vs bypassPermissions for unattended git/PR work), resume/continue, and how to read the JSON result. Runs entirely through the `bash` tool — no extra Python dependency."
category: autonomous-ai-agents
---

# Delegate Coding to Claude Code CLI

Use this skill to hand a **bounded, self-contained coding task** to Anthropic's
[Claude Code](https://code.claude.com/docs/en/overview) CLI running non-interactively
(`claude -p`, "print" mode). Claude Code is itself an autonomous coding agent: give it a
clear spec and a working directory, and it will read files, edit code, run commands, and
(when allowed) commit and open a PR — then return the result plus a resumable session id.

Everything here runs through the **`bash` tool**. Claude Code is an external Node/npm CLI,
not a Python package, so there is nothing to import — you shell out to `claude`.

Reply in Chinese unless the user clearly uses another language.

## 什么时候用这个 skill

- 用户要在某个仓库里**实现一个功能 / 修 bug / 重构 / 补测试**,并希望由一个编码 agent 自主完成。
- 想把一个**边界清晰的工程任务**整体委派出去(而不是自己一步步在当前会话里改)。
- 要**开 PR**:建分支、提交、`gh` 开 PR 一条龙。
- 要**接着上一次委派继续迭代**(用返回的 session id resume)。

不适合:开放式、边界不清、需要和用户来回确认的任务 —— headless 模式不能中途提问。先把需求收敛清楚再委派。

## 前提:安装与认证

`claude` 是外部 CLI,通过 npm 全局安装(需要 Node.js):

```bash
npm install -g @anthropic-ai/claude-code
claude --version   # 确认已装,例如 2.1.x (Claude Code)
```

认证是 CLI 自己的事(和 haitun 的 `PSI_AI_*` 模型配置**互相独立**):

- 交互式登录:先跑一次 `claude`(裸命令)走浏览器登录并存下凭证;之后 headless 调用复用它。
- 或用环境变量:`export ANTHROPIC_API_KEY=sk-...`(适合无人值守/CI)。
- 验证:任何一个只读的 headless 调用能返回结果就说明认证 OK。

如果 `bash` 里 `claude` 找不到,提示用户装 Node.js + 上面的 npm 命令;不要试图自动装(需要用户同意的系统级操作)。

## ⚠️ 权限与安全模型(最重要)

Headless 模式**无法中途向用户请求权限**,所以权限策略必须在启动时一次给定,用 `--permission-mode`:

| 模式 | 行为 | 何时用 |
|---|---|---|
| `plan` | 只读、只做计划,不改文件、不跑命令 | 先探索/评估,或做只读分析 |
| `acceptEdits` | **默认建议**。自动改文件,但不无脑跑任意命令 | 纯代码编辑、写测试等 |
| `bypassPermissions` | 全部放行,无人值守跑任意 shell/git/gh | 开 PR、必须跑 git 的流程 |

`--dangerously-skip-permissions` 等价于 `bypassPermissions`。

**安全底线:**
- `bypassPermissions` 会在目标目录里**无人值守执行任意命令**。只在你信任的仓库、且任务确实需要跑 git/gh 时才用。测试新流程时先指向一个临时空目录。
- 委派完成后**审查它产生的 diff/commit** 再决定是否合入。
- 委派 prompt 里不要塞进密钥明文;认证走 CLI 自己的登录态或 `ANTHROPIC_API_KEY`。

## 核心工作流

在**目标仓库目录**里跑(用 `bash` 工具的 `cd` 或让命令带 `cwd`)。基本形态:

```bash
claude -p "<清晰的任务规格>" --output-format json --permission-mode acceptEdits
```

写 prompt 的要点:说清**目标、涉及哪些文件、约束、怎么验证**(跑什么测试/构建)。越具体,一次成功率越高。

### 例:实现一个功能

```bash
cd /path/to/repo
claude -p "在 src/api.py 新增一个 /health GET 端点,返回 {\"status\":\"ok\"};\
并在 tests/test_api.py 补一个测试,最后跑 pytest 确认通过。" \
  --output-format json --permission-mode acceptEdits
```

### 例:开一个 PR(需要 bypassPermissions)

```bash
cd /path/to/repo
claude -p "新建分支 feat/readme-intro,在 README 顶部加一行项目简介,\
commit 后用 gh 开一个 PR,base 为 main。" \
  --output-format json --permission-mode bypassPermissions
```

## 常用 flag 速查(headless / print 模式)

| Flag | 作用 |
|---|---|
| `-p, --print "<prompt>"` | 非交互执行,prompt 作为 `-p` 的值 |
| `--output-format text\|json\|stream-json` | 输出格式。`json` 结构化(含 session_id/成本);`text` 纯文本 |
| `--model <alias\|name>` | 选模型,别名 `opus`/`sonnet`/`haiku`/`fable` 或完整名 |
| `--permission-mode <mode>` | 见上表:`plan`/`acceptEdits`/`bypassPermissions` 等 |
| `--allowed-tools "<rule>"...` | 免确认放行的工具规则,如 `"Bash(git log *)"` `Read` |
| `--disallowed-tools "<rule>"...` | 拒绝规则,同样语法 |
| `--add-dir <path>...` | 额外允许读写的目录(可多次) |
| `--max-turns <n>` | 限制 agent 回合数,超了报错退出;省略=不限 |
| `--append-system-prompt "<text>"` | 往系统提示追加约束,如 "Always use TypeScript" |
| `--resume <id>` / `-c, --continue` | 恢复某个会话 / 继续当前目录最近的会话 |

`--allowed-tools` 里带空格的规则要整体加引号,例如 `"Bash(git push *)"` 是一个 token。

## 迭代同一个任务(resume)

`--output-format json` 的返回里带 `session_id`。要在同一上下文里继续(比如"刚才那个功能再加个求幂函数"),把它传给 `--resume`:

```bash
claude -p "再给 calculator 加一个 power(base, exp) 函数和对应测试。" \
  --output-format json --permission-mode acceptEdits \
  --resume 9ecea103-fcd8-48db-aca7-a6d5fa6ee6a6
```

或 `--continue` 直接续目标目录里最近的一次会话(不用记 id)。

## 读取结果

`--output-format json` 返回一个 JSON 对象,重点字段:

- `result` — 最终给用户的文本(做了什么、结论)。
- `session_id` — 传给 `--resume` 继续迭代。
- `num_turns` / `total_cost_usd` — 用了几个回合、花了多少钱。
- `is_error` — 是否出错。

用 `bash` 拿到 stdout 后,把 `result` 讲给用户,并**保留 `session_id`** 以备后续迭代。需要机器解析时可 `... | python -c "import sys,json;d=json.load(sys.stdin);print(d['result']);print(d['session_id'])"`。

退出码非 0 时,看 stderr:常见是认证失败(登录/`ANTHROPIC_API_KEY`)、`--max-turns` 撞上限、或目标目录不存在。

## 排错

- **`claude: command not found`** — 没装或不在 PATH。装 Node.js + `npm i -g @anthropic-ai/claude-code`。
- **认证错误 / 未登录** — 先跑一次交互式 `claude` 登录,或设 `ANTHROPIC_API_KEY`。
- **它没改文件** — 多半 `--permission-mode` 是 `plan`(只读);改任务要用 `acceptEdits`。
- **git/gh 步骤没执行** — 需要 `bypassPermissions`;`acceptEdits` 不会无脑跑命令。
- **中途卡住等待输入** — 你漏了 `-p`(进了交互模式)。headless 必须带 `-p`。

## 与其他委派 skill 的区别

- **claude-code**(本 skill):委派给 Anthropic Claude Code CLI,prompt 走 `-p`,权限用 `--permission-mode`,迭代用 `--resume <id>` / `--continue`。
- **codex**:委派给 OpenAI Codex CLI(`codex exec`),prompt 是最后的位置参数,权限用 `--sandbox`,迭代用 `codex exec resume`。任务需求相同、只是用哪个厂商的编码 agent 不同时,二选一即可。
