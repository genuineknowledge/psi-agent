---
name: feishu-rookie-onboarding
description: "Use when HR asks the agent to send someone an onboarding card (e.g. \"给某人发入职卡\") — that is the only entry point; there is no auto-trigger. Covers the single entry card + per-person doc checklist (28 items), the 研发/非研发 choice made inside the doc, doc→table sync (every 10 min on day 1, then before the 9:00 reminder), daily 9:00 reminders (day 1/day 2 only), and the 19:00 HR exception alert for people still unfinished at the end of day 2."
category: productivity
agent_editable: true
---

# 新人入职卡闭环

HR 对 agent 说「给<某人>发入职卡」→ agent 解析出对方 open_id → 发**入职卡**（一条消息 +
跳转到本人清单文档的按钮）→ 新人在文档里逐项打勾 → 同步回明细表并重算总览行 →
入职第 1/2 天按截止日催办（第 1 天绿卡、第 2 天红卡，之后不再推送）→
入职第 2 天结束（19:00）仍未完成时，给 HR 发一张异常提醒卡 + 总览表链接。

## When to use

- **主路径**：HR（或任何有权限的人）在飞书里对 agent 说「给<某人>发入职卡」/
  「给<某人>发个入职 SOP」之类的话。agent 需要先把「某人」解析成 open_id
  （通讯录按姓名查，见下方"解析姓名"），再调 `rookie_sop_card_send`。
- **这是唯一入口**：没有自动触发器。原先挂在通讯录新建员工事件上的
  `rookie-sop-welcome` 已删除——建号和「HR 决定要发卡」不是同一时刻，自动发卡会在
  转岗、补录这类场景下误触发真人，让 HR 显式说一句更可靠。

## When not to use

- 管理制度确认卡（那是 `feishu-handbook-onboarding`，两者互不替代）。
- 普通待办清单 → `feishu-todo-card`；一张卡只要一个答案 → `feishu_message_send_card`。
- 签字确认、背调材料收集等线下环节。

## Instructions

### 解析姓名（HR 主动发卡时）

HR 说的是姓名，`rookie_sop_card_send` 要的是 `open_id`——这一步由 agent 做，工具本身
不认姓名。用 HR 自己当前会话的身份去查（`<feishu_context>` 里的 `sender_open_id`
作为 `user_key`），调 `GET /open-apis/search/v1/user`（按姓名搜通讯录）拿到目标人的
open_id；同名或查不到时向 HR 确认，不要猜。参考 `skills/feishu-contact/SKILL.md`
里对通讯录接口鉴权方式的说明（那个 skill 本身按手机号/邮箱匹配，不按姓名，
姓名搜索要另外调 `/search/v1/user`）。

### 发卡

1. 姓名解析出 open_id 后，调 `rookie_sop_card_send`（`open_id` 必填，`name` 建议一并传，
   `event_payload_json`/`onboard_date` 留空即可，工具自己处理默认值）。
2. 通讯录事件触发时同样调这个工具，但场景参数留空，靠 Session 注入的 `event_payload_json`。
3. 幂等：同一人重复调用复用已有明细行，不会写出两套，也不会重复建定时任务。
4. 工具成功后卡片已可见：本轮**零 assistant 文本**（不要说「卡片已发送」）。
   「零文本」= 这一轮**什么都不输出**。**不要输出 `NO_REPLY`** —— 本工具自己发卡，
   这一轮走的是普通飞书文本流，它不过滤 `NO_REPLY`，输出了就会被当成正文发给新人
   （实测踩过：新人收到一条写着 `NO_REPLY` 的消息）。

### 处理勾选

1. 解析 `<feishu_card_action>` 整段 JSON，调 `rookie_sop_tick(card_action_json=<整段 JSON>)`。
2. 不要先复述「你点击了…」—— 卡片已由框架原地重绘。
3. 成功 → 零文本结束（**什么都不输出，也不要输出 `NO_REPLY`**）；只有工具返回
   `ok=false` 才回报必要错误，不得谎称成功。
4. **批量**：payload 若包在 `<feishu_card_action_batch>` 里，**每条各调一次**
   （漏一条就丢一项完成），然后最多回一条汇总，或直接零文本（同样不要输出 `NO_REPLY`）。

### 角色（研发 / 非研发）

角色选择在**详情页文档里**，是两个互斥的勾选框（「我是研发人员」/「我是非研发人员」），
不在入口卡上——入口卡只有一个跳转按钮，没有任何回调动作。

