# 开发者对接：`channel_events`（按需反复注册触发事件）

> **交付物**（2026-07-29）：触发器能力引入了一块**新的维护空间**。后续开发者接到「用户想要每次有 xx 就提醒/干活」类需求时，**默认动作是来这里注册事件**，而不是改 Session catalog，也不是只写 skill，也**不要**改 `src/psi_agent/channel`。  
> 产品用语：**触发器**（对标 `trigger`；用户也可说「触发事件」）。旧称「定事」已弃用。与 **定时任务**（`schedule`）成对。  
> 设计背景见同目录 `2026-07-29-channel-events-in-agent-package.md`。

---

## 验收目标（本次提交之后）

在**当前已接线的 Channel**（Feishu）下：

| 要加… | 改哪里 | 不改哪里 |
|--------|--------|----------|
| 官方推送事件（`source: feishu`） | agent `channel_events/feishu/<slug>/`（`EVENT.yaml` + `map.py`） | Session / Channel 源码 |
| 自定义合成事件（已有 `source`，如 `haitun`） | 同上（`EVENT.yaml` + `produce.py`） | Session / Channel 源码 |
| **首次**新的信封 `source` 字符串 | agent `channel_events/` **+** Session `event_protocol.KNOWN_SOURCES` | Channel 业务清单；不要为每条 event 改 Session |
| 进总线后的反应 | agent `triggers/`（`trigger_manage`） | Channel 源码 |

**重启 Channel**（`--agent` / `PSI_AGENT` 指向该包）后生效。只有换 Channel 种类、扩框架接口、修 bug 才动 `src/psi_agent/channel`。

**分层口诀（后人）：** `source` = 管道品牌（很少加）；`event` = 管道里的具体事（常加）。绝大多数注册 = **只动 agent 包加 event**；只有「新一类生产者 / 改信封形状」才动 Session `event_protocol`（新 source 进 `KNOWN_SOURCES`）。Session **没有**业务 event catalog，但会对未知 `source` 硬拒。

---

## 一句话

**有新的、可观测的「触发器」需求 → 在 agent 包 `channel_events/<channel>/` 补一条事件定义（≈ 加 tool）→ 重启 Channel；用户订反应再写 `triggers/`（≈ 订 schedule）。**  
Session 只负责 `POST /events` 统一转发 + 按 TRIGGER 开火，**没有**业务事件 catalog 要维护。

---

## 为什么要反复注册

「每次有 xx 就提醒我」**不能**对任意 xx 永远成立：平台不推、Channel 未接通，TRIGGER 写了也不会响。

产品边界：

| 情况 | 开发者做什么 |
|------|----------------|
| xx 已有 `channel_events` | 教 agent / 用 `trigger_manage` 订 TRIGGER 即可 |
| xx 稳定、可观测、值得接通 | **在本目录加事件**（本文重点） |
| xx 是时间点 | 走 `schedules/` + `schedule_manage`，不是这里 |
| xx 不可观测 / 不值得做 | 产品上拒绝或降级，不要假装 invent |

因此：**触发器能力的扩展面 = 反复往 `channel_events` 注册**，与工具、skill 同一类「按需加能力」节奏。

---

## 维护入口（在哪改）

```text
examples/haitun-workspace/channel_events/     # 或其他 Session --agent 包根
  README.md
  feishu/
    member_added/          # 官方：有人进群
      EVENT.yaml           # kind: platform_map + platform_event
      map.py               # map_event(raw) -> list[envelope]
    identity_changed/      # 官方：人事身份转变（过滤后的 user.updated）
      EVENT.yaml
      map.py
    demo_tick/             # 自定义模板：kind synthetic
      EVENT.yaml           # kind: synthetic
      produce.py           # async produce(ctx) -> None；await ctx.emit(...)
```

| 文件 | 作用 |
|------|------|
| `EVENT.yaml` | 稳定 `name`（TRIGGER 的 `event:`）、`kind`、`platform_event`（仅官方） |
| `map.py` | **官方** `platform_map`：平台原始载荷 → Session 信封 |
| `produce.py` | **自定义** `synthetic`：长驻协程，条件满足时 `await ctx.emit(信封)` |

**生产者在哪**：

| kind | 真正的生产者 | Channel 做什么 |
|------|----------------|----------------|
| `platform_map` | 飞书等平台推送 | 通用 on → `map.py` → `post_event` |
| `synthetic` | agent 包里的 `produce.py`（统一 runner 启动） | 启动/取消任务 + `ctx.emit` → `post_event` |

框架胶水（**一般不要为业务事件改这些**）：

- 加载：`src/psi_agent/channel/_event_defs.py`
- 合成 runner：`src/psi_agent/channel/_synthetic.py`
- Feishu 接线：`src/psi_agent/channel/feishu/_agent_events.py`
- 管道：`ChannelCore.post_event` → Session `POST /events`

Feishu Channel 启动需指向同一 agent 包：`--agent` 或 `PSI_AGENT`。

---

## 加一条事件的检查清单

### A. 官方推送（`platform_map`）

1. 确认飞书有对应官方 event + 权限/订阅。  
2. 新建 `channel_events/feishu/<slug>/`。  
3. `EVENT.yaml`：`kind: platform_map` + `platform_event`。  
4. `map.py`：`def map_event(raw: dict) -> list[dict]`。  
5. 重启 Channel；更新 skill 对照表（只列已接通名）。  

### B. 自定义合成（`synthetic`）

1. 确认有可观测条件（轮询 API、组合状态、内部信号等）。  
2. 新建目录（可复制 `demo_tick/`）。  
3. `EVENT.yaml`：`kind: synthetic`（**不要**填 `platform_event`）。  
4. `produce.py`：`async def produce(ctx)` — 循环/等待；`await ctx.emit({...})`；可用 `anyio.sleep`；Channel 关停会 cancel。  
5. `ctx.event_name` / `ctx.source` 已由框架填好；信封建议带 `payload`、`idempotency_key`、`routing.open_id`。  
6. 重启 Channel；需要时订 `triggers/`。  

挂钩（提醒文案、调哪个工具）用 `trigger_manage` / `triggers/`，**不要**在 `channel_events` 里写业务动作。

---

## 和 tool / skill / TRIGGER 的分工

| 空间 | 像什么 | 回答什么 |
|------|--------|----------|
| `tools/` | 动作原语 | 能调用什么 |
| `skills/` | 配方 | 怎么教模型用 |
| **`channel_events/`** | **信号源** | **什么事能进总线** |
| `triggers/` | 挂钩规则 | 进总线后干什么 |
| Session `/events` | 管道 | 怎么统一收、怎么发到 TRIGGER |

**Agent 运行时**可写 TRIGGER；**不应**让模型随便 invent `channel_events`（接入层需人审 + 发版/重启）。

---

## 刻意为之（勿当 bug 修）

- Session **无**业务 catalog 硬门槛；未匹配 TRIGGER 时 `matched/fired` 可为空。  
- 官方推送与合成事件都走**同一** `POST /events`。  
- Feishu 下新事件默认**只改 agent 包**；不要为每个事件在 Channel 再写一个 onXxx。  
- 任意 NL「xx 事」永远可行 —— **不承诺**；未接通就明确说暂不支持。  
- `demo_tick` 默认空转；仅 `HAITUN_CHANNEL_EVENTS_DEMO=1` 时发一次演示信封，正式业务勿复用其 `name`。
