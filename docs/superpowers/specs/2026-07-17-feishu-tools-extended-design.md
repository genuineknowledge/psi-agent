# 飞书扩展工具集设计规格（tasks/日历/考勤/多维表格/搜索/审批）

**日期**: 2026-07-17
**状态**: 已实现
**分支**: `add-feishu-tools1`（PR #350「飞书工具」）
**对应计划**: [plans/2026-07-17-feishu-tools-extended.md](../plans/2026-07-17-feishu-tools-extended.md)

---

## 1. 概述

在已有 Feishu channel（[specs/2026-06-25-feishu-channel.md](2026-06-25-feishu-channel.md)）与
基础文档/评论工具（[specs/2026-07-10-feishu-tools-design.md](2026-07-10-feishu-tools-design.md)）
之上，扩展一组飞书（国内版 feishu.cn）能力：原生任务、日历、考勤查询、多维表格、文档
搜索、审批与单据下载、通讯录成员名单。本规格整合了这些扩展工具的设计（不含 channel
与基础文档工具，它们各有独立规格）。全部落在 `examples/haitun-workspace/` 内，`src/`
零改动、零新增依赖。

---

## 2. 分层架构

| 层 | 文件 | 职责 |
|---|---|---|
| 薄壳 | `tools/feishu_*.py` | 工具函数（名=工具名，无 `_` 前缀，`async`，参数只用 str/int/bool，Google docstring） |
| 实现 | `tools/_feishu_impl.py` | client 懒加载+缓存、`_invoke` 归一化、各域 `*_impl` + `_build_*_request` |
| 测试 | `tests/test_feishu.py` | mock `_invoke`，断言 BaseRequest 组装与响应解析，不打真实 API |

`_feishu_impl.py`（`_` 前缀）**不被扫描为工具、也不热重载** —— 改它必须重启 gateway。

---

## 3. 统一约定

- SDK 契约：`client.arequest(BaseRequest)`；BaseRequest 设
  `.http_method`（GET/POST/PATCH）/`.uri`（`:name` 占位）/`.paths`/`.add_query`（值强转 str）/
  `.body`/`.token_types`。SDK 只自带 cardkit/contact/drive-comment/im/wiki builder，其余手搭。
- 返回：成功 `{"ok": true, ...}`；飞书 `code!=0` 原样带回 `code`+`msg`+`message`；
  鉴权缺失 `ok=false` 不抛异常。`dumps_result` 用 `ensure_ascii=False`。
- 鉴权：多数工具 tenant_access_token；仅文档搜索用 user_access_token（UAT）。

---

## 4. 鉴权模型（UAT 授权码流）

文档搜索接口只吃 UAT；**国内版飞书无设备流**（v2 端点 404），走授权码流：
`accounts.feishu.cn/open-apis/authen/v1/authorize`（浏览器同意）→ 手动粘贴 `code` →
`open.feishu.cn/open-apis/authen/v1/access_token`（用 app_access_token 换 UAT）→
`/authen/v1/refresh_access_token` 刷新。app_access_token 来自
`/auth/v3/app_access_token/internal`。UAT 明文存 `<workspace>/.psi/feishu/uat.json`
（`.psi/` 已 gitignore）。

---

## 5. 工具清单与端点

### 5.1 任务 Tasks v2（tenant）

| 工具 | 端点 / 要点 |
|---|---|
| `feishu_task_create(summary, description, due, assignees, followers)` | `POST /task/v2/tasks`；members=`{id, type:"user", id_type:"open_id", role}`；due→毫秒 |
| `feishu_task_list(completed, page_size, page_token)` | `GET /task/v2/tasks`，`type=my_tasks`（仅机器人自己的任务） |
| `feishu_task_get(task_guid)` | `GET /task/v2/tasks/:guid` |
| `feishu_task_update(task_guid, summary, description, due)` | `PATCH /task/v2/tasks/:guid`，update_fields 仅列非空（列了不给值=清空） |
| `feishu_task_complete(task_guid, completed)` | PATCH `completed_at`（完成=now ms，取消="0"） |

