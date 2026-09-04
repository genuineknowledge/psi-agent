---
name: uat-onboarding
description: 周期性把 user-token 授权普及到全员（E2 取证 / 任务读取需要）：读团队 TODO 看板表取人员名单（人名列+mentor 列去重），组织架构/全员群解析 open_id，feishu_auth_check 查缺，逐个 feishu_auth_request 私聊发授权卡，24h 复查未授权者。每周二 10:00 触发（首轮可手动提前跑一次补齐存量）。
cron: "0 10 * * 2"
visibility: silent
fire: prompt
---

# 全员授权普及（uat-onboarding）

## 目标

`feishu_message_search` / 任务读取等 E2 能力是 **user-token-only**：每个成员要授权一次，
周期任务（audit 14:30 等）才不会因「该用户没授权」卡住 E2 摄入。本任务把缺授权的人主动补齐。

## 流程（细节以技能为准：先 `skill_manage(action="view", skill_name="feishu-user-auth-onboarding")` 加载）

1. **定名单**：读团队 TODO 看板表 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc
   —— `/wiki/` 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token；
   `feishu_sheet_find_columns` + `feishu_sheet_read` 认表头/人名列/mentor 列（现场探，不写死）；
   当期**人名列 + mentor 列**去重 = 目标名单。
2. **解析 open_id**：`feishu_department_members`（组织架构）或 `feishu_chat_find_member`（全员群）
   按姓名精确匹配；失败者单独列「待人工确认」，不发不冒充。
3. **查缺**：逐个 `feishu_auth_check(user_key=<open_id>)`；已授权跳过，未授权/不确定进待授权名单。
4. **补齐**：逐个 `feishu_auth_request(user_key=…, reason=固定文案, capabilities=按需)` 发私聊授权卡；
   每人每轮至多一卡，不重复。
5. **复查**：上一轮未授权者 24h 后 `feishu_auth_check`，仍未授权再发一轮（最多两轮），
   之后进「待人工跟进」（请假/拒授权/不活跃），不无限轰炸。
6. **汇总**：已授权 / 本轮新发 / 复查仍缺 / 解析失败 / 查询失败 各多少人、名单列清，报告回本会话。

## 红线

- 名单来自事实源，不编造；解析失败不静默跳过。
- 授权是本人行为：不代点、不伪造 user_key。
- 读表/查状态失败要明说，不把「查不到」硬判成已授权或未授权。
- 本任务只做授权普及，不做检查、不判 TODO、不发报告给无关人。

## 部署注记

仓库按约定不代建运行时实体：本 TASK.md 作为种子随 agent 包提供；若运行实例未自动装载，
在对应会话用 `schedule_manage(action="create", name="uat-onboarding", cron="0 10 * * 2", fire="prompt")`
创建（内容照本文件）。首轮建议手动跑一次补齐存量缺口。
