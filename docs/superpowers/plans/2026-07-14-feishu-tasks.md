# 飞书任务(Tasks v2)工具 —— 分发与管理 —— 实现计划

日期：2026-07-14
分支：`add-feishu-tools1`
状态：待批准

## 目标

给 agent 加飞书**原生任务**的分发与管理能力：创建任务并分配负责人/截止时间、列出任务、标记完成/更新。让 agent 能"派活给人 + 跟踪完成"，用飞书任务中心做载体（比多维表格更专业：有负责人、DDL、完成态、提醒，团队在任务中心直接看/勾）。

用户确认：**建 / 分配 / 列 / 完成 全套**。

## 已查证的接口与约束（含实测）

- **SDK 无 task builder**（只有 cardkit/contact/drive/im/wiki），全部**手搭 BaseRequest**。
- **创建**：`POST /open-apis/task/v2/tasks`
  - body：`summary`(标题)、`description`、`due`({timestamp ms 字符串, is_all_day})、`members`([{id, type:"open_id", role:"assignee"|"follower"}]，≤50)、`tasklists`(可选)。
  - **tenant token ✅**（源码标记 6=tenant 创建）；限制：**不能加其他租户成员**。
- **列任务**：`GET /open-apis/task/v2/tasks`
  - query：`page_size`(1-100)、`page_token`、`completed`(bool 可选)、`type`(仅 `my_tasks`)、`user_id_type`。
  - **实测 tenant token 返回 code=0 可用**。**关键语义**：列的是"**调用身份(机器人)负责的**任务"(`my_tasks`)——即机器人作为 assignee 的任务，**不能列出"某个具体人的"任务**（那需要那个人的 user_access_token，本工具不做，会在说明里讲清）。
  - 返回 items：`guid`、`summary`、`status`(todo/done)、`completed_at`、`due`、`members`、`url`…
- **完成 / 更新**：`PATCH /open-apis/task/v2/tasks/:task_guid`
  - 完成：body `{task:{completed_at:"<ms>"}, update_fields:["completed_at"]}`；恢复未完成设 `"0"`。
  - 更新：`{task:{summary/description/due/...}, update_fields:[改了的字段名]}`（**update_fields 里列了但 task 里没给值=清空该字段**，要小心）。
  - **tenant token ✅**，需对该任务有编辑权限。
  - 注意：members/reminders/tasklists **不能**用这个 PATCH 改（各有专用 API，本次不做）。
- 权限 scope：读 `task:task:read`，写 `task:task:write`（或 `task:task:writeonly`）。tenant/user 均可。

## 架构（沿用现有分层，全落 haitun-workspace，src/ 零改动）

### A. `tools/_feishu_impl.py` 新增 task impl（手搭 BaseRequest，token_types {TENANT,USER}）

- `create_task_impl(summary, description, due_ms, assignee_open_ids, follower_open_ids)`：
  - 拼 members（assignee/follower 两类，type=open_id），due 给了就带 `{timestamp:str, is_all_day:false}`。
  - POST /task/v2/tasks，返回 `{task_guid, summary, url}`。
- `list_tasks_impl(completed, page_size, page_token)`：
  - GET /task/v2/tasks，query 带 `type=my_tasks`、`user_id_type=open_id`、可选 completed。
  - 返回精简 `[{guid, summary, status, due, url, members}]` + has_more + page_token。
- `update_task_impl(task_guid, summary, description, due_ms)`：只带传入的字段进 task + update_fields。
- `complete_task_impl(task_guid, completed)`：completed=True→`completed_at=now_ms`；False→`"0"`；update_fields=["completed_at"]。

时间：due 用毫秒时间戳字符串。工具层收人类友好输入（见下）。

### B. 工具薄壳 `tools/feishu_task.py`（参数只用 str/int/bool，列表用逗号串）

- `feishu_task_create(summary, description="", due="", assignees="", followers="") -> str`
  - `due`：`yyyy-MM-dd HH:mm` 或 `yyyy-MM-dd`（impl 转 ms 时间戳；空=无 DDL）。
  - `assignees`/`followers`：逗号分隔的 open_id（配合 `feishu_chat_find_member` 拿 open_id）。
- `feishu_task_list(completed="", page_size=50, page_token="") -> str`
  - `completed`：""(全部)/"true"/"false"。工具说明写明：**列的是机器人自己负责的任务**。
- `feishu_task_update(task_guid, summary="", description="", due="") -> str`（只改传入的非空字段）
- `feishu_task_complete(task_guid, completed=True) -> str`（勾完成/取消完成）

### C. 与已有工具的配合（不新增，说明里点到）

- 派活给人：先 `feishu_chat_find_member` 把人名→open_id，再 `feishu_task_create(..., assignees="ou_xxx")`。
- 通知：建完任务可 `feishu_message_send`/`feishu_topic_start` @ 负责人告知（任务本身也会在飞书通知）。

## 测试（`tests/test_feishu.py` 追加，mock `_invoke`，不打真实 API）

- `create_task_impl`：断言 POST /task/v2/tasks、members 组装(assignee/follower、逗号串 split、type=open_id)、due 转 ms、返回 task_guid。
- `list_tasks_impl`：断言 GET、query(type=my_tasks、completed 透传)、解析 items→精简结构。
- `complete_task_impl`：断言 PATCH /task/v2/tasks/:task_guid、body {task:{completed_at}, update_fields:["completed_at"]}、completed=False 时 "0"。
- `update_task_impl`：只把非空字段进 update_fields（不误清空）。
- due 解析：合法/空/非法格式。
- 工具壳 async + docstring。
- ruff + ruff format + ty + pytest 全绿。

## 依赖与打包

- **零新增依赖**。不改 pyproject/nuitka/pyinstaller。

## 用户侧前置（飞书后台）

- 开 `task:task:write`（含读写；只读场景 `task:task:read`）并发布。
- tenant token 创建的任务归属机器人；给别人派任务=把对方 open_id 放 members(assignee)。同租户内 OK。

## 不做（YAGNI）

- 不做 tasklist(清单)/section 管理、子任务、评论、附件、自定义字段、依赖、提醒的增改。
- 不做"列某个具体人的任务"（需该用户 OAuth；my_tasks 只列调用身份自己的）。
- 不做 members/reminders 的后续增删（各有专用 API，超出"建/分配/列/完成"范围）。

## 落地顺序

1. `_feishu_impl.py` 加 4 个 task impl + 测试（红→绿）。
2. `tools/feishu_task.py` 4 个工具壳。
3. ruff+ty+pytest 全绿 → 提交推送 → 更新 PR #350。
4. 交付：用户配 task:task:write → 端到端联调（建任务派给人、列、勾完成）。
