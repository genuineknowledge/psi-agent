# 海豚功能 UAT · A→B 对话验收（设计）

> 2026-09-09。用途：省掉「人跟海豚聊一遍」的人力，用结构化用例驱动**目标 Session**。
> 同日补：能力包边界 + 同机 `agent=` / 跨栈 `target_gateway_url` HTTP chat。

## 要解决什么

多数 POC 是**海豚自己的功能模块**（提醒写方案、撰写检查、卡片闭环等）。
人工验收 = 按 UAT 文档发话、看回复是否接近 Pass 口径。

目标闭环：

```text
海豚 A（当前会话）
  → 读 playbook（用户话术 + 期望/禁止子串）
  → 选定目标 B（见下「测谁」）
  → 按序把「发给海豚」的正文发给 B
  → 用确定性规则对照 B 的回复
  → 汇总 Pass/Fail 报告
```

**刻意为之：** 不对**当前** Session 发同步 chat（会死锁）；B 必须是另一个 Session。

## 测谁：能力包边界（刻意为之）

Session 隔离的是**历史 / workspace / channel**，不是另一份能力包。
同 Gateway 上 `POST /sessions` 默认吃 `GET /defaults`.agent —— 与 SPA / 飞书 spawn 同源。
因此：

| 场景 | 做法 | 工具参数 |
|------|------|----------|
| 测**本机已部署**的 defaults agent | 默认路径：本地建 B + channel socket chat | （无额外参数） |
| 测**同机 WIP agent 包**（尚未改成 defaults） | `POST /sessions` 显式 `agent=` | `agent="/abs/path/to/wip-agent"` |
| 测**另一套 Gateway**（老板栈 ↔ 开发测试栈） | 在目标 Gateway 上建 Session，经 **HTTP** `POST /sessions/{id}/chat` 对话 | `target_gateway_url=http://…` |
| 已有干净 Session | 跳过 create | `target_session_id=…`（可与上两行组合） |

**为什么跨栈不能只拿对方的 `channel_socket`：** Named Pipe / Unix socket 只在 Gateway **本机**可达。跨主机必须走 Gateway 的 HTTP chat SSE（与 spa-v2 同面）。

**老板生产海豚没有 WIP 功能时：** 同栈默认 UAT **测不到**那份能力 —— 必须 `agent=`（同机挂 WIP 包）或 `target_gateway_url=`（打开发者测试 Gateway）。

## 与 `poc_l2_probe` 的关系

| | `poc_l2_probe` | `poc_feature_uat`（本文） |
|--|----------------|---------------------------|
| 测什么 | 技能/工具是否接线 | 功能对话是否像 UAT 期望 |
| 要不要 Gateway | 否 | **要**（建 B / 或固定 Session + 发消息） |
| 判定 | 文件/导出断言 | 回复子串组（AND of OR）+ 禁止项 |

## Playbook

见 `agents/feishu/skills/haitun-feature-uat/playbooks/*.json`。

每条 case：

| 字段 | 含义 |
|------|------|
| `id` | 用例 id（如 `A-N1`） |
| `session` | 会话键；相同键串在同一 B 上（如 A-N1→A-N2） |
| `required` | 计入总 Pass 门闩 |
| `user` | 发给 B 的原文（勿夹带「算不算中事」） |
| `expect_any_of` | `[[a,b],[c,d]]`：每组至少一个命中，组与组之间 AND |
| `forbid_any` | 命中任一 → Fail |

v1 **不做**语义模型打分；子串规则可调，假阴/假阳用改 playbook 收敛。

## 工具

```text
poc_feature_uat(
  playbook_path=...,
  case_ids="",
  timeout_seconds=180,
  agent="",                  # 同机 WIP 能力包
  target_gateway_url="",     # 跨栈 HTTP
  target_session_id="",      # 复用已有 B
)
```

出口：`verdict` = `pass` / `fail` / `blocked`，`target_mode` ∈
`local_defaults` / `agent_override` / `remote_gateway` / `fixed_session`，
每案 `reply` 摘要、`gaps`。

底层：

- `sessions_create(..., agent=, gateway_url=)` 透传 `POST /sessions`
- `chat_via_gateway`：跨栈 SSE 文本累积（`_subagent_helpers`）

## 首份剧本

从 `docs/superpowers/specs/2026-09-03-proposal-capabilities-uat.md` 抽出必过案：
A-N1～A-N4、A-N6、B-N1～B-N3（A-N5 建议级可标 `required: false`）。
