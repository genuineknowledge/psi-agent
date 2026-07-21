# 飞书扩展工具集（tasks/日历/考勤/多维表格/文档搜索/审批报告）—— 整合实现计划

日期：2026-07-17
分支：`add-feishu-tools1`（PR #350「飞书工具」）
状态：已实现并推送

> 本文档整合了 6-25(channel)、7-10(基础文档/评论工具) **之外**的 7 份飞书分计划：
> 任务(tasks)、日历(calendar)、考勤(attendance)、多维表格+mentor 反馈(bitable)、
> 文档搜索(doc-search 及其授权码流修复)、审批报告闭环(approval-report)。
> 对应设计规格见 [specs/2026-07-17-feishu-tools-extended-design.md](../specs/2026-07-17-feishu-tools-extended-design.md)。
> channel 与基础文档工具见各自独立计划（2026-06-25、2026-07-10），本文不含。

## 一、总目标

在已有 channel + 基础文档/评论工具之上，给 haitun agent 扩展飞书能力：原生任务分发、
日历日程、考勤查询、多维表格读写、按名字搜文档、审批实例与单据下载、通讯录成员名单。
配合定时任务与技能，跑通「每日 todo 话题评价」「mentor 反馈归档」「月度考勤劳务费」
「报销单据归档校验」等闭环。

## 二、统一架构（沿用 7-10 已建的分层）

- **分层**：`tools/feishu_*.py` 薄壳（函数名=工具名，无 `_` 前缀，`async`，参数只用
  `str/int/bool`，Google docstring，返回 `_f.dumps_result(await _f.xxx_impl(...))`）
  + `tools/_feishu_impl.py` 共享实现层（`_invoke`/`_error`/`dumps_result`/client 缓存已存在）。
- **手搭 BaseRequest**：SDK 只自带 cardkit/contact/drive-comment/im/wiki builder；
  task/calendar/attendance/bitable/approval/contact-v3 全部手搭
  （`.http_method`/`.uri`（`:name` 占位）/`.paths`/`.add_query`/`.body`/`.token_types`）。
- **鉴权**：绝大多数用 **tenant_access_token**（机器人，读 `PSI_FEISHU_APP_ID/SECRET`）；
  唯独**文档搜索**需 **user_access_token（UAT，用户 OAuth）**。
- **零新增依赖 / src 零改动**：全部落在 `examples/haitun-workspace/` 内。
- **测试**：`tests/test_feishu.py`，`_CapturedInvoke`/`_PagedInvoke` mock `_invoke`，
  不打真实 API。门禁：`ruff check` + `ruff format --check` + `ty check` + pytest 全绿。

## 三、各域实现

### 1. 原生任务 Tasks v2（tenant）

- `feishu_task_create(summary, description, due, assignees, followers)` —
  `POST /task/v2/tasks`；members 格式 `{id, type:"user", id_type:"open_id", role:"assignee"|"follower"}`；
  due 收 `yyyy-MM-dd[ HH:mm]`，impl 转毫秒时间戳。
- `feishu_task_list(completed, ...)` — `GET /task/v2/tasks`，`type=my_tasks`
  （**只能列机器人自己负责的任务**，列别人的需该人 UAT，不做）。
- `feishu_task_get` / `feishu_task_update` / `feishu_task_complete` — `PATCH /task/v2/tasks/:guid`；
  update_fields 只列非空字段（**列了但不给值=清空**，须小心）。
- scope：`task:task:write`（含读）。tenant 建的任务归机器人，派活=把对方 open_id 放 members。

### 2. 日历日程（tenant）

- `feishu_calendar_create_event(summary, start, end, description, attendees, timezone)` —
  先取 `primary` 拿 calendar_id → `POST /calendar/v4/calendars/:id/events`；
  attendees 非空再 `POST .../events/:event_id/attendees`。start/end 收
  `YYYY-MM-DD HH:MM` 或整天 `YYYY-MM-DD`。

### 3. 考勤查询（tenant，只读，不做代打卡）

- `feishu_attendance_query(user_ids, date_from, date_to, employee_type, need_overtime)` —
  `POST /attendance/v1/user_tasks/query`；user_ids 逗号串（≤50），date `yyyyMMdd`；
  解析每人每日 check_in/out 时间+结果(Normal/Late/Early/Lack)，透传 invalid/unauthorized。
- scope：`attendance:task:readonly`，且考勤后台要给应用**数据权限范围**。

### 4. 多维表格 Bitable（tenant，通用读写）+ mentor 反馈技能

- `feishu_bitable_list_tables(app_token)` — `GET /bitable/v1/apps/:app_token/tables`
- `feishu_bitable_list_records(app_token, table_id, page_size, page_token, filter, sort)` —
  `GET .../records`
- `feishu_bitable_create_record(app_token, table_id, fields_json)` — `POST .../records`，
  `fields_json` 经 json.loads（工具参数不能是 dict）。
