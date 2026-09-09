# B 端海豚 · 方案跟进定时「私聊消息 + 进度卡」· 测试报告（交付验收）

| 项 | 内容 |
|----|------|
| 交付 / 报告日期 | 2026-09-08 |
| 产品线 | ToB（飞书机器人 / `agents/feishu`） |
| 验收主题 | 方案通过后建的跟进定时，**到点必须同时投递私聊正文 + 进度卡**（不得只发文字） |
| 验收环境 | 本机隔离 ToB：Gateway + Feishu Channel + agent 包 `agents/feishu`；自动化用例跑本机 pytest |
| 对照脚本 | `docs/superpowers/specs/2026-09-08-proposal-e2e-poc-playbook.md`（自然对话 E2E，含到点催办步） |
| 前置交付 | [`2026-09-03-proposal-capabilities-delivery.md`](./2026-09-03-proposal-capabilities-delivery.md)（提醒者 + 撰写检测已通过） |
| 本期总结论 | **工程契约与自动化验收：通过**；存量定时需重建后才具备新行为；飞书真机「到点同拍」建议按 playbook 抽测补签 |

---

## 1. 背景与问题（为何本期要验）

上一期已交付「提醒者 + 撰写检测」；撰写通过后会按 `proposal_id` 同步跟进定时（`-pre` / `-ddl`）。

对已落地方案 **飞书回复定位锚点**（`proposal_id≈feishu-reply-anchor`）做定时抽查时发现：

| 期望（产品口径） | 当时实际 |
|------------------|----------|
| 到点：**私聊催办正文 + 进度卡**同拍 | 定时均为 `fire=tool` → **仅** `feishu_message_send`（只有文字私聊） |
| 进度可在卡上自报勾选 | **无**到点发卡路径 |

根因不是 Session 调度坏了，而是：

1. `fire=tool` **一次只能调一个工具**，历史上创建定时时挂了裸 `feishu_message_send`；
2. skill 未把「消息+卡」写成**硬约束**，Agent 合法地只挂了发消息。

本期交付目标：把「到点 = 消息 + 进度卡」收成**可执行、可校验**的工程契约，并用自动化钉住；存量定时须重建才升级行为。

---

## 2. 交付范围

| 交付物 | 说明 |
|--------|------|
| 新工具 `feishu_proposal_nudge` | 到点一次调用内：先发私聊正文，再发进度卡（`feishu_todo_card_send`） |
| `schedule_manage` 校验 | `tool=feishu_proposal_nudge` 时强制非空 `receive_id` / `text` / `items_json`；拒占位 receive_id |
| skill `proposal-writing-standard` §C | `-pre`/`-ddl` **必须**挂 `feishu_proposal_nudge`；**禁止**只挂 `feishu_message_send`；卡行 bake 进 `items_json`；勾选=自报 ≠ 验收 |
| 文档 | `agents/feishu/AGENTS.md`、E2E playbook 同步为 nudge 口径 |
| 自动化测试 | `test_feishu_proposal_nudge.py` + `test_schedule_manage` 相关用例 |

**明确不在本期：**

- 勾选进度卡 = 组织验收通过（仍禁止；验收对照另走 acceptance skill）
- 自动迁移/改写**已落盘**的旧定时（须删旧组后按新 skill 重建）
- C 端桌面包（`agents/desktop`）

---

## 3. 测试设计

### 3.1 分层

| 层 | 目的 | 方法 | 是否本期已跑 |
|----|------|------|--------------|
| **L0 问题复现（审计）** | 确认存量定时只有发消息 | 读 workspace `schedules/prop-feishu-reply-anchor-*/TASK.md` 的 `fire`/`tool` | **已做**（发现问题依据） |
| **L1 单元 / 契约** | 钉死 nudge 行为与建定时校验 | pytest，mock 飞书发送与发卡 | **已做 · 全 Pass** |
| **L2 自然对话 E2E** | 用户不点名工具名，走通「过检 → 建定时 → 到点同拍」 | playbook 私聊话术 | **脚本已备；真机到点抽测建议上级抽签补做** |

### 3.2 L1 用例设计（自动化）

| 编号 | 用例 | Pass 标准 |
|------|------|-----------|
| N-1 | nudge 正常路径 | 调用后：消息发送 1 次 + 进度卡发送 1 次；返回 `ok` 且含两者结果 |
| N-2 | `items_json` 为空 / 无有效行 | **不发**消息、**不发**卡；返回明确错误（避免空催办） |
| N-3 | 发卡失败 | 消息已发时仍如实带回卡侧错误，不静默假装整段成功 |
| S-1 | `schedule_manage` 创建 one-shot + `feishu_proposal_nudge` | 落盘 YAML：`fire=tool`、`tool=feishu_proposal_nudge`、`run_once`、args 含三项必填 |
| S-2 | 缺 `items_json` 仍想建 nudge 定时 | **拒绝创建**，不得写出半残 TASK |
| S-0（回归） | 原有 one-shot `once_at` 路径 | 不因 nudge 改动破坏既有 `feishu_message_send` 类 one-shot 创建 |

