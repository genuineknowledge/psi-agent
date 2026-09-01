---
name: himalaya
description: Send, read, search and manage IMAP/SMTP email straight from the terminal via the `himalaya` CLI. LOAD whenever the task needs to read/list emails in a folder, search a folder, compose & send an email, reply/forward, save drafts, manage folders/flags, or download attachments over IMAP/SMTP. Not for iMessage/SMS (see apple-imessage), Telegram/Discord/Slack, or webmail-only APIs (Gmail API).
category: email
---

# Himalaya —— 终端里的 IMAP/SMTP 邮件（via `himalaya` CLI）

> 语法基于 **himalaya v1.2.0**（`himalaya --version` 确认）。跨版本参数会漂移，
> 拿不准就 `himalaya <cmd> --help`。

## 定义

用 [`himalaya`](https://github.com/pimalaya/himalaya)（Rust 写的跨平台命令行邮件客户端，
Linux / macOS / Windows 都能装）通过 `bash` 工具收发和管理邮件。它直接连你配好的
IMAP/SMTP 服务器，所以能列文件夹、列/读/搜信、写信发信、回复转发、存草稿、管标记、下附件。

| 你做 | Skill 教你怎么做 |
|------|------------------|
| 说明收件人 + 主题 + 正文 | `himalaya template write -H "To:..." -H "Subject:..." "正文" \| himalaya message send` |
| 要读某封信 | `himalaya envelope list -f INBOX -o json` 找 id → `himalaya message read <id> -o json` |
| 要搜文件夹 | `himalaya envelope list -f INBOX "from alice and after 2026-01-01 order by date desc"` |
| 要机读输出 | 全局加 `-o json`（**不是** `--json`），用 `jq` 解析 |

**没有专用 tool**——全部经 `bash` 工具调用 `himalaya`（就像 apple-imessage 经 bash 调 `imsg`）。

## 前置条件

- **安装 `himalaya`**（用户级操作，agent 只引导，不代跑）：
  - 预编译包（推荐，无需 Rust 工具链）：从 <https://github.com/pimalaya/himalaya/releases>
    下最新 release 里对应平台的包（Windows 是 `himalaya.x86_64-windows.zip`），解压出
    `himalaya`(.exe) 放进 PATH。
  - Cargo：`cargo install himalaya`
  - Homebrew（mac/Linux）：`brew install himalaya`
  - `himalaya --version` 验证；输出含 `+imap +smtp +wizard` 说明带了这些能力。
- **配置账户**。⚠️ **没有配置文件时，任意 himalaya 命令都会弹交互向导**
  （`Cannot find configuration... Would you like to create one with the wizard? (Y/n)`）
  **并挂住等输入**——不只裸 `himalaya`，连 `folder list` 也会。agent 环境下**别指望**能
  交互填：跑任何 himalaya 命令前**先确认配置文件存在**（`ls "$APPDATA/himalaya/config.toml"`
  之类），不存在就停下引导用户先配，别让命令挂死。请引导用户自己跑向导配好，或给出配置
  文件让用户落盘。配置文件按顺序取第一个存在的：
  - `$XDG_CONFIG_HOME/himalaya/config.toml`
  - `$HOME/.config/himalaya/config.toml`
  - `$HOME/.himalayarc`
  - Windows 上通常是 `%APPDATA%\himalaya\config.toml`；用 `-c <PATH>` 可显式指定。
- **密码/令牌绝不明文写死。** 密码字段可给字面量，也可给一条打印密钥到 stdout 的 shell
  命令——**优先用后者**（如 `pass show gmail`、读环境变量、系统 keyring）。

最小 IMAP/SMTP 账户配置（Gmail 为例，密码走 `pass`）：

```toml
[accounts.gmail]
default = true

backend.type = "imap"
backend.host = "imap.gmail.com"
backend.port = 993
backend.encryption = "tls"
backend.login = "example@gmail.com"
backend.auth.type = "password"
backend.auth.command = "pass show gmail"

message.send.backend.type = "smtp"
message.send.backend.host = "smtp.gmail.com"
message.send.backend.port = 465
message.send.backend.encryption = "tls"
message.send.backend.login = "example@gmail.com"
message.send.backend.auth.type = "password"
message.send.backend.auth.command = "pass show gmail"
```

- 上面是 v1.2.0 风格的配置骨架；**确切键名以向导生成的文件为准**。让用户跑
  `himalaya`（无参）或 `himalaya account configure <name>` 由向导生成，比手写更稳。
- Gmail 等需用**应用专用密码**（开了两步验证时），不是账户主密码——引导用户去开。
- `himalaya account list` 看账户，`himalaya account doctor <name>` 诊断连接问题。
- 缺配置/连不上时 `himalaya` 会报错，**照实转达用户**，别假装成功。

## 安全规则（最高优先级）

- **发送前必须确认收件人、主题和正文。** 发邮件是外发、不可撤回的动作——先把
  「发给谁、主题、正文」复述给用户确认，得到明确同意再真正 `message send`。
- **不给来历不明的地址发信**，收件人拿不准先问用户，别猜。
- **附件先验证路径存在**（`ls -l <path>`）再带上，别发不存在或错的文件。
- **不刷屏、不群发轰炸**，连发多封之间自我节流；不做批量营销。
- **只管 IMAP/SMTP 邮件。** iMessage/SMS 归 [[psi-agent-imessage-tool]]（apple-imessage skill），
  Telegram/Discord/Slack 各归各的，别用这个 skill 处理。
- **凭据是机密。** 配置文件里的密码/令牌、读到的邮件内容都含隐私——读到什么**不要外传**，
  只按用户当前请求用；也别把密钥值回显进对话，按字段名引用。

## 核心命令

机读输出用**全局** `-o json`（等价 `--output json`；注意 v1 **没有** `--json` 开关）。
文件夹用 `-f/--folder`。日志/警告走 stderr，管道保持可解析。

### 列文件夹 / 列信封（拿 id）

```bash
himalaya folder list -o json | jq          # 列所有文件夹(别名 mailbox)
himalaya envelope list -f INBOX -p 1 -o json | jq   # 列信封，-p 页码 -s 每页条数
```

信封含 `id`、`subject`、`from`、`to`、`date`、`flags` 等字段。`id` 是**每文件夹局部**的，
copy/move 后会变——当次操作用 `id`，要长期定位某封信靠 `Message-ID` 头（`message read` 里看）。

### 搜索 / 排序（就是带 QUERY 的 list）

v1 **没有独立的 `envelope search`**，过滤和排序作为查询串直接跟在 `envelope list` 后：

```bash
himalaya envelope list -f INBOX "from alice and after 2026-01-01 order by date desc" -o json | jq
```

查询语法：条件 `date/before/after <yyyy-mm-dd>`、`from/to/subject/body <pattern>`、`flag <flag>`，
用 `and` / `or` / `not` 组合；排序 `order by date|from|to|subject [asc|desc]`。
权威语法查 `himalaya envelope list --help`。
**注意（实测）**：过滤/排序编译成后端原生 IMAP SEARCH/SORT，需服务器支持——**很多国内邮箱
（QQ 实测）和 Gmail 的 IMAP 都不支持 SEARCH/SORT**，带任何查询串（`order by`、`subject xxx`、
`from xxx`）会**静默返回空列表**（不是报错，容易误判成"没邮件"）。这些服务上**别加查询串**，
直接 `envelope list -f INBOX` 用普通分页（默认按日期降序，最新在最前），要筛就自己在客户端侧
`jq` 过滤，或在邮箱 webmail 里搜。

### 读信

```bash
himalaya message read 42 -o json | jq       # 解析后的结构(含 headers/body)
himalaya message read 42                     # 人类可读渲染
himalaya message read 42 --no-headers        # 只要正文
himalaya message export 42                   # 导出原始 RFC 5322 字节
```

`message thread 42` 读整条会话。多个 id 直接 `message read 1 2 3`。

### 写信 / 发送（发送前先确认！）

> **关键坑**：`himalaya message write` / `message reply` / `message forward` 会打开
> `$EDITOR` 交互编辑，**在 agent 环境会挂住**。非交互路径一律走 `template` 子命令生成
> 纯文本模板，再管到 `message send`：

```bash
# 从零写一封并发送(先确认收件人/主题/正文!)
himalaya template write -H "To: you@example.org" -H "Subject: Hello" "正文内容" \
  | himalaya message send

# 回复某封(-A 回复全部)：先生成回复模板再发
himalaya template reply 42 "我这周五前处理" | himalaya message send
himalaya template reply -A 42 "收到" | himalaya message send

# 转发
himalaya template forward 42 "帮忙看下这封" | himalaya message send
```

`message send` 收原始 message（含头+正文）。存草稿不发：`... | himalaya message save -f Drafts`。

> **发送后存副本的坑（QQ 实测）**：`message.send.save-copy = true` 时，himalaya 发完会往
> Sent 文件夹存一份副本；若服务器返回的 Sent 文件夹不给 UID（QQ 的 `Sent Messages` 实测如此），
> 会**在 SMTP 已成功发出之后**报 `Error: cannot find UID of appended IMAP message`。
> 这时**信其实已经发出去了**，别因这个报错就重发导致发两封。规避：配置里设
> `message.send.save-copy = false`（发送就不报错，只是本地不留副本；收件人照常收到）。

### 标记 / 移动 / 附件

```bash
himalaya flag add 42 seen                 # 标已读(<ID>... <FLAG>...)；flag remove / set 同理
himalaya message copy -f INBOX Archives 42 # 复制到 Archives
himalaya message move -f INBOX Archives 42 # 移动
himalaya attachment download -f INBOX 42   # 下附件
```

读信默认不置 `\Seen`，要标已读用上面的 `flag add 42 seen`。

## 典型工作流：给某人回一封邮件

用户说「帮我回一下 Alice 那封关于发票的邮件，说这周五前处理」：

1. **定位邮件**——列信封拿 `id`（服务器支持 SEARCH 时可加 `"from alice"` 过滤；QQ/Gmail 等
   不支持，就列普通列表后自己 `jq` 筛）：
   ```bash
   himalaya envelope list -f INBOX -o json | jq '.[] | select(.from.addr | test("alice";"i"))'
   ```
   拿不准是哪封就把候选列给用户选，别猜。
2. **看原文**（避免答非所问）：`himalaya message read <id> -o json | jq .`
3. **确认**——把「回给 Alice、主题、正文」复述给用户，等明确同意。
4. **回复**（非交互，走 template）：
   ```bash
   himalaya template reply <id> "收到，这周五前处理完。" | himalaya message send
   ```
5. **复核**——`himalaya envelope list -f Sent -p 1 -o json | jq '.[0]'` 确认发出去了
   （sent 文件夹名可能是 `Sent` / `[Gmail]/Sent Mail` 等，先 `folder list` 看真名）。

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `himalaya: command not found` | 未安装 → 引导用户下 release 预编译包或 `cargo install himalaya` |
| 命令挂住无返回 | `message write/reply/forward` 打开了 `$EDITOR` → 改用 `template <write\|reply\|forward> ... \| message send` |
| 任意命令挂住问 `wizard? (Y/n)` | 无配置文件时**所有**命令都弹向导等输入 → 跑命令前先确认 config 存在，没有就停下引导用户配，别挂死 |
| `--json` 无效 | v1 用全局 `-o json`（`--output json`），不是 `--json` |
| `-m/--mailbox` 报错 | v1 文件夹参数是 `-f/--folder`（`mailbox` 只是 `folder` 命令的别名） |
| 带查询串列出来是空的 | QQ/Gmail 等 IMAP 不支持 SEARCH/SORT，带 `order by`/`from x` 会**静默返回空** → 去掉查询串用普通 list，自己 `jq` 筛 |
| 发完报 `cannot find UID of appended IMAP message` | `save-copy=true` 存 Sent 副本时服务器不给 UID（QQ 实测）→ **信已发出别重发**，配置设 `message.send.save-copy = false` 规避 |
| `-s/--page-size N` 返回空 | 部分服务器（QQ 实测）对 page-size 敏感 → 去掉 `-s` 用默认分页，或调 `-p` 页码 |
| 认证失败 | 密码错、没用应用专用密码、或 `auth.command` 没打印出密钥 → `account doctor <name>` 诊断 |
| 引用的 id 过一会失效 | `id` 是每文件夹局部键，copy/move 后变 → 长期定位靠 `Message-ID` 头 |
| 发错人 | 没先确认收件人 → 永远发送前复述确认，候选拿不准让用户选 |
| 字段名/参数对不上 | 随版本略变 → `himalaya <cmd> --help`，或先 `-o json \| jq '.[0]'` 看真实结构 |