- app_token 从 `feishu.cn/base/<app_token>` 取，或 wiki 链接经 `feishu_wiki_get_node` 解析。
- scope `bitable:app` + **把应用加为该 base 协作者（可编辑）**，否则 403（1254302）。
- 技能 `skills/feishu-mentor-feedback`：把 mentor 反馈写入/汇总到 bitable（编排型，表结构可变）。

### 5. 文档搜索（**user OAuth，唯一非 tenant**）

- 关键坑：搜索接口 `POST /suite/docs-api/search/object` **只吃 UAT**；且**国内版飞书无
  设备流**（v2 端点 404），改用**授权码流 + 手动粘贴 code**。
- `feishu_auth_start(user_key)` — 拼 `accounts.feishu.cn/open-apis/authen/v1/authorize` URL
  （client_id/redirect_uri/response_type=code/scope/state），state 存 pending。
  **scope 固定、不作参数暴露给 LLM**（后续增强，见第七节）。
- `feishu_auth_complete(code, user_key)` — 取 app_access_token（`/auth/v3/app_access_token/internal`）
  → `POST /authen/v1/access_token` 换 UAT → 按 `user_key` 存 FileTokenStore；支持粘整段 URL 抠 code；
  刷新走 `/authen/v1/refresh_access_token`。
- `feishu_docs_search(search_key, count, offset, docs_types, user_key)` — `token_types={USER}`；
  搜到的是**授权用户可见范围**的文档，非全局。`user_key` 对应搜哪个用户的可见范围。
- UAT+refresh_token 明文存 `<workspace>/.psi/feishu/uat.json`（`.psi/` 已 gitignore），
  **按 `user_key`（用户 open_id）分槽**，多人互不覆盖（后续增强，见第七节）。
- 用户侧：注册 redirect_uri（如 `http://localhost/`）+ scope `docs:doc:readonly`/
  `drive:drive:readonly` + `offline_access`，真机走一次授权。

### 6. 审批 + 单据下载 + 通讯录（tenant，报告闭环）+ 两个技能

- `feishu_approval_list_tasks(user_id, topic, ...)` — `GET /approval/v4/tasks/query`（列某人审批任务）
- `feishu_approval_list_instances(approval_code, start_time, end_time)` —
  `GET /approval/v4/instances`（翻页收 instance_code_list，时间为 Unix 毫秒，默认近30天）
- `feishu_approval_get(instance_id)` — `GET /approval/v4/instances/:instance_id` +
  从 `form` JSON 解析 `attachments:[{name, type, kind, value}]`
- `feishu_approval_decide(approve, approval_code, instance_code, approver_user_id, task_id, ...)` —
  `POST /approval/v4/tasks/{approve|reject}`（记在真实审批人名下）
- `feishu_file_download(source, save_path, is_url)` — is_url=True 直下链接；
  否则 `GET /drive/v1/medias/:file_token/download`
- `feishu_department_members(department_id, department_id_type, user_id_type, recursive)` —
  `GET /contact/v3/users/find_by_department`（+ `/departments/:id/children` 递归）
- **审批附件关键坑**：表单附件（attachmentV2/image/imageV2）是**12h 有效直链 URL**
  （kind=url，用 is_url=True 直下），非 drive token；只有 document 控件回 drive token
  （kind=drive）。读详情后须立即下。
- 技能 `skills/feishu-attendance-payroll`（名单→考勤→按用户当次给的公式算劳务费出表）、
  `skills/feishu-reimbursement-archive`（审批实例→下载单据到每笔一个文件夹→按用户当次
  给的清单校验→汇总表）。两技能不内置金额/校验规则。
- scope：`approval:approval:readonly`、`drive:drive:readonly`、
  `contact:contact.base:readonly`（+ `contact:user.employee_id:readonly`，+通讯录范围=全部成员）。

## 四、定时任务（本地专属，不进 git、不部署远端）

`schedules/daily-todo-topic`（每日发当日 todo 话题）+ `schedules/todo-check`
（12:00 后读回复逐条评价、@ 指定人）。靠工具自身发 API，绕过 session→channel 无主动
推送的底座缺口，不改内核。**两目录未被 git 跟踪、不进部署包，只在本地跑。**

## 五、依赖与打包

零新增依赖（手搭 BaseRequest 走已有 client；直链下载复用 httpx）。不改
pyproject / nuitka / pyinstaller。

## 六、非目标（YAGNI，跨域汇总）

不做代打卡；不做任务 members/reminders/tasklist 增改；不做评论删除/解决；不做 bitable
记录删改/字段管理；不做 session 主动推送 / channel 轮询；不在 API 层改
飞书审批流定义（“设条件”靠 agent 作为审批人校验）。

> 注：原“不做多用户 UAT”已在第七节落地（按 `user_key` 隔离）。

