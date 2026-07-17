# 飞书工具集设计规格（haitun workspace）

**日期**: 2026-07-17
**状态**: 已实现
**分支**: `add-feishu-tools1`（PR #350「飞书工具」）
**对应计划**: [plans/2026-07-17-feishu-tools-master.md](../plans/2026-07-17-feishu-tools-master.md)

---

## 1. 概述

在 haitun workspace 为 agent 提供一整套飞书（国内版 feishu.cn）能力：文档读取与
云文档评论、消息与原生话题、原生任务、日历、考勤查询、多维表格、文档搜索、审批与
单据下载、通讯录成员名单。本规格是这些工具的单一设计参照，合并自 10 份历史分计划。

除最早的 **Feishu channel**（改 `src/`，另见
[specs/2026-06-25-feishu-channel.md](2026-06-25-feishu-channel.md)）外，全部能力落在
`examples/haitun-workspace/` 内，`src/` 零改动、零新增依赖。

---

## 2. 分层架构

| 层 | 文件 | 职责 |
|---|---|---|
| 薄壳 | `tools/feishu_*.py` | 工具函数（名=工具名，无 `_` 前缀，`async`，参数只用 str/int/bool，Google docstring），转调 impl |
| 实现 | `tools/_feishu_impl.py` | client 懒加载+缓存、`_invoke` 归一化、各域 `*_impl` + `_build_*_request` |
| 测试 | `tests/test_feishu.py` | mock `_invoke`，断言 BaseRequest 组装与响应解析，不打真实 API |

`_` 前缀模块（`_feishu_impl.py`）**不被 ToolRegistry 扫描为工具，也不热重载** —— 改它
必须重启 gateway。

---

## 3. SDK 调用契约（lark-channel-sdk）

- `Client.builder().app_id(id).app_secret(secret).build()` → `Client`
- `await client.arequest(req: BaseRequest) -> BaseResponse`
- `BaseRequest`：`.http_method`（`HttpMethod.GET/POST/PATCH`）、`.uri`（`:name` 占位）、
  `.paths[name]=val`、`.add_query(k, v)`（值强转 str）、`.body=dict`、
  `.token_types={AccessTokenType.TENANT, AccessTokenType.USER}`
- `BaseResponse`：`.code`（0=成功）、`.msg`、`.raw.content`（bytes JSON body）
- 现成 builder 仅 cardkit/contact/drive-comment/im/wiki；其余全手搭 BaseRequest
- 枚举/模型：`from lark_channel.core.enum import HttpMethod, AccessTokenType`、
  `from lark_channel.core.model import BaseRequest`

---

## 4. 统一返回与错误处理

- 成功：`{"ok": true, "code": 0, "msg": "", "data": {...}}`（各 impl 再精简成业务结构）
- 飞书业务错误：`code != 0` → `{"ok": false, "code", "msg", "message": "Feishu API error <code>: <msg>"}`
- 鉴权缺失：`PSI_FEISHU_APP_ID/SECRET` 未配 → `ok=false` + 明确提示，**不抛异常、不影响其它工具加载**
- 序列化：`dumps_result` 用 `json.dumps(..., ensure_ascii=False)`

---

## 5. 鉴权模型

| 方式 | 用途 | 工具 |
|---|---|---|
| tenant_access_token（机器人） | 绝大多数工具 | 除文档搜索外全部 |
| user_access_token（UAT，用户 OAuth） | 文档搜索（搜授权用户可见范围） | `feishu_docs_search` + `feishu_auth_*` |

国内版飞书**无设备流**（v2 端点返 404），UAT 走**授权码流**：
`accounts.feishu.cn/open-apis/authen/v1/authorize`（浏览器同意）→ 手动粘贴 `code` →
`open.feishu.cn/open-apis/authen/v1/access_token`（app_access_token 换 UAT）→
`refresh_access_token` 刷新。UAT 明文存 `<workspace>/.psi/feishu/uat.json`（`.psi/` 已 gitignore）。

---

## 6. 工具清单与端点

### 6.1 文档 / 评论 / wiki（tenant）

| 工具 | 端点 |
|---|---|
| `feishu_doc_read(file_type, token, max_chars)` | docx→`GET /docx/v1/documents/{token}/raw_content`；doc→`GET /doc/v2/{token}/raw_content`；sheet→sheets v3/v2 拼文本 |
| `feishu_drive_add_comment(file_token, file_type, content)` | `POST /drive/v1/files/.../comments`（SDK builder） |
| `feishu_drive_list_comments(...)` | `GET /drive/v1/files/.../comments`，`is_whole=true` |
| `feishu_drive_list_comment_replies(...)` | SDK builder |
| `feishu_drive_reply_comment(..., at_user_id="")` | `POST /drive/v1/files/:file_token/comments/:comment_id/replies`（rich-text，可加 person mention） |
| `feishu_wiki_get_node(token)` | wiki node → obj_token/obj_type |

### 6.2 消息 / 话题（tenant）

