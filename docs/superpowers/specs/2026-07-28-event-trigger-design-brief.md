# 触发器（事件驱动 Trigger）方案说明 —— 供评审

> **日期**：2026-07-28  
> **目的**：汇总当前设计结论、落地位置、与「catalog 是否要穷举」相关的麻烦点，便于上级拍板。  
> **相关草案**：`docs/superpowers/specs/2026-07-27-session-event-protocol-draft.md`  
> **目录归属**：`docs/superpowers/specs/2026-07-28-triggers-in-agent-package.md`（triggers **已归入 agent 包**，与 schedules 同根）。  
> **状态摘要**：Session 协议 + `POST /events` + `TRIGGER.md` 匹配/fire 已落地；Feishu Channel 已接通「入群」→ 信封；haitun 侧有 `trigger_manage` / `feishu-event-remind` skill。审批/评论仍为 Channel 短路，未进触发器协议。  
> **推拉问题**：**已解决**（见 §0 / §5）——Portal + `POST /events` + `agent._lock`，无需另建全局 Queue。

---

## 0. 核心概括（评审口播）

1. **TRIGGER 层 = 与 `TASK.md` 同级的规则文件**（落在 **agent 包** `triggers/`，与 `schedules/` 同根）  
   接口（协议、Channel 映射、订阅、`/events`、fire 运行时）都就绪之后，TRIGGER 只写「订哪类已有事件、filter、干什么」。**不涉及接口实现**，因此可以由 **agent**（`trigger_manage`）根据 NL 创建/修改。

2. **Catalog = `event` 字段的合法取值集合**  
   单条 TRIGGER 的 `event: …` 必须落在 catalog 内。相对定时：  
   - 字段角色上 **`event` ↔ `cron`**（都回答「何时」）；  
   - **catalog 全体** ≈ 触发器侧「何时」的**封闭枚举表**；定时的 cron 则是开放时间表达式。  
   - 再收窄用 **`filter`**（对信封 `payload` 精确匹配）。

3. **`event` / catalog 不能由用户或 agent 简单包装**  
   背后是接口契约：Session 校验、Channel mapper、飞书事件订阅与权限、发版。新事件类型须**写死进协议 + 适配并发版**；agent 只能选用已有 `event` 名写 TRIGGER。

4. **推送 vs 拉取：已解决，不挡触发器**  
   飞书 WS 是推；Session 以请求/`dispatch` 消费。转换不靠单独「队列模块」，而靠：  
   Channel `BlockingPortal`（进主 loop）→ `post_event` / `POST /events`（跨进程邮箱）→ `handle_event` + `agent._lock`（串行拉取式执行）。与上级 merge+Queue demo **目标等价、实现分散**。

---

## 1. 要解决什么问题

两类「自动干活」：

| 类型 | 叫醒方式 | 配置文件 |
|------|----------|----------|
| **定时（schedule）** | 本机 cron / `once_at` | `schedules/*/TASK.md` |
| **触发器（trigger）** | 外部平台推送（飞书等） | `triggers/*/TRIGGER.md` |

触发器要回答：

1. 系统**认哪些**外部事实（事件类型）？  
2. 用户/agent **订了哪些反应**（规则）？  
3. 推送进来后如何与定时、聊天**串行**执行（同一 Session 锁）？

---

## 2. 推荐方案（分层）

```text
飞书等平台  --push-->  Channel（映射）  --POST /events-->  Session（协议校验 + 匹配 TRIGGER + fire）
                                                              │
用户 NL ----agent----> 只写 TRIGGER.md（订已有 catalog 事件）   │
                                                              ▼
                                                    fire=tool | fire=prompt
```

