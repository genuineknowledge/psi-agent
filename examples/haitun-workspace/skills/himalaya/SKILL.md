---
name: himalaya
description: Send, read, search and manage IMAP/SMTP email straight from the terminal via the `himalaya` CLI. LOAD whenever the task needs to read/list emails in a mailbox, search a mailbox, compose & send an email, reply/forward, save drafts, manage folders/flags, or download attachments over IMAP/SMTP. Not for iMessage/SMS (see apple-imessage), Telegram/Discord/Slack, or webmail-only APIs (Gmail API).
category: email
---

# Himalaya —— 终端里的 IMAP/SMTP 邮件（via `himalaya` CLI）

## 定义

用 [`himalaya`](https://github.com/pimalaya/himalaya)（Rust 写的跨平台命令行邮件客户端，
Linux / macOS / Windows 都能装）通过 `bash` 工具收发和管理邮件。它直接连你配好的
IMAP/SMTP 服务器，所以能列邮箱、列/读/搜信、发信、回复转发、存草稿、管文件夹与标记、
下附件。

| 你做 | Skill 教你怎么做 |
|------|------------------|
| 说明收件人 + 主题 + 正文 | 组 `himalaya message compose --to ... --subject ... --body ... --send` 经 `bash` 跑 |
| 要读某封信 | `himalaya envelope list -m INBOX --json` 找 id → `himalaya message read <id> --json` |
| 要搜邮箱 | `himalaya envelope search <query>`（需 IMAP SORT 能力） |
| 要机读输出 | 任意命令加 `--json`，用 `jq` 解析 |

**没有专用 tool**——全部经 `bash` 工具调用 `himalaya`（就像 apple-imessage 经 bash 调 `imsg`）。

## 前置条件

- **安装 `himalaya`**（用户级操作，agent 只引导，不代跑）：
  - Cargo：`cargo install himalaya`
  - Homebrew（mac/Linux）：`brew install himalaya`
  - 其它见项目 README 的预编译包。`himalaya --version` 验证装好了。
- **配置账户**。首次跑裸 `himalaya`（无配置时）会进交互向导，agent 环境下**别指望**能
  交互——请引导用户先自己配好，或给出配置文件让用户落盘。配置文件按顺序取第一个存在的：
  - `$XDG_CONFIG_HOME/himalaya/config.toml`
  - `$HOME/.config/himalaya/config.toml`
  - `$HOME/.himalayarc`
  - 用 `-c <PATH>` 覆盖；多个路径用 `:` 分隔（第一个是 base，其余深度合并）。
- **密码/令牌绝不明文写死。** 每个 `*.passwd` / `*.password` / `*.token` 字段既可给字面量，
  也可给一条打印密钥到 stdout 的 shell 命令——**优先用后者**（如 `pass show gmail`、
  读环境变量、系统 keyring）。README 明确说字面量形式"不该用于生产"。

最小 IMAP/SMTP 账户配置（Gmail 为例，密码走 `pass`）：

```toml
[accounts.gmail]
default = true

imap.server = "imaps://imap.gmail.com:993"
imap.sasl.plain.username = "example@gmail.com"
imap.sasl.plain.password.command = "pass show gmail"

smtp.server = "smtps://smtp.gmail.com:465"
smtp.sasl.plain.username = "example@gmail.com"
smtp.sasl.plain.password.command = "pass show gmail"

mailbox.alias.inbox = "INBOX"
```

- STARTTLS：加 `imap.starttls = true` / `smtp.starttls = true`；自定义证书 `imap.tls.cert`。
- Gmail 等需用**应用专用密码**（开了两步验证时），不是账户主密码——这一步引导用户去开。
- 配好后可用 `himalaya account configure <name>` 重新配，`himalaya account list` 看账户。
- 缺配置/连不上时 `himalaya` 会报错，**照实转达用户**，别假装成功。

## 安全规则（最高优先级）

- **发送前必须确认收件人、主题和正文。** 发邮件是外发、不可撤回的动作——先把
  「发给谁、主题、正文」复述给用户确认，得到明确同意再 `--send`。
- **不给来历不明的地址发信**，收件人拿不准先问用户，别猜。
- **附件先验证路径存在**（`ls -l <path>`）再带上，别发不存在或错的文件。
- **不刷屏、不群发轰炸**，连发多封之间自我节流；不做批量营销。
- **只管 IMAP/SMTP 邮件。** iMessage/SMS 归 [[psi-agent-imessage-tool]]（apple-imessage skill），
  Telegram/Discord/Slack 各归各的，别用这个 skill 处理。
- **凭据是机密。** 配置文件里的密码/令牌、读到的邮件内容都含隐私——读到什么**不要外传**，
  只按用户当前请求用；也别把密钥值回显进对话，按字段名引用。

## 核心命令

任意命令加 `--json` 得机读输出，用 `jq` 解析。设了 `[mailbox.alias] inbox` 后 `-m/--mailbox`
可省，回落到该别名。日志走 stderr，可 `--log-level debug` 排障、`NO_COLOR=1` 去色。

### 列邮箱 / 列信封（拿 id）

```bash
himalaya mailbox list --json | jq
himalaya envelope list -m INBOX --page 1 --json | jq   # 按日期降序的普通分页
```

信封字段：`id`、`message-id`、`flags`、`subject`、`from`、`to`、`date`、`size`、`has-attachment`。
**`message-id` 是跨邮箱稳定键**；`id` 是每邮箱局部的，copy/move 后会变——要长期引用某封信用
`message-id`，当次操作用 `id`。

### 搜索

```bash
himalaya envelope search from alice and after 2026-01-01 order by date desc
```

查询语法在 `date / after / from / to / subject / body / flag` 条件上用 `and` / `or` / `not` 组合，
再接 `order by date|from|to|subject [asc|desc]`。权威语法查 `himalaya envelope search --help`。
**注意**：search 编译成各后端原生搜索，IMAP 需服务器有 `SORT` 能力——**Gmail 目前会拒**，
Gmail 上改用 `himalaya gmail messages list -q "from:alice is:unread"`。

### 读信

```bash
himalaya message read 42          # 渲染 header + 文本正文
himalaya message read 42 --raw    # 原始 RFC 5322 字节
himalaya message read 42 --json   # 解析后的结构
```

读信**无副作用**（用 `BODY.PEEK`，不会置 `\Seen`）。要标已读得显式：
`himalaya flag add -m INBOX --flag seen 42`。

### 写信 / 发送（发送前先确认！）

简单场景直接用命令行 flag：

```bash
himalaya message compose --from me@example.org --to you@example.org \
    --subject "Hello" --body "Hi!" --send
```

复杂正文（多段/MIME/附件）用独立编排器 `mml` 管到 `message send` / `message add`：

```bash
mml compose /tmp/draft.eml && himalaya message send /tmp/draft.eml   # 显式临时文件
himalaya message read 42 | mml reply >(himalaya message send)        # 回复
```

> 坑：`mml compose | himalaya message send` 这种**裸管道会挂住**（编辑器 stdout 继承了管道）。
> 用显式临时文件或进程替换 `>(...)`，别用裸管道。

草稿与留底：

```bash
himalaya message add -m drafts --flag draft < message.eml   # 存草稿
himalaya message send --save sent < message.eml            # 发送并留一份到 sent
```

### 标记 / 移动 / 附件

```bash
himalaya flag add -m INBOX --flag seen 1:3,5        # 标已读（支持 1:3,5 这种区间/列表）
himalaya message copy --from INBOX --to Archives 42 # 复制到 Archives
himalaya attachment download -m INBOX 42            # 下附件
```

### 后端专属命令

各后端在自己的子组下暴露原生 API（`-b/--backend` 只影响上面的通用命令）：

```bash
himalaya imap raw 'SEARCH FROM "alice@example.com"'
himalaya gmail messages list -q "from:alice is:unread"
himalaya maildir create Archives
```

## 典型工作流：给某人回一封邮件

用户说「帮我回一下 Alice 那封关于发票的邮件，说这周五前处理」：

1. **定位邮件**——列/搜信封拿 `id`：
   ```bash
   himalaya envelope list -m INBOX --json | jq '.[] | select(.from | test("alice";"i"))'
   ```
   拿不准是哪封就把候选列给用户选，别猜。
2. **看原文**（避免答非所问）：`himalaya message read <id> --json | jq .`
3. **确认**——把「回给 Alice、主题、正文」复述给用户，等明确同意。
4. **回复**：
   ```bash
   himalaya message read <id> | mml reply >(himalaya message send --save sent)
   ```
   （或简单场景直接 `himalaya message compose --to alice@... --subject "Re: 发票" --body "..." --send`）
5. **复核**——`himalaya envelope list -m sent --page 1 --json | jq '.[0]'` 确认发出去了。

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `himalaya: command not found` | 未安装 → 引导用户 `cargo install himalaya` 或 `brew install himalaya` |
| 启动进了交互向导 | 无配置文件 → 引导用户先落盘 `config.toml`，agent 环境别指望能交互填 |
| 认证失败 | 密码错、没用应用专用密码、或密码命令没打印出密钥 → 检查 `*.password.command` |
| `envelope search` 在 Gmail 上报错 | Gmail 无 IMAP `SORT` 能力 → 改用 `himalaya gmail messages list -q "..."` |
| `mml compose \| himalaya send` 挂住 | 裸管道 stdin/stdout 冲突 → 用临时文件或进程替换 `>(...)` |
| 读了信但没标已读 | `read` 故意用 `BODY.PEEK` 不置 `\Seen` → 要标读显式 `flag add --flag seen <id>` |
| 引用的 id 过一会失效 | `id` 是每邮箱局部键，copy/move 后变 → 长期引用用 `message-id` |
| 发错人 | 没先确认收件人 → 永远发送前复述确认，候选拿不准让用户选 |
| 字段名/参数对不上 | 随版本略变 → `himalaya <cmd> --help`，或先 `--json | jq '.[0]'` 看真实结构 |

