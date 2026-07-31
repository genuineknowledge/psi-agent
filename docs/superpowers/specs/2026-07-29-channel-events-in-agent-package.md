# Channel 事件定义落在 Agent 包 —— 最终设计

> **日期**：2026-07-29  
> **状态**：接口已落地（Session 薄管道 + agent `channel_events/` + Feishu 转发）  
> **取代**：以 Session `EVENT_CATALOG` 作业务事件硬门槛的旧设想

---

## 结论（对称）

```text
官方推送 ∪ 内部合成（均在 Channel / agent channel_events 定义）
        │  同一套 map → 信封
        ▼
ChannelCore.post_event → Session POST /events
        │
        ▼
TriggerRegistry 匹配 triggers/ → fire
```

| 层 | 职责 |
|----|------|
| **Session** | **仅**统一接收 `POST /events` + 按 TRIGGER 发放；薄信封形状校验（无业务 catalog 门） |
| **Agent `channel_events/<channel>/`** | **事件注册与维护入口**（≈ 加 tool）；`EVENT.yaml` + `map.py` |
| **Agent `triggers/`** | 挂钩（≈ schedule TASK）；NL 只订已接通的 `event` 名 |
| **src channel** | 加载 defs、订平台 processor、调用 `post_event`（框架胶水，不放业务清单） |

加事件按需加目录；**不要**指望任意 NL「xx 事」永远可行。

**后续开发对接（必读交付物）**：`docs/superpowers/specs/2026-07-29-channel-events-developer-guide.md` —— 有触发器需求时默认在 `channel_events/` 反复注册事件。

---

## 维护入口

```text
{agent}/channel_events/feishu/<slug>/
  EVENT.yaml     # name, source, kind=platform_map|synthetic, platform_event?
  map.py         # platform_map: def map_event(raw) -> list[envelope_dict]
  produce.py     # synthetic: async def produce(ctx); await ctx.emit(envelope)
```

Feishu Channel：`--agent` / `PSI_AGENT` 指向该包；启动后注册 `platform_event`，并在 TaskGroup 中启动全部 `synthetic` 的 `produce.py`。

**验收**：Feishu 接线完成后，后续开发者只按 developer guide 改 agent 包即可，不必再为每个事件改 Channel 源码。
---

## Session 接口

- `POST /events` → `SessionAgent.handle_event`
- `ChannelCore.post_event(envelope)`
- 信封：`source` / `event` / `payload` / 可选 `raw_*` / `routing` / `idempotency_key`

业务事件名 **不**在 Session 表里封闭校验。