| 层级 | 职责 | 不负责 |
|------|------|--------|
| **Session 协议** | catalog、信封形状、校验、`POST /events` | 解析飞书私有 JSON；用户业务规则 |
| **Channel** | 平台推送 → 填信封 → `post_event`；线程桥接（Portal） | 发明 catalog 名；NL 订规则 |
| **TRIGGER.md** | `event` + `filter` + `fire`（用户侧规则，可很多） | 定义新事件类型 |
| **tools/*.py** | 真正动作（发消息、联表等） | 订阅「何时」 |

**刻意为之**：

- Agent **只注册 TRIGGER**，**不**在运行时注册 catalog。  
- Catalog 增长 = 发版级能力（+ Channel mapper + 飞书后台订阅），按需加，不按自然语言穷举。  
- 「推→可处理」不单独做全局 Queue：Channel 用 `BlockingPortal` 进主 loop，跨进程用 HTTP `/events` 当邮箱，Session 用 `agent._lock` 串行消费（与 schedule/聊天共用）。

---

## 3. 协议写在哪、长什么样

### 3.1 位置

| 用途 | 路径 |
|------|------|
| 信封 / catalog / 校验 | `src/psi_agent/session/event_protocol.py` |
| HTTP 进门 | `src/psi_agent/session/server.py` → `POST /events` |
| 处理入口 | `SessionAgent.handle_event` |
| 匹配与开火 | `src/psi_agent/session/trigger_registry.py` |
| 层约定文档 | `src/psi_agent/session/AGENTS.md` § Event 协议 |
| Channel 适配（入群示例） | `src/psi_agent/channel/feishu/client.py`（`map_*` + `post_event`） |
| Channel 传输 | `ChannelCore.post_event` → 同 socket 上 `POST /events` |
| 用户规则 | **agent 包** `triggers/<name>/TRIGGER.md`（与 `schedules/` 同根） |
| Agent 工具 / skill | `trigger_manage`；`skills/feishu-event-remind` |

**不放**：Gateway 业务、haitun skill 里私自定义协议名、Session 直接吃飞书 SDK 原始 event type。

### 3.2 信封（协议写法）

一次投递 = 一个 JSON 对象（HTTP body），逻辑类型为 `EventEnvelope`：

```json
{
  "schema_version": 1,
  "source": "feishu",
  "event": "feishu.chat.member_added",
  "idempotency_key": "feishu:im.chat.member.user.added_v1:<event_id>:<member_open_id>",
  "routing": {},
  "payload": {
    "chat_id": "oc_xxx",
    "member_open_id": "ou_xxx",
    "member_name": "张三",
    "operator_open_id": "ou_op",
    "chat_type": "group"
  }
}
```

- `event`：**必须**落在 Session `EVENT_CATALOG`；未知 → **HTTP 400**（刻意早暴露映射错误）。  
- `payload`：按该 catalog 条目的必填/可选字段校验。  
- `source`：有限集合（`feishu` / `telegram` / `gateway` / `test`）。

### 3.3 当前 catalog（已写入协议的事件名）

| Catalog `event` | 含义 | Channel 是否已映射进 `/events` |
|-----------------|------|--------------------------------|
| `feishu.chat.member_added` | 有人进群 | **是** |
| `feishu.chat.member_removed` | 有人退群 | 否（预留） |
| `feishu.im.message_received` | IM 消息（触发器用） | 否；普通聊天仍走 `/chat/completions` |
| `telegram.chat.member_joined` | TG 入群 | 否（预留） |

说明：Channel 里的 `message` / `comment` / `approval_instance` **已是事件驱动**，但是 **Channel 短路**（对话或 DM），**未**进入上述 catalog / TRIGGER 体系。

### 3.4 TRIGGER 完整形态（用户规则写哪）

路径：`{agent}/triggers/<name>/TRIGGER.md`（与 schedules 同区）

```markdown
---
name: group-welcome-sales
description: 销售群新人进群发欢迎语
event: feishu.chat.member_added          # 必须已在 catalog
source: feishu                           # 可选
filter:                                  # 对 payload 精确子集匹配；{} = 不限制
  chat_id: oc_真实群id
visibility: silent                       # silent | display
run_once: false
fire: tool                               # tool | prompt
tool: feishu_message_send                # fire=tool 时必填
tool_args:
  receive_id: oc_真实群id
  receive_id_type: chat_id
  text: 欢迎新人进群
---

正文：fire=prompt 时作为 user 消息；fire=tool 时多为备注。
```

- **注册事件类型（catalog）**：人改 `event_protocol.py` + Channel mapper + 发版。  
- **注册触发规则（TRIGGER）**：agent / `trigger_manage` 写 workspace 文件，**即时生效**（refresh）。

---

## 4. 和定时 schedule 的对照（迁移心智）

| | Schedule | Trigger |
|--|----------|---------|
| 配置 | `TASK.md` | `TRIGGER.md` |
| 叫醒 | `_run_one` + cron sleep | Channel `POST /events` |
| 「何时」 | 时间表达式 | `event` + `filter` |
| 开火 | `fire=tool\|prompt` | 同左 |
| 文件是否上网传 | 否，进内存 `Schedule` | 否，进内存 `Trigger`；传的是**信封** |

触发器相对定时，多的是：**Channel 映射 + 信封协议 + 匹配**；fire 与 md 身份同构。

**字段类比（写进方案）**：`event` ↔ `cron`；catalog 全体 = 触发器「何时」的封闭取值表；`filter` = 额外收窄。Catalog/`event` **写死**（接口相关）；TRIGGER **可 agent 写**（纯规则）。

---

## 5. 推送 vs「拉取」——**已解决**

> **结论（已拍板进方案）**：不存在「飞书只能推、我们只能拉 → 触发器做不了」。推拉已用现有接口桥接，**不必**为触发器再引入全局 `asyncio.Queue` 模块。

飞书是 **WS 推送**；上级 demo 用 Queue 把多路生产合成一路 `async for` 消费——那是教学模型。本方案等效职责如下（**已落地**）：

| 步骤 | 位置 | 作用 | 对应 demo |
|------|------|------|-----------|
| SDK 回调 → 主 loop | Feishu Channel `BlockingPortal.start_task_soon` | 跨线程 | ≈ `pump` |
| 跨进程投递 | `ChannelCore.post_event` ↔ Session `POST /events` | 类邮箱 | ≈ `Queue` |
| 串行执行 | Session `handle_event` + `agent._lock` | 与聊天、schedule 互斥 | ≈ `async for` 消费 |

仅当将来需要「HTTP 快速 200、后台再 fire / 削峰」时，才考虑在 **Session 收件之后、`dispatch` 之前** 加显式 Queue；**不**进 catalog，**不**进各 Channel 私有队列。当前无此需求。

---

## 6. 着重说明：Catalog「穷举」麻烦点

### 6.1 问题从哪来

有人直觉：用户需求未知 → catalog 应尽量做大，把进群、退群、开会、消息、填表、转正…都预先定义。

### 6.2 为什么不能（也不该）穷举

1. **一条可用 catalog ≠ 一个字符串**  
   还要：Channel mapper、飞书开放平台订阅与权限、payload 契约、单测、skill 对照。表越大，**未接通的假能力**越多（TRIGGER 能建、永远不火）。

2. **口语无限，平台事实有限**  
   「有人进群提醒我 / 新人来了喊一声」共用 `feishu.chat.member_added`。穷举的是**外部事实种类**，不是自然语言种类；后者靠 **多条 TRIGGER** 消化。

3. **Agent 不应运行时注册 catalog**  
   Agent 改不了飞书订阅与 mapper；在 Session「注册新事件名」只会制造无法投递的规则。Agent 只订 **TRIGGER**。

4. **发版耦合是预期的**  
   **新事件类型**（系统第一次支持某种推送）→ 改协议 + Channel → **用户更新版本**。  
   **新触发规则**（已有事件上再订一条）→ 只写 TRIGGER → **不用发版**。  
   定时不需要大 catalog，因为「何时」只有时间一维；触发器需要小 catalog，因为「何时」是异构外部事实——这是维度差异，不是漏做穷举。

5. **部分业务根本不该进 catalog**  
   - 纯定时提醒 → schedule  
   - 对话里当场查表/填表 → tool  
   - 飞书 aPaaS 即可闭环 → 可不经 Haitun  
   - 规则极少且写死 → Channel 短路（现有审批路径）  

### 6.3 推荐扩法

- **按垂直切片**：有真实需求 + 能映射 + 能测，再加一条。  
- **当前生产主路径**：`feishu.chat.member_added`；其余为预留。  
- **HR「转实习/转正式」**：飞书侧已有可抓推送 `contact.user.updated_v3`（比对 `employee_type`），**不是**缺平台事件；若走触发器，再**按需**增加一条收窄后的 catalog（如 `feishu.contact.employee_type_changed`）+ mapper，而不是先堆十几种 HR 事件。  
- 联表规则进 **Mapping 资产**，不进 tool 参数、不进 catalog。

### 6.4 若把协议整层挪到 Channel

可行，但等于触发器变成「飞书 Channel 插件」：多 Channel 重复实现、与 schedule 不对称、NL/`trigger_manage` 更难、Session 单测变弱。  
**评审建议**：协议与匹配留 Session；Channel 只适配。

---

## 7. 端到端示例（入群）

```text
用户：「销售群有人进群提醒我」
  → agent：trigger_manage 写 TRIGGER（event=feishu.chat.member_added, filter.chat_id=…, fire=tool）

之后飞书推 im.chat.member.user.added_v1
  → Channel map → 信封 event=feishu.chat.member_added
  → POST /events
  → 匹配 TRIGGER → feishu_message_send(...)
```

---

## 8. 请上级拍板的点

1. **触发器协议留在 Session**（catalog + `/events` + TRIGGER），Channel 只映射 —— 是否认可？  
2. **Catalog 按需加长、禁止 agent 运行时扩 catalog、禁止为「未知需求」预穷举** —— 是否认可？  
3. **P1 范围**：以入群竖切为准；人员类型变更 / 联表 Mapping 作为后续切片 —— 是否同意优先级？  
4. **审批/评论**继续 Channel 短路，暂不收编进 TRIGGER —— 是否保持？

---

## 9. 参考代码与文档

- `src/psi_agent/session/event_protocol.py`  
- `src/psi_agent/session/trigger_registry.py`  
- `src/psi_agent/session/AGENTS.md`（Event 协议节）  
- `src/psi_agent/channel/feishu/client.py`（member_added / approval / comment）  
- `src/psi_agent/channel/AGENTS.md`  
- `docs/superpowers/specs/2026-07-27-session-event-protocol-draft.md`