### 3.3 L0 审计结论（改前）

对 `feishu-reply-anchor` 相关定时抽查：**全部**为 `feishu_message_send` only → 与「消息+卡」产品期望不符 → 驱动本期改造。  
（改后：**新建**定时才走 nudge；旧 TASK **不会**自动变。）

---

## 4. 测试结果

### 4.1 自动化（L1）· 2026-09-08

命令：

```text
pytest agents/feishu/tests/test_feishu_proposal_nudge.py \
  agents/feishu/tests/test_schedule_manage.py::test_create_proposal_nudge_one_shot \
  agents/feishu/tests/test_schedule_manage.py::test_create_proposal_nudge_rejects_missing_items_json \
  agents/feishu/tests/test_schedule_manage.py::test_create_one_shot_once_at
```

| 结果 | 数量 |
|------|------|
| **Passed** | **6 / 6** |
| Failed | 0 |
| 段结论 | **通过** |

说明：同目录另有依赖本机 `tzdata` 的时区用例，与本期无关，未计入本报告范围。

### 4.2 验收总览（供上级一眼看）

| 能力段 | 计划 | 实测 | 段结论 |
|--------|------|------|--------|
| L0 问题确认（旧定时仅文字） | 抽查 reply-anchor 定时 | 确认仅 `feishu_message_send` | **问题成立** |
| L1 工程契约 + 自动化 | N-1～N-3、S-1～S-2、S-0 | 6/6 Pass | **通过** |
| L2 飞书真机到点同拍 | playbook「到点催办」步 | 脚本就绪；**建议抽测补签** | **待抽测（不阻塞工程交付）** |
| **整体（工程交付）** | L0 闭环 + L1 | — | **通过** |

### 4.3 与产品期望对照

| 期望 | 本期状态 |
|------|----------|
| 新创建的方案跟进定时到点发「字+卡」 | skill + 工具 + 创建校验已强制；自动化覆盖 |
| 禁止「只发字」的跟进定时（新路径） | `schedule_manage` + skill 双闸 |
| 勾选进度 ≠ 组织验收 | skill / playbook 明文保留（刻意为之） |
| 历史 `prop-feishu-reply-anchor-*` 立刻变成字+卡 | **否** — 须删除旧组后按新 skill 重建 |

---

## 5. 能力边界（对齐预期）

| 会做 | 不会做 |
|------|--------|
| 到点同拍私聊催办正文 + 进度卡（经 `feishu_proposal_nudge`） | 一次 `fire=tool` 里串任意多个无关工具 |
| 建定时时校验卡行与收件人，缺项拒建 | 静默写出缺 `items_json` 的「半残」定时 |
| 进度勾选作为**自报**痕迹 | 把勾选当成验收通过 / 立项结论 |
| 同 `proposal_id` 顶替旧定时组（skill 既有规则） | 自动 patch 磁盘上仍挂 `feishu_message_send` 的旧 TASK |

---

## 6. 已知限制与后续建议

1. **存量定时重建（运维必做）**  
   已存在的 `prop-*-pre` / `prop-*-ddl` 若仍是 `feishu_message_send`，到点行为不变。  
   **建议：** 对重点方案（如 reply-anchor）`schedule_manage` 删旧组 → 用通过稿再跑一次「同步定时」，确认 YAML 中 `tool: feishu_proposal_nudge`。

2. **真机到点抽测（建议上级抽签）**  
   用 playbook：建一条 **数分钟后** 的 DDL 定时 → 到点看飞书是否**同会话**出现文字 + 进度卡。  
   该步依赖租户凭证与 Channel 在线，属现场验收，不替代 L1。

3. **Gateway 凭证**  
   定时在 Session/Gateway 进程触发；`PSI_FEISHU_APP_ID` / `SECRET` 须在 **Gateway** 侧配置，仅配 Channel 会导致到点报未配置（既有坑，本期未改）。

4. **与上一期交付关系**  
   提醒者 + 撰写检测结论不变；本期是其「通过后跟进投递」形态的补强，不重新打开 A/B 段结论。

---

## 7. 签字栏（可选）

| 角色 | 姓名 | 日期 | 意见 |
|------|------|------|------|
| 验收执行 | | 2026-09-08 | L1 自动化 6/6 通过；存量重建与真机到点见 §6 |
| 直属上级 | | | |
| 备注 | | | |

---

## 8. 附件索引

| 文件 | 用途 |
|------|------|
| 本报告 | 上级验收用测试报告 |
| `2026-09-08-proposal-e2e-poc-playbook.md` | 自然对话 E2E 待测脚本（含到点步） |
| `2026-09-03-proposal-capabilities-delivery.md` | 上一期提醒者 + 撰写检测交付说明 |
| `agents/feishu/tools/feishu_proposal_nudge.py` | 到点同拍工具 |
| `agents/feishu/skills/proposal-writing-standard/SKILL.md` | §C 同步定时硬约束 |
| `agents/feishu/tests/test_feishu_proposal_nudge.py` | nudge 单测 |
| `agents/feishu/tests/test_schedule_manage.py` | 建定时 / 拒残缺 args |
