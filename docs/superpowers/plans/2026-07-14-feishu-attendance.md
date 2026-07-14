# 飞书考勤打卡工具（只读查询/统计）—— 实现计划

日期：2026-07-14
分支：`add-feishu-tools1`
状态：待批准

## 目标

给 agent 加读取飞书官方**考勤打卡记录**的能力：查某些人在某时间段的打卡结果（谁、哪天、上/下班打卡时间、地点、是否缺卡/迟到），让 agent 能回答"今天谁没打卡""本周迟到情况"并汇总。

## 关键决策：只读，不做代打卡（建议默认，待你确认）

- **只做读取/统计**，不实现"代员工打卡"（写入打卡流水）。原因：代打卡有明确合规风险（伪造考勤），且写入接口门槛更高。
- 若你确实要"代打卡"写入，请在审阅时说明——那是另一套接口(`user_flows/batch_create`)和另一套风险评估，本计划不含。

## 已查证的接口与约束

- **SDK 无考勤 builder**（只有 cardkit/contact/drive/im/wiki），全部**手搭 BaseRequest**（沿用现有 `_feishu_impl.py` 模式）。
- **查打卡结果**：`POST /open-apis/attendance/v1/user_tasks/query`
  - query：`employee_type`（必填，如 `employee_id`）、`ignore_invalid_users`、`include_terminated_user`
  - body：`user_ids`（string[]，≤50，必填）、`check_date_from`/`check_date_to`（int `yyyyMMdd`，必填）、`need_overtime_result`（可选）
  - 返回 `user_task_results[]`：每条含 `user_id`、`employee_name`、`day`、`check_in_record`/`check_out_record`（各含 `check_time` 秒级时间戳、`location_name`、`type`、`check_result`…）、`check_in_result`/`check_out_result`（Normal/Early/Late/Lack 等）。
- **鉴权（关键，已确认）**：**tenant_access_token（bot）可读**，权限 scope `attendance:task:readonly`，**仅「自建应用」支持**。
  - 还需在**考勤管理后台**给该应用**数据权限范围**（否则 1220004/1220005 权限错误）。
- 可选的更细"打卡流水"(`user_flows/query`)本次不做，`user_tasks/query` 已够覆盖"谁几点打卡+结果"。

## 架构（沿用现有分层，全落 haitun-workspace，src/ 零改动）

### A. `tools/_feishu_impl.py` 新增考勤 impl（手搭 BaseRequest，token_types {TENANT,USER}）

- `_build_user_tasks_query_request(user_ids, check_date_from, check_date_to, employee_type, need_overtime)`：
  POST `/attendance/v1/user_tasks/query`，query 带 `employee_type`+`ignore_invalid_users=true`，body 带 user_ids/日期。
- `query_attendance_impl(user_ids: list[str], date_from: str, date_to: str, employee_type: str, need_overtime: bool) -> dict`：
  调 `_invoke`，把 `user_task_results` 精简成便于 agent 用的结构：
  `[{user_id, name, day, check_in_time, check_in_result, check_in_location, check_out_time, check_out_result, check_out_location}]`（时间戳转可读时间）。
  透传 `invalid_user_ids`/`unauthorized_user_ids` 让 agent 知道哪些人没查到/没授权。

### B. 工具薄壳 `tools/feishu_attendance.py`

工具参数只用 str/int/bool（ToolRegistry 限制），`user_ids` 用逗号分隔字符串传入，impl 层 split：

- `feishu_attendance_query(user_ids: str, date_from: str, date_to: str, employee_type: str = "employee_id", need_overtime: bool = False) -> str`
  - `user_ids`：逗号分隔（≤50）；`date_from`/`date_to`：`yyyyMMdd`。
  - 返回精简后的每人每日打卡结果 JSON。

（先只做这一个查询工具，够回答"谁打了/没打/迟到"。汇总/播报由 agent 用已有 message 工具做，不额外写工具。）

## 测试（`tests/test_feishu.py` 追加，mock `_invoke`，不打真实 API）

- `query_attendance_impl`：断言 POST `/attendance/v1/user_tasks/query`、query 带 employee_type、body 的 user_ids（逗号串被 split 成数组）+ check_date_from/to；解析 user_task_results→精简结构；时间戳转换。
- 空/多个 user_ids、缺勤（check_out_record 缺失）场景解析不崩。
- 工具壳 async + docstring 校验。
- ruff check + ruff format --check + ty check + pytest 全绿。

## 依赖与打包

- **零新增依赖**（手搭 BaseRequest 走已有 client）。不改 pyproject/nuitka/pyinstaller。

## 用户侧前置（飞书后台）

- 应用类型必须是**自建应用**。
- 开通 `attendance:task:readonly` 权限并发布。
- 在**考勤管理后台**把该应用加入**数据权限范围**（能看到哪些人的考勤）。
- 需要 `user_id`（employee_id）列表——可配合已有 `feishu_chat_find_member`（返回 open_id，注意 employee_type 要匹配；若用 open_id 则 employee_type 传 `open_id`，employee_type 支持 open_id/union_id/employee_id/employee_no，实现时按飞书文档确认取值）。

## 不做（YAGNI）

- 不做代打卡/补卡写入（合规风险）。
- 不做班次/考勤组管理、审批、统计报表导出。
- 不做 user_flows 细粒度流水（user_tasks 已够）。

## 落地顺序

1. `_feishu_impl.py` 加 query impl + 测试（红→绿）。
2. `tools/feishu_attendance.py` 工具壳。
3. ruff+ty+pytest 全绿 → 提交（**按你要求先不 push**，等你发话）。
4. 交付：用户配 attendance:task:readonly + 考勤后台数据权限 → 端到端联调。
