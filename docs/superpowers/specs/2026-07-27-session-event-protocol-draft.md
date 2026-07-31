# Session Event Protocol（草案 · 已落地 P1 实现）

> **状态**：Session 侧 P1 已实现（`event_protocol` / `trigger_registry` / `POST /events`）。  
> Channel 映射与 haitun `trigger_manage`/skill **尚未**接线。  
> **层级**：Session 层通用协议（channel 无关）。飞书等特化只在对应 Channel 适配。  
> **与 schedule**：平行能力；schedule 用 cron 叫醒，本协议用外部推送叫醒；开火语义对齐 `fire=tool|prompt`。

---

## 1. 拟落地位置（结构）

| 用途 | 拟路径 | 说明 |
|------|--------|------|
| **协议文档（审定后）** | `src/psi_agent/session/AGENTS.md` → 新节「Event 协议」 | 与 schedule / ChannelAdapter 同级约定 |
| **类型与校验（实现时）** | `src/psi_agent/session/event_protocol.py` | 信封 dataclass、catalog 常量、解析/校验纯函数 |
| **进门路由（实现时）** | `src/psi_agent/session/server.py` | 新增 `POST /events`（建议；见 §3） |
| **消费方（实现时）** | `src/psi_agent/session/trigger_registry.py`（名可再定） | 匹配 TRIGGER 配置 → fire |
| **Channel 适配（实现时）** | 各 `channel/*/client.py` | 平台事件 → 填本信封 → POST Session |
| **配置实例（workspace）** | `triggers/<name>/TRIGGER.md`（名可再定） | 使用本协议的 `event`/`filter`，不重新定义协议 |
| **本草案存放（当前）** | `docs/superpowers/specs/2026-07-27-session-event-protocol-draft.md` | 评审用；勿当已生效 AGENTS |

**不放**：haitun skill、Gateway 业务逻辑、飞书 SDK 原始 event 名直接进 Session。

---

## 2. 设计原则（刻意为之）

1. **Session 拥有协议**：合法 `event` 名、信封字段、进门路径由 Session 定义。  
2. **Channel 只映射**：平台原始事件 → 本信封；禁止 Session 解析飞书/Telegram 私有 JSON。  
3. **与具体业务解耦**：协议不写「提醒谁」「调哪个工具」；那是 TRIGGER 配置 + `fire`。  
4. **与 chat 正交**：事件进门默认**不**当作普通用户对话；避免污染 `kind=chat` 历史（除非显式 `fire=prompt` 且配置要求）。  
5. **P1 catalog 宜小**：先少后多；未列出的 `event` 名 → 拒绝或忽略并打日志。

---

## 3. 投递绑定（怎么推进 Session）

### 3.1 建议：新建专用入口（草案首选）

```http
POST {channel_socket}/events
Content-Type: application/json

<EventEnvelope JSON>
```

- **成功**：`202 Accepted` 或 `200` + 简短 `{ "ok": true, "matched": N }`（实现时二选一，审定）。  
- **校验失败**：`400` + `{ "error": "..." }`（非法 JSON / 缺字段 / 未知 event）。  
- **与现有对比**：`POST /chat/completions` 仍只服务对话；事件不走该路径（刻意分离）。

### 3.2 备选（不推荐作默认）

复用 `POST /chat/completions`，在 body 中带扩展字段标记 event——易与 chat / history / extra_params 搅在一起，仅当兼容压力极大时再议。

### 3.3 谁可以调用

- **预期调用方**：本进程或本机 Channel（Feishu/Telegram/…）经已解析的 `channel_socket`。  
- Gateway：可选转发/路由到正确 Session，**不**解释 `event` 语义。

---

## 4. 信封形状（EventEnvelope）