| 工具 | 端点 |
|---|---|
| `feishu_chat_find(name, exact)` | `chat.asearch` + `chat.alist` 兜底 |
| `feishu_chat_find_member(chat_id, name, ...)` | `GET /im/v1/chats/:chat_id/members` 翻页匹配名字 |
| `feishu_message_send(receive_id, content, msg_type, receive_id_type)` | `POST /im/v1/messages` |
| `feishu_message_reply(message_id, content, ..., reply_in_thread=True)` | `POST /im/v1/messages/:id/reply` |
| `feishu_message_list(container_id, container_id_type, ...)` | `GET /im/v1/messages` |
| `feishu_topic_start(...)` | 发话题根消息（post 富文本 @；机器人不能用 text 消息 @） |
| `feishu_thread_read(thread_id, ...)` | `container_id_type=thread` 读话题全部回复 |

### 6.3 任务 Tasks v2（tenant）

| 工具 | 端点 |
|---|---|
| `feishu_task_create(summary, description, due, assignees, followers)` | `POST /task/v2/tasks`；members=`{id, type:"user", id_type:"open_id", role}` |
| `feishu_task_list(completed, ...)` | `GET /task/v2/tasks`，`type=my_tasks`（只列机器人自己的） |
| `feishu_task_get(task_guid)` | `GET /task/v2/tasks/:guid` |
| `feishu_task_update(task_guid, ...)` | `PATCH /task/v2/tasks/:guid`，update_fields 仅列非空字段（避免误清空） |
| `feishu_task_complete(task_guid, completed)` | PATCH `completed_at`（完成=now ms，取消="0"） |

### 6.4 日历 / 考勤 / 多维表格（tenant）

| 工具 | 端点 |
|---|---|
| `feishu_calendar_create_event(summary, start, end, description, attendees, timezone)` | 先取 `primary` calendar_id → `POST /calendar/v4/calendars/:id/events`（+attendees） |
| `feishu_attendance_query(user_ids, date_from, date_to, employee_type, need_overtime)` | `POST /attendance/v1/user_tasks/query`（只读打卡结果） |
| `feishu_bitable_list_tables(app_token)` | `GET /bitable/v1/apps/:app_token/tables` |
| `feishu_bitable_list_records(app_token, table_id, ...)` | `GET .../records`（filter/sort/page 透传） |
| `feishu_bitable_create_record(app_token, table_id, fields_json)` | `POST .../records`，`fields_json` 经 json.loads |

### 6.5 文档搜索（user OAuth）

| 工具 | 端点 |
|---|---|
| `feishu_auth_start(scopes)` | 拼 `accounts.feishu.cn/.../authorize` URL，state 存 pending |
| `feishu_auth_complete(code)` | app_access_token 换 UAT，存 FileTokenStore |
| `feishu_docs_search(search_key, count, offset, docs_types)` | `POST /suite/docs-api/search/object`（`token_types={USER}`） |

### 6.6 审批 / 单据下载 / 通讯录（tenant，报告闭环）

| 工具 | 端点 |
|---|---|
| `feishu_approval_list_tasks(user_id, topic, ...)` | `GET /approval/v4/tasks/query` |
| `feishu_approval_list_instances(approval_code, start_time, end_time)` | `GET /approval/v4/instances`（翻页收 instance_code_list） |
| `feishu_approval_get(instance_id)` | `GET /approval/v4/instances/:instance_id` + 解析 `attachments` |
| `feishu_approval_decide(approve, approval_code, instance_code, approver_user_id, task_id, ...)` | `POST /approval/v4/tasks/{approve\|reject}`（记在真实审批人名下） |
| `feishu_file_download(source, save_path, is_url)` | is_url=True 直下链接；否则 `GET /drive/v1/medias/:file_token/download` |
| `feishu_department_members(department_id, department_id_type, user_id_type, recursive)` | `GET /contact/v3/users/find_by_department`（+ `/departments/:id/children` 递归） |

**审批附件关键设计**：审批表单附件（attachmentV2/image/imageV2）是**12h 有效的直链
URL**（`kind=url`，用 is_url=True 直下），非 drive token；只有 document 控件回 drive
token（`kind=drive`，is_url=False）。`feishu_approval_get` 从 `form` JSON 解析出
`attachments:[{name, type, kind, value}]`，读详情后应立即下载。

---

## 7. 定时任务（本地专属）

`schedules/daily-todo-topic`（每日发当日 todo 话题）+ `schedules/todo-check`
（12:00 后读回复逐条评价、@ 指定人）。定时任务靠工具自身发飞书 API，绕过 session→
channel 无主动推送的底座缺口，不改内核。**两目录未被 git 跟踪、不进部署包，只在本地跑。**

---

## 8. 技能（编排型，规则不写死）

| 技能 | 作用 |
|---|---|
| `feishu-mentor-feedback` | mentor 反馈写入/汇总 bitable |
| `feishu-attendance-payroll` | 名单→考勤→按用户当次给的公式算劳务费出表 |
| `feishu-reimbursement-archive` | 审批实例→下载单据到每笔一个文件夹→按用户当次给的清单校验→汇总表 |

技能不内置金额/校验规则，规则每次由用户提供。

---

## 9. 非目标（YAGNI）

不做代打卡；不做任务 members/reminders/tasklist 增改；不做评论删除/解决；不做 bitable
记录删改/字段管理；不做多用户 UAT；不做 session 主动推送 / channel 轮询；不在 API 层
改飞书审批流定义（“设条件”靠 agent 作为审批人校验）。
