---
name: gog
description: "Google Workspace 全家桶终端集成，经 `gog` CLI 操作 Gmail / 日历(Calendar) / 云盘(Drive) / 文档(Docs) / 表格(Sheets)，以及 Slides/Forms/Contacts/Tasks。LOAD 当任务需要：搜/读/发 Gmail、列/建/改日历事件、搜/传/下 Drive 文件、导出或读取 Docs、读写 Sheets 单元格区域、管理 Contacts/Tasks。需先做一次性 OAuth 授权。不用于 IMAP/SMTP 通用邮箱(那走 himalaya)、iMessage/SMS(apple-imessage)、或本地文件读写。"
category: google
---

# gog —— 终端里的 Google Workspace（via `gog` CLI）

> 语法基于 [gogcli](https://github.com/openclaw/gogcli)（`steipete/gogcli`，Go 单体二进制）。
> 跨版本参数会漂移，拿不准就 `gog <service> <action> --help`，或 `gog schema --json` 看运行时契约。

## 定义

用 [`gog`](https://gogcli.sh/)（一个用 Go 写的跨平台命令行二进制，Linux/macOS/Windows 都能装）
通过 `bash` 工具访问 Google Workspace。它直接打 Google API，所以能操作 Gmail、日历、云盘、
文档、表格，以及 Slides/Forms/Contacts/Tasks/Admin。命令统一是 `gog <service> <action>` 形态。

| 你要做 | Skill 教你怎么做 |
|--------|------------------|
| 搜/读/发邮件 | `gog gmail search '...'` → `gog gmail get <id> --json` / `gog gmail send ...` |
| 看今天日程 | `gog calendar events --today` |
| 建日历事件 | `gog calendar create <calId> --summary "..." --from <iso> --to <iso>` |
| 找/传云盘文件 | `gog drive search "..."` / `gog drive tree --parent <id>` |
| 读/导出文档 | `gog docs cat <docId>` / `gog docs export <docId> --format txt --out <path>` |
| 读写表格区域 | `gog sheets get <id> "Tab!A1:D10" --json` / `gog sheets append ...` |
| 要机读输出 | 全局加 `--json`（稳定 JSON 到 stdout）或 `--plain`（TSV） |

**没有专用 tool**——全部经 `bash` 工具调用 `gog`（就像 himalaya 经 bash 调 `himalaya`、
apple-imessage 经 bash 调 `imsg`）。相关技能：通用 IMAP/SMTP 邮件见 [[psi-agent-himalaya-skill]]。

## 安全规则（最高优先级）

- **发邮件、建/改日历事件、删文件前必须确认。** 这些是外发或不可撤回的动作——先把
  「发/建/删什么、给谁、内容」复述给用户，得到明确同意再真正执行。宁可多问一句。
- **优先用只读与防误伤开关。** 调研/浏览类操作全程带 `--readonly`（在发网络请求前拦截会改数据的调用）；
  破坏性动作要 `--force` 才生效——**不要**随手加 `--force`，让用户明确要求再加。想看某个写操作会做什么，
  先 `--dry-run`。演练 Gmail 流程可加 `--gmail-no-send` 只走流程不真发。
- **凭据与令牌是机密，绝不外传、绝不落库。** OAuth client secret JSON、keyring 密码
  （`GOG_KEYRING_PASSWORD`）、访问令牌、以及读到的邮件/文档/联系人内容都含隐私——
  **不要** `cat` 出 client_secret / token 文件，不要把密钥值回显进对话（按字段名引用），
  不要把它们写进 git 或日志。
- **授权是用户的动作。** 你可以引导跑 `gog auth ...`，但真正把 Google 账户授权给这台机器
  由用户完成（浏览器同意页 / 提供 client secret）。别代填、别伪造账号或令牌。
- **只管 Google Workspace。** 通用 IMAP/SMTP 邮箱走 [[psi-agent-himalaya-skill]]，
  iMessage/SMS 走 apple-imessage，别用这个 skill 处理。

## 前置条件

- **安装 `gog`**（用户级操作，agent 只引导，不代跑）：
  - Homebrew（推荐，mac/Linux）：`brew install openclaw/tap/gogcli`，二进制名是 `gog`。
  - 其它平台见 <https://gogcli.sh/> 的 install 页（含预编译二进制、Docker 镜像
    `ghcr.io/openclaw/gogcli`）。
  - `gog version` 或 `gog --help` 验证装好了。
- **一次性 OAuth 授权**（见下节）。没授权时任何 API 命令都会失败或要求登录。
- 拿不准环境是否就绪，先跑只读诊断：`gog auth doctor --check --no-input`（见「Agent/headless 授权坑」）。

## OAuth 授权（一次性）

`gog` 用标准 OAuth：先存 OAuth client（一份从 Google Cloud Console 下的 `client_secret_*.json`），
再逐个授权账户。**client secret 与令牌都是机密，别 `cat`、别回显、别进 git。**

```bash
# 1) 存 OAuth client 凭据（用户从 Google Cloud Console 下载的 JSON）
gog auth credentials ~/Downloads/client_secret_xxx.json

# 2) 授权一个账户并声明要用哪些服务（会走浏览器同意页）
gog auth add you@gmail.com --services gmail,calendar,drive,docs,sheets,contacts

# 3) 校验授权与 API 可达
gog auth doctor --check
gog auth list --check
```

需要 gog 帮忙开 API / 打开控制台，可用 setup 助手（可选）：

```bash
gog auth setup you@gmail.com --gcloud-project my-gog-project --enable-apis --open-console
```

多账户 / 多 client（不同账户走不同 Cloud 项目）：

```bash
gog --client work auth credentials ~/Downloads/work-client.json
gog --client work auth add you@company.com
gog auth credentials list
gog auth alias set work you@company.com     # 起别名，之后 --account work 即可
```

## 账户选择

一台机器上多个账户时，用下面任一方式指定当次操作用哪个账户：

```bash
export GOG_ACCOUNT=you@gmail.com     # 设一次，后续命令免带 --account
gog --account you@company.com gmail search 'is:unread'   # 单次覆盖，可用 email/别名/auto
```

## Agent / headless 授权坑（重要）

Agent 环境没有浏览器、也常没有系统 keyring，`gog` 支持**文件型加密 keyring**，密码由环境变量注入：

```bash
# 用文件 keyring 后端，密码走 GOG_KEYRING_PASSWORD（值是机密，别回显/别进 git）
GOG_KEYRING_BACKEND=file GOG_KEYRING_PASSWORD=****** gog auth list --check
```

- **坑：你在交互 shell 里 check 通过，不代表 agent 拉起的子进程继承了 `GOG_KEYRING_PASSWORD`。**
  跑任何真操作前，先在**同一执行上下文**里确认：`gog auth doctor --check --no-input`。
  它退出码非 0 就说明子进程没拿到密码/授权 —— 停下来把环境变量传进去，别硬跑导致挂起或半途失败。
- 授权本身（浏览器同意）请引导用户在有浏览器的机器上完成，或用官方 Docker
  （`ghcr.io/openclaw/gogcli`，挂 `GOG_HOME` 持久卷 + `GOG_KEYRING_*`）容器内 `auth add`。
- 缺授权 / 连不上时 `gog` 会报错，**照实转达用户**，别假装成功。

## 输出与全局开关

机读优先 `--json`（stdout 稳定 JSON 信封）或 `--plain`（TSV）；**人类进度、提示、警告一律走
stderr**，所以管道保持可解析。脚本化建议 `--json --no-input` 一起用（不交互，缺参数就失败而非挂起）。

| 开关 | 作用 |
|------|------|
| `--json` / `--plain` | stdout 出 JSON / TSV，便于 `jq` 或切列解析 |
| `--no-input` | 需要交互时**直接失败**而非挂起（agent 环境必备） |
| `--readonly` | 发请求前拦截会改数据的调用（只读调研用） |
| `--dry-run` | 演练写操作，看它会做什么但不真做 |
| `--force` | 放行破坏性操作（删除等），**别随手加** |
| `--gmail-no-send` | Gmail 走完整流程但不真的发出 |
| `gog schema --json` | 打印运行时命令契约，参数拿不准时查 |

## 核心命令

### Gmail

```bash
gog gmail search 'newer_than:7d' --max 10           # 按线程，每线程一行
gog gmail messages search "in:inbox from:foo.com" --max 20   # 按单封，每封一行
gog gmail get <messageId> --sanitize-content --json # 读单封（sanitize 去掉危险内容）
gog gmail send --to a@b.com --subject "Hi" --body "Hello"
gog gmail send --to a@b.com --subject "Hi" --body-file ./message.txt   # 多段正文
gog gmail send --to a@b.com --subject "Hi" --body-html "<p>Hello</p>"  # 富文本
gog gmail drafts create --to a@b.com --subject "Hi" --body-file ./msg.txt
gog gmail drafts send <draftId>
gog gmail send --to a@b.com --subject "Re: Hi" --body "回复" --reply-to-message-id <msgId>
```

多段/含换行的正文用 `--body-file`（或 `--body-file -` 从 stdin 读 heredoc），**别**指望 `--body` 里的 `\n` 生效：

```bash
gog gmail send --to r@example.com --subject "会议跟进" --body-file - <<'EOF'
你好，
今天会议辛苦了。后续事项：
- 事项一
- 事项二
此致
EOF
```

> 发送前先复述「收件人 + 主题 + 正文」给用户确认。演练可加 `--gmail-no-send`。

### 日历 Calendar

```bash
gog calendar events --today                          # 今日日程
gog calendar events <calendarId> --from <iso> --to <iso>
gog calendar create <calendarId> --summary "标题" --from <iso> --to <iso>
gog calendar create <calendarId> --summary "标题" --from <iso> --to <iso> --event-color 7
gog calendar update <calendarId> <eventId> --summary "新标题" --event-color 4
gog calendar colors                                  # 列颜色 ID（1-11）
```

> `--from/--to` 用 ISO 时间。建/改事件前先跟用户确认时间、标题、参与人。

### 云盘 Drive

```bash
gog drive search "query" --max 10
gog drive tree --parent <folderId> --depth 2         # 目录树（只读预览）
gog drive du   --parent <folderId> --max 20 --json   # 目录占用统计
```

> 删除/移动文件是破坏性操作：先 `--dry-run` 看清楚，确认后再带 `--force`。

### 文档 Docs

```bash
gog docs cat <docId>                                 # 直接读正文
gog docs export <docId> --format txt --out /tmp/doc.txt   # 导出（txt/其它格式）
```

> Docs **不支持在线编辑**（gog 缺 Docs 编辑 API 客户端），只能 export / cat / copy。
> 要改内容就导出→本地改→或用 Drive 上传新版本。

### 表格 Sheets

```bash
gog sheets get <sheetId> "Tab!A1:D10" --json
gog sheets update <sheetId> "Tab!A1:B2" --values-json '[["A","B"],["1","2"]]' --input USER_ENTERED
gog sheets append <sheetId> "Tab!A:C" --values-json '[["x","y","z"]]' --insert INSERT_ROWS
gog sheets clear  <sheetId> "Tab!A2:Z"
gog sheets metadata <sheetId> --json                 # 看有哪些 tab / 维度
```

> 值优先走 `--values-json`（二维数组）。`clear` 是破坏性的，先确认区域范围再跑。

### Contacts / Tasks（补充）

```bash
gog contacts list --max 20
gog tasks list --json
```

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `gog: command not found` | 未安装 → 引导 `brew install openclaw/tap/gogcli` 或下预编译二进制 |
| 命令挂住等输入 | 缺授权/缺 keyring 密码时会尝试交互 → 全程带 `--no-input`，并先 `gog auth doctor --check --no-input` |
| shell 里 auth 正常但 agent 跑就报未授权 | 子进程没继承 `GOG_KEYRING_PASSWORD` → 在同一执行上下文注入该环境变量，用 `auth doctor --no-input` 验 |
| `--body` 里 `\n` 没换行 | `--body` 不 unescape → 多段用 `--body-file`/`--body-file -` heredoc，或 `$'a\n\nb'` |
| `gmail search` 少了某些邮件 | search 每线程一行 → 要逐封用 `gmail messages search` |
| Docs 改不动 | gog 无 Docs 编辑 API → 只能 export/cat/copy，改内容走导出或 Drive 上传新版本 |
| 误删/误改数据 | 破坏性动作没先演练 → 调研带 `--readonly`，写操作先 `--dry-run`，确认后才 `--force` |
| 字段/参数对不上 | 随版本漂移 → `gog <service> <action> --help` 或 `gog schema --json`，或先 `--json | jq '.[0]'` 看真实结构 |
| 多账户发错号 | 没指定账户 → `export GOG_ACCOUNT=` 或每次带 `--account <email|alias>` |
| 凭据泄露风险 | 别 `cat` client_secret/token、别回显 `GOG_KEYRING_PASSWORD`、别把它们提交进 git |