一次 POST = 一次「事件发生」。字段如下（名称可审，语义拟冻结）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema_version` | `int` | 是 | 协议版本；P1 固定 `1` |
| `source` | `str` | 是 | 事件源，小写枚举：`feishu` / `telegram` / `gateway` / …（见 §5.1） |
| `event` | `str` | 是 | 逻辑事件名，**必须**落在 §5 catalog；建议 `source.domain.action` |
| `occurred_at` | `str` | 否 | ISO-8601 时间；缺省由 Session 用接收时刻 |
| `idempotency_key` | `str` | 否 | 去重键（平台 event_id 等）；有则同一 key 重复投递可忽略 |
| `routing` | `object` | 否 | 路由提示，**不参与** TRIGGER 匹配（见下） |
| `payload` | `object` | 是 | 客观字段；键集合由该 `event` 在 catalog 中声明 |

### 4.1 `routing`（可选，给 Gateway/多用户）

```json
{
  "open_id": "ou_xxx",
  "session_hint": "feishu-ou_xxx"
}
```

Session 单实例部署可忽略；**Trigger 匹配只看 `source`/`event`/`payload`，不看 `routing`。**

### 4.2 JSON 示例（飞书入群 → 归一化后）

```json
{
  "schema_version": 1,
  "source": "feishu",
  "event": "feishu.chat.member_added",
  "occurred_at": "2026-07-27T09:00:00+08:00",
  "idempotency_key": "feishu:im.chat.member.user.added_v1:evt_xxx",
  "routing": { "open_id": "ou_owner_optional" },
  "payload": {
    "chat_id": "oc_xxx",
    "member_open_id": "ou_new",
    "member_name": "张三",
    "operator_open_id": "ou_op",
    "chat_type": "group"
  }
}
```

---

## 5. 事件目录（Catalog）· P1 草案

### 5.1 `source` 枚举（可扩展）

| `source` | 含义 |
|----------|------|
| `feishu` | 飞书 / Lark Channel |
| `telegram` | Telegram Channel（预留） |
| `gateway` | Gateway 内生事件（预留） |
| `test` | 单测注入 |

### 5.2 `event` 条目（P1 建议只落地第一条）

#### `feishu.chat.member_added`

| | |
|--|--|
| **含义** | 有用户加入群聊（真人；是否含机器人由 Channel 过滤策略决定，建议默认排除 bot） |
| **payload 必填** | `chat_id` (str), `member_open_id` (str) |
| **payload 可选** | `member_name` (str), `operator_open_id` (str), `chat_type` (str) |
| **Channel 映射提示** | 飞书侧如 `im.chat.member.user.added_v1` 等 → 本 `event`；原始名不得出现在 TRIGGER.md |

#### 预留（先写进目录草案，P1 可不实现投递）

| `event` | 含义 | payload 草案 |
|---------|------|----------------|
| `feishu.chat.member_removed` | 成员退群/被移 | `chat_id`, `member_open_id` |
| `feishu.im.message_received` | 非 @ 策略外的消息旁路（慎用） | `chat_id`, `message_id`, `sender_open_id` |
| `telegram.chat.member_joined` | Telegram 入群 | `chat_id`, `user_id` |

**未知 `event`**：Session 返回 400 或 202+`matched=0` 且 WARNING（审定二选一；建议 **400** 以便 Channel 早发现映射错误）。

---

## 6. 配置侧用法（TRIGGER，对标 TASK 的 cron）

> 配置格式属「复刻 schedule」的下一层；此处只规定 **如何引用本协议**，完整 TRIGGER schema 可另稿。

TRIGGER YAML 中与协议相关的字段：

```yaml
name: notify-join-xxx
source: feishu                         # 可选；缺省则只按 event 前缀推断，或要求显式
event: feishu.chat.member_added        # 必须 ∈ catalog
filter:                                # 对 payload 做子集匹配（精确相等）
  chat_id: oc_xxx
fire: tool
tool: feishu_message_send
tool_args: { ... }
visibility: silent
```

**匹配规则（草案）**：

1. `envelope.event == trigger.event`  
2. 若 trigger 写了 `source`，则 `envelope.source == trigger.source`  
3. `filter` 中每个 key：`envelope.payload.get(key) == filter[value]`（缺 key → 不匹配）  
4. 可命中多条 → 逐条 fire（顺序：稳定按 name 排序，审定）

**对称关系**：

| schedule | event trigger |
|----------|----------------|
| `cron` / `once_at` | `event` + `filter` |
| 内部 sleep 叫醒 | `POST /events` 推送叫醒 |
| `fire` / `tool` / `tool_args` | 同左（复用语义） |

---

## 7. Session 进门后行为（协议级，非 skill）

收到合法信封后（草案）：

1. 校验 `schema_version` / catalog / payload 必填键。  
2. （可选）`idempotency_key` 去重。  
3. 交给 TriggerRegistry：`match` → 对每条命中执行 `fire`。  
4. **不**默认调用 LLM；仅 `fire=prompt` 的 trigger 才跑 agent turn。  
5. 历史 `kind`：建议 `trigger.silent` / `trigger.display`（与 schedule 同构，名称待审）。

---

## 8. 非目标（本协议不做）

- 在协议里写死 `feishu_message_send` 或提醒文案  
- 自然语言条件（「有人进群就…」）由运行时理解  
- 用 heartbeat 轮询冒充事件  
- 把 AppData / 密钥放进信封或 ContextVar  

---

## 9. 请审要点（请批注）

1. **入口**：默认 `POST /events` 是否同意？  
2. **未知 event**：400 vs 静默忽略？  
3. **P1 catalog**：是否只做 `feishu.chat.member_added`？  
4. **filter**：仅精确相等是否够用（P1）？  
5. **多 trigger 命中**：全部执行 vs 第一条？  
6. **命名**：`event_protocol.py` / `triggers/` / `trigger.silent` 是否 OK？

---

## 10. 落地清单

- [x] 精简并入 `session/AGENTS.md`（Event 协议节）
- [x] `event_protocol.py` + 单测
- [x] `server.py` 注册 `POST /events`
- [x] `trigger_registry.py`（match + fire=tool/prompt + TRIGGER.md 加载）
- [ ] Channel feishu 映射 + 投递
- [ ] haitun `trigger_manage` + 独立 skill