- 新人勾完，下一次同步（入职当天每 10 分钟）会把它落到明细表：打 `适用角色` 标签；
  选非研发时再把 5 个 `dev_only` 项标成不适用，分母从 28 降到 23。
- 两个框都勾了**以非研发为准**——宁可让研发项显示为不适用（可由 HR 或本人改回），
  也不要给非研发的人压一堆他做不了的项。
- `role_confirmed` 自己**永远不是** `dev_only`，也绝不会被标成不适用：它是「角色已确认」
  这件事本身，对两种角色都成立。
- 只在表里还没记角色时落地一次。反复标不适用是无谓写入，而且新人若改主意（重勾另一个框），
  由人改表更稳妥——自动来回翻转会让已完成的开发项在两种状态间反复横跳。

`rookie_sop_role_set` 这个工具仍在（卡片回调式的角色选择），但当前没有任何地方会发出
那张角色卡，属于保留代码。


### 定时任务

- 催办：每人一份 `rookie-remind-<open_id 后 8 位>`，`cron="0 9 * * *"`、`fire=tool`、
  `tool="rookie_sop_remind"`、`tool_args={"open_id": "ou_…"}`。由 `rookie_sop_card_send` 自动建，
  到点不经过 LLM（`fire=tool`）。
  - 只推两天，不是「没做完就一直催」：入职第 1 天发绿卡，第 2 天发红卡，第 3 天起
    即便清单仍未做完也**不再发卡**（`rookie_sop_remind.decide_remind` 的四分支决策）。
  - 第 2 天若清单仍未完成，除了给新人的红卡，还会**顺带给 HR 发一张反馈卡**——
    前提是 `config/rookie_sop.yaml` 里 `hr_notify_id` 已配置；留空时（联调期间的
    安全默认）明确跳过并在返回值里写清原因，不猜收件人、不悄悄不发。
  - 不论哪种情形（毕业、还是第 3 天起停推），工具都会删掉自己这份定时，而不是
    继续到点转、天天返回"无事可做"——全部做完随时可以毕业（不看第几天）；
    没做完但到了第 3 天则直接停推，是否继续跟进变成 HR 反馈卡（第 2 天那张）
    该管的事，不再靠天天骚扰新人来兜底。
- HR 异常提醒：全局一份 `rookie-exception-alert`，落在 HR 自己的 Session，
  `cron="0 19 * * *"`、**`fire=prompt`**（内容要现算聚合，`fire=tool` 到点不经 LLM
  只能传固定参数），TASK 正文写「调用 rookie_sop_digest」。
  - **它不是日报，是异常提醒**：只报「入职第 2 天结束时仍未完成」的人
    （`rookie_sop_digest.active_rookies`）。第 1 天还在办手续，催 HR 没意义；
    已完成的更不该占 HR 的待办。所以收到这张卡就意味着有人需要人工介入。
  - 入职日缺失的行会照样报出来——宁可让人多看一眼，也不静默漏掉一个卡住的新人。
  **这一份不会自动建**——它需要真实的 HR open_id，必须上线时手工建一次：
  ```text
  schedule_manage(action="create", schedule_name="rookie-exception-alert",
    cron="0 19 * * *", fire="prompt",
    content="调用 rookie_sop_digest：把入职第 2 天结束仍未完成的新人报给 HR。",
    visibility="silent", description="新人入职异常提醒（Day 2 未完成）")
  ```
  没建之前，`rookie_sop_digest` 工具本身可用（可手工调），但到点不会自己发。

## 边界

- 禁止用 `feishu_message_edit_card` 改这些卡（不重新注册回调，按钮会全死）。
- 禁止手写 `schedules/*/TASK.md` 或 `triggers/*/TRIGGER.md`，一律走
  `schedule_manage` / `trigger_manage`。
- 总览表是**投影**：只由工具从明细整体重算，不要手工改它、也不要写增量更新逻辑。
- 单卡最多 40 行。

## 配置

`config/rookie_sop.yaml`：SOP 清单（模块 / 项 / 验收标准 / `window_days` / `dev_only`）、
`sop_doc_url`、`hr_notify_id`。改 SOP 只改这里，不动代码。
运行时的 `app_token` 与两个 `table_id` 存在 workspace 的 `.psi/rookie_sop/base.json`。
