# Triggers 归入 Agent 包 —— 已落地变更与后续设计

> **日期**：2026-07-28  
> **状态**：本文件描述的「triggers 与 schedules 同区（agent 包）」**已落地**；下文 **§3 起为后续工作**。  
> **相关**：`docs/superpowers/specs/2026-07-28-event-trigger-design-brief.md`（触发器总方案）；`docs/superpowers/specs/2026-07-29-event-catalog-role.md`（catalog 角色 / 有无对比 / 维护清单）

---

## 1. 已落地：放置与读取

### 1.1 决定

| 项 | 决定 |
|----|------|
| TRIGGER 落盘位置 | **`{Session.agent}/triggers/<name>/TRIGGER.md`** |
| Session 加载 | `TriggerRegistry.load(agent_root / "triggers")` |
| Agent 写入 | `trigger_manage` → `resolve_agent() / "triggers"` |
| 与 schedule | **同根**（均在 agent 包） |
| Workspace | 用户打开目录；**不**再承载 triggers（`--agent` 分根时尤其重要） |

单根兼容：`--agent` 为空时 `agent_root == workspace`，行为与旧单目录一致。

### 1.2 代码触点（已改）

- `src/psi_agent/session/agent.py` — create 加载；turn 开始 / `stop` 后 `trigger_registry.refresh()`
- `src/psi_agent/session/trigger_registry.py` — 注释
- `src/psi_agent/session/AGENTS.md`
- `agents/feishu/tools/trigger_manage.py`、`_runtime_paths.py`
- `agents/feishu/AGENTS.md`、`skills/feishu-event-remind/SKILL.md`

### 1.3 含义（飞书多用户）

- **共享同一 agent 包**（如 `--feishu-shared-workspace` 且 agent=haitun 根）：全员共享同一套 TRIGGER/TASK。  
- **每用户独立 agent 子树**：规则按人隔离。  
- 这是产品选择，不是协议 bug；触发器规则跟「能力包」走，不跟「用户随便打开的文件夹」走。

---

## 2. 核心分层回顾（不变）

```text
catalog / 信封 / POST /events     → Session 框架（写死，发版）
Channel mapper                    → Channel（写死，发版）
triggers/*.TRIGGER.md             → agent 包（agent 可写）
schedules/*.TASK.md               → agent 包（agent 可写）
tools/*.py                        → agent 包
用户文档 / 相对 IO                → workspace
```

推拉转换：**已解决**（Portal + `/events` + lock）。见总方案 §0 / §5。

---

## 3. 后续设计方案

### 3.1 P0 — 对齐 `schedule_manage` 写入根（一致性债）

**现状**：Session **读** `agent/schedules`，但 `schedule_manage` 仍写 `resolve_workspace()/schedules`。  
分根时会出现「工具写成功、runner 读不到」。

**建议**：`schedule_manage` 改为 `resolve_agent() / "schedules"`，与 `trigger_manage`、Session 加载一致；单测与 `feishu-schedule-message` 文档同步。

### 3.2 P1 — 触发器竖切补齐（协议能力，非目录问题）

| 项 | 说明 |
|----|------|
| `feishu.chat.member_removed` | catalog 已有；补 Channel mapper + 单测 |
| 人员类型变更 | 按需加 catalog（如 `feishu.contact.employee_type_changed`）+ `contact.user.updated_v3` 映射；先确认租户 `employee_type` 枚举 |
| skill / 对照表 | 仅列出**已接通** event；未接通 NL → 明确拒绝，不 invent catalog |

Catalog **仍禁止**海豚运行时写入生效表；可选后续「能力申请草案」文件（未点亮）。

### 3.3 P1 — 联表抽象（HR 录用填表）

不把规则塞进 tool 参数：

1. 原语：补 `update_record` / batch（若缺）  
2. Mapping 资产：建议同放 **agent 包** `bitable-mappings/<name>/MAPPING.md`（与 TRIGGER 同区，便于共享 agent）  
3. `bitable_mapping_apply(mapping_name, source_key=…)`  
4. TRIGGER `fire=tool` → apply；或先对话/审批短路验证联表  

### 3.4 P2 — 可选增强

| 项 | 说明 |
|----|------|
| turn 内创建 TRIGGER 后同事件立刻可见 | 已有 `dispatch`/`refresh` 与 stop 后 refresh；观察是否够用 |
| Session 内显式事件 Queue | 仅当需要快速 ACK + 削峰时再加；非目录问题 |
| 审批/评论收编进 TRIGGER | 可选；现 Channel 短路可继续 |
| 安装包只读 agent | 若 agent 打进只读介质，TRIGGER 写入需可写 agent 数据目录或明确「规则可写根」——产品化时再定 |

### 3.5 明确不做

- 海豚运行时改 `EVENT_CATALOG` / 无 mapper 的假 event  
- 为未知需求穷举 catalog  
- 把触发器协议整层挪到 Feishu Channel（除非放弃跨 Channel 统一）

---

## 4. 建议落地顺序

```text
1. [本变更] triggers 读写 → agent 包          ✅
2. schedule_manage 写入 → agent 包            ← 下一刀（消分根 bug）
3. member_removed mapper + 测试
4. bitable 原语 + Mapping + apply
5. （可选）employee_type_changed 竖切
6. （可选）capability-request 草案流程
```

---

## 5. 验收清单（本变更）

- [ ] `SessionAgent.create` 从 `agent_root/triggers` 加载  
- [ ] `trigger_manage` 写入 `resolve_agent()/triggers`  
- [ ] 单根下 haitun 测试：`test_trigger_manage` / `test_event_protocol` 通过  
- [ ] AGENTS.md（session + haitun）与 skill 表述一致  

（实现后跑：`uv run pytest tests/psi_agent/session/test_event_protocol.py agents/feishu/tests/test_trigger_manage.py --override-ini="addopts="`）
