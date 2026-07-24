# Schedule display：触发 user 也应可展示

**日期**: 2026-07-23  
**状态**: 协议待修正（现行实现不合理；**尚未**改代码 / 未写入 Session `AGENTS.md` 为现行原则）  
**相关**: `session/AGENTS.md` Schedule 节、`schedule_registry.py`、`history_display.py`

---

## 现行行为（有问题）

- 定时触发时：**user 恒为** `kind: schedule.silent`（`schedule_registry`）
- `visibility: display` 时：仅 assistant/tool 打成 `schedule.display`
- Gateway `/history` 白名单：`schedule.display` **只放行 assistant**

后果：像「每天早上播报天气」这类可见定时任务，刷新后只见助手开口，**看不见触发那句 user**，会话页像 Agent 莫名自说自话。

---

## 应有规则

`visibility` 应同时约束**整轮**（触发 user + 本轮 assistant/tool），而不是只约束回复：

| visibility | user kind | assistant/tool kind | `/history` | pending 注入下一轮 chat |
|------------|-----------|---------------------|------------|-------------------------|
| `silent`（如 heartbeat） | `schedule.silent` | `schedule.silent` | 都不展示 | 否 |
| `display`（如晨间天气） | **`schedule.display`** | `schedule.display` | **user + assistant** 均可展示 | 是（至少 assistant 流；user 若已落盘，刷新靠 history 即可） |

心智：display =「这轮定时对话对用户可见」；silent =「整轮对 UI 隐身」。

---

## 实现时要动的点（备忘，本树不施工）

1. `schedule_registry`：user 的 `with_kind(...)` 与 `response_kind` 同用 visibility，勿写死 `KIND_SCHEDULE_SILENT`
2. `history_display.is_displayable_chat_message`：`schedule.display` 时 `user` 与 `assistant` 都放行（非空 content）
3. 同步 `session/AGENTS.md` 流程说明与白名单表；标旧行为为已废止
4. 补单测：display 任务触发后 `/history` 含成对 user+assistant；silent 仍皆不可见
5. 旧 JSONL：已落盘的 display 轮若 user 仍是 `schedule.silent`，可选迁移或展示层对「后接 schedule.display assistant 的 silent user」做兼容（实现阶段再定）

---

## 与「能力包 / 工作区 / 记录」方案的关系

正交：本条只修正 **kind 展示协议**，不改变 schedule 文件仍在能力包 `schedules/` 下加载的事实。