## 七、后续增强：多用户 UAT 隔离 + scope 固定 + 建知识库 + 写入类以用户身份调用 + 一步建带内容文档（2026-07-20，已完成）

分支 `feishu-per-user-uat`。设计规格见 spec 第 9 节。场景：公司里每人与 agent 各有对话框，
用全局搜索查知识库 / 审阅交付物，需每人各自授权、各搜自己可见的文档，互不覆盖。

**根因（多人授权互相覆盖）**：UAT 存储 key 写死常量 `"default"`。底层 `FileTokenStore`
本就支持一个 JSON 多 user key，只是没用上。
**根因（授权页报错 20043）**：`feishu_auth_start` 把 `scopes` 暴露给 LLM，模型编造无效
scope（如 `drive:drive:drive:readonly`），飞书拒绝整个授权页。

**Files:**
- Modify: `examples/haitun-workspace/tools/_feishu_impl.py`
- Modify: `examples/haitun-workspace/tools/feishu_auth.py`
- Modify: `examples/haitun-workspace/tools/feishu_docs.py`
- Modify: `examples/haitun-workspace/tools/feishu_wiki.py`
- Modify: `examples/haitun-workspace/tools/feishu_doc.py`
- Modify: `examples/haitun-workspace/tools/feishu_bitable.py`
- Modify: `examples/haitun-workspace/tools/feishu_task.py`
- Modify: `examples/haitun-workspace/tools/feishu_drive.py`
- Modify: `examples/haitun-workspace/tests/test_feishu.py`
- Modify: `examples/haitun-workspace/TOOLS.md`
- Modify: `docs/superpowers/specs/2026-07-17-feishu-tools-extended-design.md`（第 9 节）

- [x] `auth_start_impl` / `auth_complete_impl` / `_get_valid_uat` / `search_docs_impl` 加 `user_key`；
  三个对外工具暴露 `user_key`（用户 open_id，来自 `<feishu_context>.sender_open_id`，同一用户三处一致）
- [x] `_norm_user_key`（空 → `default`，向后兼容）；`_pending_auth_path(user_key)` 按用户分文件 +
  正则清洗非 `[A-Za-z0-9_-]` 防路径穿越
- [x] `feishu_auth_start` 去掉 `scopes` 参数（LLM 碰不到），wrapper 恒传空 → impl 回落固定
  `_DEFAULT_SCOPES`（docs:doc:readonly drive:drive:readonly offline_access）
- [x] `create_wiki_space_impl` + `feishu_wiki_create_space(name, description, open_sharing, user_key)`：
  `POST /wiki/v2/spaces`（**只吃 UAT**），复用按用户隔离；`open_sharing` 仅 open/closed；未授权 need_auth
- [x] 写入类以用户身份调用：`_invoke` 加可选 `user_key`（非空→UAT 分支 `_invoke_as_user`，否则 tenant）；
  抽 `_resp_to_result`；写入类 impl+wrapper 透传 `user_key`（wiki 建文档节点 / docx 建+写正文 /
  bitable 增删记录字段清表 / task 建改完成 / drive 评论回复）。修"机器人非知识库协作者→建文档权限不足"。
  刻意不给日历/消息/考勤/只读类加 UAT
- [x] 一步建带内容文档（修"空节点"）：`create_wiki_doc_with_content_impl` + 工具
  `feishu_wiki_create_doc_with_content`，一次调用内部建节点 + 写正文；正文写入失败仍回报
  `node_token`/`obj_token`（`body_written=False`，不静默留空壳），空正文按成功处理。TOOLS.md 引导优先用它
- [x] 测试：UAT 按 key 隔离不覆盖、pending 分离且防穿越、search+建库+建文档节点 转发 user_key、
  `_norm_user_key` 回落、authorize_url 的 scope 恰为默认值且不含编造 scope、wrapper 无 scopes 参数、
  建库(UAT 请求组装 / 未授权 / 非法 open_sharing)、`_invoke` 空 user_key 走 tenant / 非空走 UAT / 未授权 need_auth；
  fake `_invoke`/`_CapturedInvoke`/`_PagedInvoke` 接受 `user_key`
- [x] `TOOLS.md`：引导 agent 传 `sender_open_id` 作 `user_key`，先问再授权，建文档链路全程同一 `user_key`
- [x] 门禁：`ruff check` + `ruff format --check`（用 CI 版 ruff 0.15）+ pytest（feishu 142 passed）
- [x] Commit `feat(haitun/feishu): 飞书全局搜索 UAT 按用户隔离`（`c1c44e9f`）；scope 修复 `721b9fe0`；
  建库工具 `0a76240f`；写入类以用户身份调用 `afbc9ea8`；一步建带内容文档（本次）

**仍未做（诚实边界）**：OAuth 回调仍手动回传 code；UAT 仍明文存；`auth_complete` 不校验 CSRF state。
