# Session Event Catalog —— 是什么、有无差别、怎么维护

> **日期**：2026-07-29  
> **状态**：说明文档（对齐当前实现与「规范化 + raw 二者兼得」约定）  
> **代码**：`src/psi_agent/session/event_protocol.py`（`EVENT_CATALOG` / `parse_event_envelope`）  
> **相关**：`2026-07-28-event-trigger-design-brief.md`、haitun `skills/feishu-event-remind/SKILL.md`

---

## 0. 先分清：通用转发接口 ≠ Catalog

**Catalog 不是转发接口。** 它只是转发管路上的**词汇表 / 校验契约**——有了它，多 Channel 才能往**同一条管道**里塞同形信封，从而实现「统一转发」。

### Session 侧通用转发接口在哪

| 层 | 位置 | 职责 |
|----|------|------|
| **HTTP 进门（管道）** | `session/server.py` → `POST /events` | 与对话管 `POST /chat/completions` **并列**的通用入口；不识别「入群 / 审批」业务 |
| **Handler** | `SessionAgent.handle_event`（`session/agent.py`） | 解析 JSON → `parse_event_envelope` → 持 `_lock` → `TriggerRegistry.dispatch` |
| **信封形状** | `session/event_protocol.py` 的 `EventEnvelope` | 通用字段：`source` / `event` / `payload` / 可选 `raw_*` / `idempotency_key` / `routing` |
| **消费（挂钩，不是转发）** | `session/trigger_registry.py` | 匹配 `triggers/*/TRIGGER.md` → `fire=tool\|prompt` |

```text
Channel（任意平台）
    │  填信封（可带规范 event + raw_event）
    ▼
POST /events          ←【通用转发接口 / 管道】Session 只认信封，不认飞书 SDK
    │
    ├─ catalog 校验     ←【支持层】规范名合法 + payload 契约（可选强化，非管道本身）
    ▼
TriggerRegistry       ←【钩子】有 TRIGGER 才开火；无钩子则 matched/fired 空
```

**刻意为之：**

- **转发** = 把一次外部事实送进 Session（`/events`）。管道对业务事件类型尽量无知。  
- **Catalog** = 规定「信封上的 `event` 允许叫什么、payload 至少有哪些键」。它让管道上的内容**可统一、可校验、可跨 Channel 对齐**，但**不是**管道本身。  
- **TRIGGER** = 登记「这类进来的事实命中后干什么」。无 TRIGGER ≠ 转发失败；只是不开火。

Channel 侧应对齐同一管道（填信封后 `POST` 到该 Session 的 `/events`）。平台特化（飞书 WS 类型 → 信封）停在 Channel mapper，**不要**把飞书原名解析塞进 `server.py`。

---

## 1. Catalog 究竟是干什么的

**Catalog = Session 认可的、有限的、规范化事件名表 + 每条事件的 payload 契约。**

它回答三件事：

| 问题 | Catalog 的答案 |
|------|----------------|
| **进门叫什么** | 信封上的 `event` 必须是表里的名字（如 `feishu.chat.member_added`），否则 `POST /events` → 400 |
| **进来至少带什么字段** | 每条 `EventSpec.required_payload`（如入群要有非空 `chat_id`、`member_open_id`） |
| **TRIGGER / skill / Channel 共用哪套词汇** | 同一字符串：Channel 映射目标、TRIGGER 的 `event:`、agent 登记时选用的名 |

**它不是：**

- **转发接口 / 邮箱**（那是上面的 `POST /events` + Channel 投递；catalog 只是其上的统一词汇支持）
- 飞书开放平台事件全集（平台事件远多于我们表）
- Agent 运行时可扩展的动态表（**刻意为之**：海豚不能「发明一个 event 名就立刻全局生效」）
- TRIGGER 本身（TRIGGER 是**钩子**：在已认可的 event 上再 `filter` + `fire`）

**「有了 catalog 可以统一转发」怎么理解：**

| | 含义 |
|--|------|
| **统一的是什么** | 各 Channel 都往**同一** `/events` 管道塞**同形信封**；Session / TRIGGER / skill 共用同一套 `event` 名 |
| **统一的不是什么** | 不是「catalog 替代了转发」；没有 `/events`，光有一张表什么也进不来 |
| **catalog 的贡献** | 把「随便一串平台原名」收成有限方言，管道内容可校验、可跨源对齐，转发才谈得上「统一」 |

**和 cron 的类比（角色对称、来源不对称）：**

| | Schedule | Trigger |
|--|----------|---------|
| 「何时」字段 | `cron` / `once_at` | `event` (+ `filter`) |
| 语法从哪来 | 五段 cron 是外来计时语言 | **event 名是我们自建目录** |
| 谁保证合法 | croniter + 我们钉本地墙钟 | **catalog 校验** + Channel 映射 |

---

## 2. 有 Catalog vs 没有 Catalog

### 2.1 有 Catalog（当前主路径）

```text
飞书 WS 原始类型
    → Channel mapper 填：event=规范名 + payload=约定字段
      （建议同时带 raw_event / raw_payload）
    → POST /events
    → parse_event_envelope：event ∈ catalog + required_payload
    → TriggerRegistry：先 event+filter，再 raw_event+raw_filter
    → fire=tool | prompt
```

**好处：**

