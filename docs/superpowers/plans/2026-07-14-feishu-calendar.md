# 飞书日历创建日程工具 —— 实现计划

日期：2026-07-14
分支：`add-feishu-tools1`
状态：待批准

## 目标

给 agent 加"创建飞书日程"能力：在机器人主日历上建日程（标题/开始结束时间/描述），并可加参与人（open_id）。用户确认：**建在机器人主日历**（自动查 primary）+ **支持加参与人**。

## 已查证的接口与约束（3 端点，均 tenant token 可用）

- **查主日历**：`POST /open-apis/calendar/v4/calendars/primary`
  - 返回 caller 主日历列表，取其中 `calendar.calendar_id`（机器人自己的主日历）。
  - tenant token ✅；权限 `calendar:calendar:read` 或 `:readonly`；**需应用开启机器人能力**（否则 190007）。
- **创建日程**：`POST /open-apis/calendar/v4/calendars/:calendar_id/events`
  - body：`summary`(标题)、`description`(可选,支持HTML)、`start_time`/`end_time`(必填 time_info：`{timestamp:"秒级", timezone:"Asia/Shanghai"}` 或全天 `{date:"YYYY-MM-DD"}`)、`need_notification`(默认 true)。
  - tenant token ✅；权限 `calendar:calendar` 或 `calendar:calendar.event:create`；需对该日历有 writer/owner。
  - 返回 `event.event_id`。
- **加参与人**：`POST /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees`
  - query `user_id_type=open_id`；body `attendees:[{type:"user", user_id:<open_id>}]`、`need_notification`。
  - tenant token ✅；权限 `calendar:calendar` 或 `calendar:calendar.event:update`。

## 架构（沿用现有分层，全落 haitun-workspace，src/ 零改动）

### A. `tools/_feishu_impl.py` 新增日历 impl（手搭 BaseRequest，token_types {TENANT,USER}）

- `_primary_calendar` 模块级缓存 + `_get_primary_calendar_id()`：
  POST /calendar/v4/calendars/primary，取第一个 `calendar.calendar_id`，缓存（机器人主日历不变）。失败返回 None。
- `_time_to_info(t: str, tz)`：把 `'YYYY-MM-DD HH:MM'`→`{timestamp:"<秒>", timezone}`；`'YYYY-MM-DD'`→全天 `{date, timezone}`；空/非法→None。
- `create_event_impl(summary, start, end, description, attendees, timezone)`：
  1. 取主日历 id（无则报错提示"应用未开机器人能力/无日历权限"）。
  2. 解析 start/end（必填，缺失/非法→ok=false）。
  3. POST events 建日程，拿 `event_id`。
  4. 若 attendees 非空：拆逗号 open_id，POST attendees 加人（失败不推翻已建日程，但在返回里带 `attendee_warning`）。
  5. 返回 `{event_id, calendar_id, summary, start, end, attendees_added, url?}`。

### B. 工具薄壳 `tools/feishu_calendar.py`

- `feishu_calendar_create_event(summary, start, end, description="", attendees="", timezone="Asia/Shanghai") -> str`
  - `start`/`end`：`'YYYY-MM-DD HH:MM'`（定时）或 `'YYYY-MM-DD'`（全天，两者都用 date）。
  - `attendees`：逗号分隔 open_id（配合 `feishu_chat_find_member` 拿 open_id）。

（先做"创建"这一个工具，覆盖你的需求；查询/改/删日程本次不做。）

## 测试（`tests/test_feishu.py` 追加，mock `_invoke`，不打真实 API）

- `_time_to_info`：定时→timestamp+timezone；全天→date；非法→None。
- `create_event_impl`：mock `_get_primary_calendar_id`(返回固定 id) + mock `_invoke`（多次调用：create→attendees）。断言 POST events 的 uri/body（summary、start_time/end_time 结构）；带 attendees 时断言 attendees 接口 body（type=user、user_id、逗号 split）；返回 event_id。
- 无 start/end → ok=false；无主日历 id → ok=false。
- 工具壳 async + docstring。
- ruff + ruff format + ty + pytest 全绿。

## 依赖与打包

- **零新增依赖**。不改 pyproject/nuitka/pyinstaller。

## 用户侧前置（飞书后台）

- 应用**开启机器人能力**（否则查主日历/建日程 190007）。
- 开 `calendar:calendar`（或 create/read 细分 scope）并发布。
- 说明：日程建在**机器人主日历**、机器人是组织者；参与人会收到 Bot 通知（need_notification 默认 true）。

## 不做（YAGNI）

- 不做查询/更新/删除日程、会议室预订、日程列表、忙闲查询。
- 不做在"某个用户日历"上代建（那需该用户 OAuth；本工具用机器人主日历）。
- 不做重复日程(repeat)、提醒(reminders)自定义。

## 落地顺序

1. `_feishu_impl.py` 加 primary/time/create_event impl + 测试（红→绿）。
2. `tools/feishu_calendar.py` 工具壳。
3. ruff+ty+pytest 全绿 → 提交推送 → 更新 PR #350 → 重启 gateway。
4. 交付：用户确认机器人能力+calendar 权限 → 端到端联调（建日程、加参与人）。
