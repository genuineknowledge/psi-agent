---
name: feishu-user-auth-onboarding
description: "让 E2 取证 / 任务读取等 user-token 能力在成员中「普及授权」——周期性扫描全员授权状态，对缺授权的成员逐个私聊发授权卡，24h 复查未授权者，汇总报告。LOAD FIRST when asked 全员授权还差谁 / 把授权普及到所有人 / 为什么又让我复制 code / 谁还没授权读消息读任务, or when the uat-onboarding schedule fires. 事实源：团队 TODO 看板表的人名列 + mentor 列（谁该有 user-token 能力），姓名→open_id 用组织架构成员表（feishu_department_members）或全员群（feishu_chat_find_member）解析；个别解析失败单独列出，不静默跳过、不冒充。发起授权用 feishu_auth_request（发卡，自动选最省事通道），状态复查用 feishu_auth_check。NOT for 单条工具返回 need_auth 的即时授权（那是 feishu_auth_request 随用随授）、判授权环境配置（feishu_auth_env_check）。"
category: productivity
---

# 全员 user-token 授权普及（E2 取证 / 任务读取）

`feishu_message_search` 等 E2 通道是 **user-token-only**：机器人自己的凭据查不了，必须每个成员
以本人身份授权一次。单条任务触发时 `feishu_auth_request` 会随用随授，但那样只能等用户撞上
`need_auth` 才补——本技能做**主动普及**：周期性扫全员、把缺授权的人一次补齐，让 audit 的 E2
摄入与任务读取在周期任务里不再被「该用户没授权」卡住。

## When to use

- 周期性普及（`uat-onboarding` 定时任务触发，见 `schedules/uat-onboarding/TASK.md`）。
- 用户问「还有谁没授权」「把授权普及到所有人」「全员授权进度如何」。
- audit / 例行检查发现一批 `need_auth`、需要成批补齐时。

## When not to use

- 某一条工具刚返回 `need_auth`、只需要当前用户授权 → 直接用 `feishu_auth_request`
  （随用随授，别等普及轮次）。
- 排查「为什么每次都要复制 code / 授权环境哪里没配好」→ `feishu_auth_env_check`。
- 请假、报销等非飞书授权问题。

## 流程

### 1. 定名单（谁需要 user-token 能力）

名单 = 团队 TODO 体系里会填报与验收的人：

- 读团队 TODO 看板表（`feishu_sheet_read`，结构现场探：表头/人名列/mentor 列/当期列）：
  当期**人名列** + **mentor 列**去重 = 目标名单（含 mentor——E2 检索与任务读取对 mentor 同样必要）。
- 拿不到看板表（没权限/表结构读不懂）→ 明说查询失败，用组织架构兜底：
  `feishu_department_members` 列出部门成员作为候选并标注「看板未读到」。
- 空值跳过；不编造不存在的名字。

### 2. 姓名 → open_id（发私聊卡必需 user_key）

- 优先组织架构：`feishu_department_members` 拉成员（含 open_id + 姓名），按姓名精确匹配；
- 备选全员群：`feishu_chat_find_member` 在已知的全员群 roster 里按姓名解析；
- 匹配失败的名字**单独列「待人工确认」**：不发、不冒充、不静默跳过。

### 3. 查缺（谁已授权、谁没有）

对每个有 open_id 的成员调 `feishu_auth_check(user_key=<open_id>)`：

- 已授权（工具返回已覆盖所需能力）→ 跳过；
- 未授权 / 状态不确定 → 进待授权名单。查询失败（读不到状态）→ 按「待授权」处理但报告里注明是查询失败。

### 4. 补齐（逐个发授权卡）

对待授权成员逐个 `feishu_auth_request`：

- `user_key` = 该成员的 open_id；`receive_id` 默认即其本人（私聊卡）；
- `reason` 固定文案（面向成员，说清用途）：
  「为保证海豚能读取群消息与任务记录完成验收取证（E2），需要你授权一次；授权一次长期有效，之后新增权限才会再次请求。」
- `capabilities` 按当时实际需求传（E2 消息检索 / 任务读取等工具的 `need_capabilities`）；
  没有明确需求时用通用 docs/drive 集（同 `feishu_auth_request` 的空值语义）。
- 授权卡会自动选最省事通道（卡片/免复制链接）；**不要**把授权链接当文本直接发群里。
- 每人每轮至多一张卡；同一人不重复发。

### 5. 复查与汇总

- 24h 后（下一轮或手动复查）对上一轮未授权者再 `feishu_auth_check`：
  仍未授权 → 再发一轮卡（最多连续两轮），之后仍未授权者进「待人工跟进」名单（可能是请假/拒授权/不活跃），
  **不无限轰炸**。
- 汇总报告：已授权 / 本轮新发卡 / 复查仍缺 / 名字解析失败 / 查询失败 各多少人，名单列清；
  报告回发起会话（定时触发时是调度会话，正文写明去向）。

## 红线

- 名单来自事实源（看板/组织架构），不编造；解析失败的人不发也不冒充。
- 授权是**本人**的行为：不代点、不伪造 user_key、不替用户同意。
- 每人每轮至多一卡；用户不授权只记「待人工跟进」，不催到骚扰。
- 查询失败 / 读表失败要明说，不把「查不到」当「已授权」或「未授权」硬判。
- 复制 code 类手工兜底文案按 `feishu_auth_request` 的返回执行，不在群里贴授权 URL 明文让所有人点。

## 相关

- 即时授权：`feishu_auth_request` / `feishu_auth_check` / `feishu_auth_env_check`
- 消费方：`company-todo-audit` 的 E2 证据摄入（`feishu_message_search` 需 user_key）
- 定时载体：`schedules/uat-onboarding/TASK.md`