1. **契约稳定**：TRIGGER / skill / 测试写死的是短名，不跟飞书改版字符串绑死。  
2. **跨 Channel 可对齐**：Telegram 入群可映射到同类语义（或平行的 `telegram.*` 名），Session 只认 catalog。  
3. **坏映射早失败**：Channel 写错 event 名或漏字段 → HTTP 400，不静默空跑。  
4. **产品可控**：开放哪些「事」是发版决策，不是对话里临时发明。  
5. **与 filter 配合**：payload 键名稳定（`chat_id`），skill 好教。

**代价：**

1. 每接通一类事实，要改 **三处对齐**：catalog 条目、Channel mapper、（可选）skill/对照表。  
2. 未入表的原始事件，**不能**靠 invent 名字就进 Session（除非走 raw 旁路且放宽校验——见下，当前进门仍要求 catalog `event`）。  
3. 维护负担在框架发版，不在单条 TRIGGER。

### 2.2 没有 Catalog（只推飞书原名）

想象信封只有：

```json
{ "event": "im.chat.member.user.added_v1", "payload": { /* 飞书原样 */ } }
```

**实现上会变成：**

| 维度 | 结果 |
|------|------|
| Session | 几乎变成「字符串相等管道」；payload 形状随平台漂移，`filter` 难写稳 |
| TRIGGER | 直接绑飞书版本化 event type；升级/改名要改所有 TRIGGER |
| 多 Channel | Telegram / 其它源各写各的原名，Session 无统一语义层 |
| 校验 | 要么几乎不校验（脏数据进匹配），要么在 Session 里堆平台 if/else（破坏微内核） |
| Agent 登记 | skill 必须教飞书原名；NL→原名易错；「有人进群」没有单一稳定枚举 |

**可以做，但不适合作为唯一路径：**适合「调试旁路 / 尚未映射的过渡」，不适合作为产品默认契约。

### 2.3 「二者兼得」（推荐，已对齐）

- **Catalog 仍是进门主身份**（规范名 + 契约字段）。  
- **信封额外带 `raw_event` / `raw_payload`**，TRIGGER **双写** `event` + `raw_event`。  
- **匹配**：先规范化，失败再 raw。  

这样：有 mapper 时走稳契约；mapper 暂缺或规范字段对不上时，仍可用原始类型兜底——**不放弃 catalog，也不把 Session 做成飞书专用解析器。**

| | 仅 Catalog | 仅 Raw | Catalog + Raw（二者兼得） |
|--|------------|--------|---------------------------|
| 契约/跨 Channel | 强 | 弱 | 强（主） |
| 映射未齐时可用性 | 低 | 高 | 中高（靠 raw 回退） |
| Agent 发明事件 | 禁止 | 易失控 | 仍禁止 invent catalog；raw 只作回退字段 |
| 维护点 | catalog+mapper | 全平台原名散落 | catalog+mapper+（可选）raw 对照 |

---

## 3. 维护 Catalog 要做些什么

### 3.1 什么时候加一条

仅当同时满足：

1. 有真实产品场景（如「有人进群提醒」）  
2. Channel **能稳定订阅并映射**该平台事件  
3. 愿意冻结 **payload 必填键**（给 filter / 测试用）

**不要**为「飞书文档里还有几百个事件」而预填空壳；表小而真。

### 3.2 加一条的检查清单

按顺序改（同一变更里做完，避免半接通）：

| # | 动作 | 位置 |
|---|------|------|
| 1 | 定 **规范名**（`source` 前缀 + 稳定语义，如 `feishu.chat.member_added`） | `event_protocol.py` 常量 |
| 2 | 写入 `EVENT_CATALOG`：`required_payload` 元组 | 同文件 |
| 3 | Channel：**订阅**平台事件 + **mapper** 填信封（`event` + `payload`；建议 `raw_event`/`raw_payload`） | 如 `channel/feishu/` |
| 4 | 传输：确保走 `POST /events`（非 `/chat/completions`） | ChannelCore / client |
| 5 | haitun：`trigger_manage._KNOWN_EVENTS` + `_EVENT_TO_RAW` 对照 | `tools/trigger_manage.py` |
| 6 | skill 对照表只列 **已接通** 行 | `skills/feishu-event-remind/SKILL.md` |
| 7 | 单测：parse 成功/缺字段失败；可选 dispatch / HTTP | `tests/psi_agent/session/` |
| 8 | 文档：`session/AGENTS.md` / 设计 brief 同步「刻意为之」 | 按需 |

### 3.3 刻意不做的事

- **Agent 运行时写 catalog 并立刻生效**（禁止）  
- Skill 私自发明未入表的 `event` 字符串  
- 把飞书原名写进 TRIGGER 的 `event:` 主字段冒充 catalog（原名进 `raw_event`）  
- 把 IM 普通聊天当 catalog 事件（对话仍走 `/chat/completions`）

### 3.4 运维 / 产品侧

- 飞书后台开通对应事件订阅与权限  
- Gateway/Session **与** Feishu channel 进程都配置同一套 app 凭证（触发器 `fire=tool` 发消息在 Session 进程）  
- 无 TRIGGER 时：事件仍可进门，但 **matched/fired 为空**（能力开、钩子关——刻意为之）

---

## 4. 一句话

**通用转发接口是 Session 的 `POST /events`（`handle_event`）；catalog 不是这条管道，而是管道上的规范词汇表——有了它，多 Channel 才能往同一入口塞同形信封，统一转发才成立。TRIGGER 是挂钩，不是转发。**