### 5.2 日历 / 考勤（tenant）

| 工具 | 端点 / 要点 |
|---|---|
| `feishu_calendar_create_event(summary, start, end, description, attendees, timezone)` | 取 `primary` → `POST /calendar/v4/calendars/:id/events`（+`.../attendees`） |
| `feishu_attendance_query(user_ids, date_from, date_to, employee_type, need_overtime)` | `POST /attendance/v1/user_tasks/query`（只读；date `yyyyMMdd`；≤50 人） |

### 5.3 多维表格 Bitable（tenant）

| 工具 | 端点 / 要点 |
|---|---|
| `feishu_bitable_list_tables(app_token)` | `GET /bitable/v1/apps/:app_token/tables` |
| `feishu_bitable_list_records(app_token, table_id, page_size, page_token, filter, sort)` | `GET .../records` |
| `feishu_bitable_create_record(app_token, table_id, fields_json)` | `POST .../records`，`fields_json` 经 json.loads |

前提：应用 `bitable:app` scope + 加为该 base 协作者（可编辑），否则 403（1254302）。

### 5.4 文档搜索（user OAuth）

| 工具 | 端点 / 要点 |
|---|---|
| `feishu_auth_start(scopes)` | 拼 `accounts.feishu.cn/.../authorize` URL，state 存 pending |
| `feishu_auth_complete(code)` | app_access_token 换 UAT，存 FileTokenStore；支持粘整段 URL |
| `feishu_docs_search(search_key, count, offset, docs_types)` | `POST /suite/docs-api/search/object`（`token_types={USER}`） |

### 5.5 审批 / 单据下载 / 通讯录（tenant，报告闭环）

| 工具 | 端点 / 要点 |
|---|---|
| `feishu_approval_list_tasks(user_id, topic, ...)` | `GET /approval/v4/tasks/query` |
| `feishu_approval_list_instances(approval_code, start_time, end_time)` | `GET /approval/v4/instances`（翻页收 instance_code_list） |
| `feishu_approval_get(instance_id)` | `GET /approval/v4/instances/:instance_id` + 解析 `attachments` |
| `feishu_approval_decide(approve, approval_code, instance_code, approver_user_id, task_id, ...)` | `POST /approval/v4/tasks/{approve\|reject}` |
| `feishu_file_download(source, save_path, is_url)` | is_url=True 直下链接；否则 `GET /drive/v1/medias/:file_token/download` |
| `feishu_department_members(department_id, department_id_type, user_id_type, recursive)` | `GET /contact/v3/users/find_by_department`（+ `/departments/:id/children` 递归） |

**审批附件关键设计**：表单附件（attachmentV2/image/imageV2）是 **12h 有效直链 URL**
（`kind=url`，用 is_url=True 直下），非 drive token；只有 document 控件回 drive token
（`kind=drive`，is_url=False）。`feishu_approval_get` 从 `form` JSON 解析出
`attachments:[{name, type, kind, value}]`，读详情后应立即下载。

---

## 6. 定时任务（本地专属）

`schedules/daily-todo-topic` + `schedules/todo-check`：靠工具自身发飞书 API，绕过
session→channel 无主动推送的底座缺口，不改内核。**两目录未被 git 跟踪、不进部署包，
只在本地跑。**

---

## 7. 技能（编排型，规则不写死）

| 技能 | 作用 |
|---|---|
| `feishu-mentor-feedback` | mentor 反馈写入/汇总 bitable |
| `feishu-attendance-payroll` | 名单→考勤→按用户当次给的公式算劳务费出表 |
| `feishu-reimbursement-archive` | 审批实例→下载单据到每笔一个文件夹→按用户当次给的清单校验→汇总表 |

---

## 8. 非目标（YAGNI）

不做代打卡；不做任务 members/reminders/tasklist 增改；不做评论删除/解决；不做 bitable
记录删改/字段管理；不做多用户 UAT；不做 session 主动推送 / channel 轮询；不在 API 层改
飞书审批流定义（“设条件”靠 agent 作为审批人校验）。
