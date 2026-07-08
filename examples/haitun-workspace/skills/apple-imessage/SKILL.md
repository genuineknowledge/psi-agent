---
name: apple-imessage
description: Send and receive iMessages / SMS through Apple's Messages.app via the `imsg` CLI on macOS. LOAD whenever the task needs to text someone, read or search message history, list conversations, send a file/photo over iMessage/SMS, or watch for incoming messages. macOS only; not for Telegram / Discord / Slack / WhatsApp / email.
category: apple
---

# Apple iMessage / SMS（via `imsg` CLI）

## 定义

用 `imsg`（[steipete/imsg](https://github.com/steipete/imsg)）这个 macOS 命令行工具，
通过 `bash` 工具收发 iMessage / 短信。`imsg` 直接读 Messages.app 的本地聊天数据库、
并驱动 Messages 自动化发送，所以能列会话、读历史、搜索、发文本/文件、监听新消息。

| 你做 | Skill 教你怎么做 |
|------|------------------|
| 说明收件人 + 内容 | 组 `imsg send --to ... --text ...` 经 `bash` 跑 |
| 要读消息 | `imsg chats` 找 chat_id → `imsg history --chat-id N --json` |
| 要机读输出 | 一律加 `--json`（JSON-lines，一行一对象），用 `jq` 解析 |

**没有专用 tool**——全部经 `bash` 工具调用 `imsg`。

## 前置条件（仅 macOS）

- macOS 14+，Messages.app 已登录 iMessage。
- 安装 `imsg`：`brew install steipete/tap/imsg`（`imsg --version` 验证）。
- **权限**（System Settings → Privacy & Security）：
  - **Full Disk Access** 给你的终端 → 读聊天历史。
  - **Automation** 允许控制 Messages.app → 发送。
- 缺失时 `imsg` 会报错，**照实转达用户**去装/授权,别假装成功。安装与授权是
  用户级操作，agent 只引导,不代跑 `brew` 或点权限弹窗。

## 安全规则（最高优先级）

- **发送前必须确认收件人和内容。** 发消息是外发、不可撤回的动作——先把「发给谁、
  发什么」复述给用户确认,得到明确同意再 `imsg send`。
- **不给陌生号码发消息。** 收件人来历不明时先问用户,别猜。
- **附件先验证路径存在**（`ls -l <path>`）再 `--file`,别发不存在或错的文件。
- **不刷屏。** 不做群发/轰炸,连发多条之间自我节流。
- **只管 iMessage/SMS。** Telegram、Discord、Slack、WhatsApp、邮件不归这个 skill;
  也不做群成员管理/批量营销。
- 聊天历史含隐私内容,读到什么**不要外传**,只按用户当前请求用。

## 核心命令（只需 Full Disk Access + Automation）

`--json` 输出是 JSON-lines(一行一个对象),进度/警告走 stderr,所以管道保持可解析。
收集成数组用 `imsg <cmd> --json | jq -s`。

### 列会话（拿 chat_id）

```bash
imsg chats --limit 20 --json | jq -s
```

返回每个会话的 `chat_id`、显示名、参与者 handle。后续读历史/监听/发送都用这个 `chat_id`。

### 读会话历史

```bash
imsg history --chat-id 1 --limit 20 --json | jq -s
imsg history --chat-id 1 --limit 20 --attachments --json | jq -s   # 带附件元信息
```

其它可选过滤:`--participants <handles>`、`--start <iso>` / `--end <iso>` 限时间段。

### 搜索本地历史

```bash
imsg search --query "午饭" --match contains --limit 50 --json | jq -s
# --match exact 精确匹配
```

### 发送(发送前先确认!)

收件人四选一:`--to`(号码/Apple ID/联系人名)、`--chat-id`、`--chat-identifier`、`--chat-guid`。

```bash
imsg send --to "+14155551212" --text "我在路上了,大概十分钟到"
imsg send --to "Jane Appleseed" --file ~/Desktop/photo.jpg --text "看这个"
imsg send --to "+14155551212" --text "hi" --service imessage   # 强制 iMessage
imsg send --to "+14155551212" --text "hi" --service sms        # 强制短信
```

`--service` 默认 `auto`(Messages.app 自己决定 iMessage 还是 SMS);要强制才传 `imessage`/`sms`。
成功后建议 `imsg history --chat-id <该会话> --limit 1 --json` 复核确实发出去了。

### 监听新消息(会一直流,需自己中断)

```bash
imsg watch --chat-id 1 --attachments --json
```

`imsg watch` **不会自动退出**(持续流式)。经 `bash` 跑时给它加超时(如
`timeout 15 imsg watch --chat-id 1 --json`)做一次性轮询,拿到最近消息就返回;
否则会一直挂住。不带 `--chat-id` 监听全部会话。

## 典型工作流:给某个联系人发消息

用户说「给我妈发个消息说我到家了」:

1. **定位联系人**——列会话并按名字/号码过滤拿 `chat_id`:
   ```bash
   imsg chats --limit 50 --json | jq -s '.[] | select(.display_name // "" | test("妈|Mom"; "i"))'
   ```
   拿不准是哪个会话就把候选列给用户选,别猜。
2. **确认**——把「发给 X(号码 Y):内容 Z」复述给用户,等明确同意。
3. **发送**:
   ```bash
   imsg send --chat-id <id> --text "我到家了"
   ```
4. **复核**——读回最后一条确认发出。

## 版本 / 高级能力

- 输出字段名(`chat_id` / `display_name` / `is_from_me` / `text` / `date` 等)可能随
  `imsg` 版本略变;拿不准就先 `imsg <cmd> --json | jq -s '.[0]'` 看一条的真实结构再解析。
- `imsg --help` / `imsg <subcommand> --help` 查当前版本的确切参数。
- 高级 IMCore 命令(`react`/tapback、`edit`、`unsend`、`send-rich`、群管理等)需要关闭
  SIP 并注入 dylib(`imsg launch`),默认**不用**、也别主动帮用户关 SIP(降低系统安全性)。
  默认的 read/send/watch/search 流程只需 Full Disk Access + Automation,足够日常收发。

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `imsg: command not found` | 未安装 → 引导用户 `brew install steipete/tap/imsg` |
| 读历史报权限/空库 | 缺 Full Disk Access → 让用户在隐私设置里给终端授权 |
| 发送失败/无反应 | 缺 Automation 权限,或 Messages.app 未登录 iMessage |
| `imsg watch` 挂住不返回 | 它是长流,必须 `timeout` 包一层做一次性轮询 |
| 发错人 | 没先确认收件人 → 永远发送前复述确认,`chat_id` 拿不准就让用户选 |
| 非 macOS 环境 | 这个 skill 仅 macOS 可用,Linux/Windows 上 `imsg` 无法收发 |

