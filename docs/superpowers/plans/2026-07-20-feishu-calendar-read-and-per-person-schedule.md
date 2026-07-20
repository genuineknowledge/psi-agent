# 飞书日程：读取 + 为每个人设立日程

## 目标
给 haitun agent 的飞书日历能力补两块（分支 `add-feishu-tools3-work`，worktree `C:\Users\12815\psi-agent-feishu-tools3`）：
1. **读取日程** —— 列出某日历在一个时间段内的事件详情（标题/起止/状态/参与人等）。
2. **为每个人设立日程** —— 两种：
   - **逐人分别建独立日程**：给一批人，每人各建一个只邀请本人的事件；
   - **建一个会邀请所有人**：现有 `feishu_calendar_create_event` 已覆盖，复核并在 AGENTS.md 讲清边界，不重复造。

## 现状（已核实）
- [examples/haitun-workspace/tools/feishu_calendar.py](examples/haitun-workspace/tools/feishu_calendar.py)：仅 `feishu_calendar_create_event`（机器人主日历建事件 + 按 open_id 邀请）。
- [_feishu_impl.py](examples/haitun-workspace/tools/_feishu_impl.py) 已有基建：`_invoke` / `_error` / `dumps_result`、`_get_primary_calendar_id`（缓存主日历）、`_time_to_info`（时间解析）、`_fmt_ms`、`create_event_impl`、`_build_create_event_request` / `_build_add_attendees_request`。
- SDK `lark_channel` **无** calendar builder（现有 create_event 也是手搭 `BaseRequest`）→ 新增照手搭范式，**零新依赖**，不动 pyproject/nuitka/pyinstaller。
- 解析人名→open_id 已有：`feishu_chat_find_member`、`feishu_department_members`。

## 飞书 API（已核实）
- **列事件**：`GET /open-apis/calendar/v4/calendars/:calendar_id/events`，query `start_time`/`end_time`（Unix 秒，成对）、`page_size`(50-1000)、`page_token`、`user_id_type`。scope `calendar:calendar` 或 `calendar:calendar.event:read`；身份须对该日历有读权限。
- **建事件 / 加参与人**：沿用现有两个 builder。

## 实现

### 1. `_feishu_impl.py`（新增，紧接 calendar 段）
- `_build_list_events_request(calendar_id, start_ts, end_ts, page_size, page_token) -> BaseRequest`：GET 上述 URI，`token_types={TENANT, USER}`，成对写 `start_time`/`end_time`，带 `user_id_type=open_id`。
- `_ts_of(t, timezone) -> str | None`：把 `'YYYY-MM-DD HH:MM'` / `'YYYY-MM-DD'` 转 Unix 秒字符串（复用 `_time_to_info` 逻辑，all-day 当天 00:00）。
- `async list_events_impl(start, end, calendar_id="", timezone="Asia/Shanghai", max_events=50) -> dict`：
  - `calendar_id` 空则用 `_get_primary_calendar_id()`；
  - 解析 start/end，非法则 `_error`；
  - 翻页累计到 `max_events`，每条归一化为 `{event_id, summary, description, start, end, status, is_all_day, organizer, attendee_count}`（时间用 `_fmt_ms` 或 date 原样）；
  - 返回 `{ok, calendar_id, count, events:[...]}`。
- `async create_events_per_person_impl(summary, start, end, attendees, description="", timezone="Asia/Shanghai", per_person_summary=True) -> dict`：
  - 拆 `attendees`（逗号分隔 open_id）；空则 `_error`；
  - 对每个 open_id 复用 `create_event_impl`（标题可加 `（<open_id 末4位/或原样>）` 区分，默认在 summary 后不改，仅各自只邀请本人）——即循环调 `_build_create_event_request` + `_build_add_attendees_request`，逐人独立事件、每个只邀请该人；
  - 逐人收集结果 `{open_id, ok, event_id, error?}`，返回 `{ok(全成功才true), created:[...], failed:[...]}`（部分失败不 crash，明确列出）。

### 2. `feishu_calendar.py`（新增两个薄壳工具）
- `feishu_calendar_list_events(start, end, calendar_id="", timezone="Asia/Shanghai", max_events=50) -> str`
- `feishu_calendar_create_per_person(summary, start, end, attendees, description="", timezone="Asia/Shanghai") -> str`
  - docstring 讲清：逐人各建独立事件、每人只邀请自己；要先用 `feishu_chat_find_member`/`feishu_department_members` 拿 open_id。
- 更新模块 docstring 说明三种能力与权限/scope。

### 3. 测试 [tests/test_feishu.py](examples/haitun-workspace/tests/test_feishu.py)（calendar 段追加）
- `test_list_events_builds_request`：断言 GET + URI + start/end query（Unix 秒）+ calendar_id。
- `test_list_events_uses_primary_when_blank`：calendar_id 空时走 `_get_primary_calendar_id`。
- `test_list_events_bad_time`：非法时间 `ok=false`。
- `test_list_events_normalizes`：mock 返回事件列表，断言归一化字段。
- `test_create_per_person_one_event_each`：3 人 → 每人 create+add-attendee，各自只含本人 open_id。
- `test_create_per_person_partial_failure`：某人建失败进 `failed`，整体 `ok=false` 但其他人成功。
- `test_calendar_tools_async_with_docstrings`：新工具是 async + 有 docstring。
- 复用已有 `_CapturedInvoke` / `_PagedInvoke` / `_SequencedInvoke`。

### 4. [AGENTS.md](examples/haitun-workspace/AGENTS.md)
- 在 feishu 工具表补 `feishu_calendar` 行：列出三个工具（create_event / list_events / create_per_person）+ 权限 + scope `calendar:calendar` / `calendar:calendar.event:read`，并点明"读别人日历需该日历读权限"的限制。

## 约束与验证
- 参数只用 str/int/bool（ToolRegistry 限制）；鉴权缺失/飞书 code!=0/import 失败一律 `ok=false` 不 crash。
- 零新依赖 → 不动 `pyproject.toml` / nuitka / pyinstaller。
- 提交前跑齐 CI 三项：`uv run ruff check .`、`uv run ruff format --check .`、`uv run ty check .`（确认 feishu 文件零诊断，忽略 pre-existing 无关报错）。
- 测试：`uv run pytest examples/haitun-workspace/tests/test_feishu.py -p no:cov -o addopts="" -q`（async 用例带 `@pytest.mark.asyncio`）。
- 注意本仓 py3.14 + ruff format 强制 `except A, B:` 无括号，别误判语法错。
- 完成即 commit（结尾带 Co-Authored-By）+ push 到 `add-feishu-tools3-work`，**不合 main**。

## 交付物
- `tools/_feishu_impl.py`（+2 builder/impl，+1 时间辅助）
- `tools/feishu_calendar.py`（+2 工具，更新 docstring）
- `tests/test_feishu.py`（+7 测试）
- `AGENTS.md`（feishu_calendar 行）
- `docs/superpowers/plans/2026-07-20-feishu-calendar-read-and-per-person-schedule.md`（本文件）
