---
name: haitun-feature-uat
description: "Automate Haitun feature-module UAT by driving a target Session B with natural-language cases from a playbook. LOAD when 功能验收, UAT 自动化, 海豚自测对话, 方案两能力自然语境验收, or user asks to run proposal-capabilities UAT without human typing. Call poc_feature_uat. Supports agent= (same-host WIP pack) and target_gateway_url= (cross-stack HTTP). NOT for L1 document padding. NOT a substitute for poc_l2_probe wiring asserts."
---

# 海豚功能 UAT · A→B 对话验收

权威实现在本 skill + 工具 `poc_feature_uat`。

## 何时加载

- 用户要**省人工**跑海豚功能验收 / UAT
- 「按自然语境 UAT 测方案提醒 + 撰写」
- 「开一个干净 Session 把用例打一遍出 Pass/Fail」
- 「测开发机上的 WIP agent / 另一套 Gateway」

## 不要用来做什么

- L1 垫词/文档对照 → `proposal-acceptance-poc` / L1 基准文档
- 只查技能/工具是否接线 → `poc_l2_probe`（`poc-l2-reproduce`）
- 对**当前会话**同步发 chat（会死锁）

## 测谁（先选目标再跑）

| 目标 | 参数 |
|------|------|
| 本机已部署 defaults agent | 默认（无额外参数） |
| 同机 WIP 能力包 | `agent="<绝对或可解析的 agent 根>"` |
| 另一套 Gateway（开发测试栈） | `target_gateway_url="http://host:port"`（走 HTTP chat，不要指望跨机 Named Pipe） |
| 已有干净 Session | `target_session_id="<id>"` |

老板生产栈**没有** WIP 功能时，默认路径测不到那份能力 —— 必须 `agent=` 或 `target_gateway_url=`。

## 怎么跑

1. 读 playbook（默认方案两能力）:
   `skills/haitun-feature-uat/playbooks/proposal-capabilities-natural.json`
2. 调工具（按上表加目标参数）:
   ```text
   poc_feature_uat(
     playbook_path="skills/haitun-feature-uat/playbooks/proposal-capabilities-natural.json"
   )
   ```
3. 冒烟可加 `max_cases=2` 或 `case_ids="A-N1,A-N3"`
4. 向用户报告 `verdict` + `target_mode` + 失败案的 `gaps` + 各案回复摘要；**不要**说「组织验收通过」

## 判定说明（刻意为之）

v1 用子串组（组内 OR、组间 AND）+ `forbid_any`。假阴/假阳 → **改 playbook needles**，不要假装语义模型已判分。

## 前置

- 目标 Gateway 在线且已挂 AI
- 同栈：能 `POST /sessions` 并连上 channel socket
- 跨栈：目标 Gateway 的 `/sessions` 与 `/sessions/{id}/chat` 从本机 HTTP 可达
