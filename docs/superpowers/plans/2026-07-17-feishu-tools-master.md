# 飞书工具集（haitun workspace）—— 总实现计划

日期：2026-07-17
分支：`add-feishu-tools1`（PR #350「飞书工具」）
状态：已实现并推送（本文档为多份分计划的合并归档）

> 本文档合并了原先分散的 10 份飞书相关计划（channel / tools / tasks / calendar /
> attendance / bitable-mentor / doc-search / doc-search-authcode-fix /
> daily-todo-topic / approval-report-tools），作为飞书能力的单一实现参照。
> 对应设计规格见 [specs/2026-07-17-feishu-tools-master-design.md](../specs/2026-07-17-feishu-tools-master-design.md)。

## 一、总目标

给 haitun agent 一整套飞书（国内版 feishu.cn）能力：读写文档与评论、收发消息与
原生话题、原生任务分发、日历日程、考勤查询、多维表格读写、按名字搜文档、审批实例
与单据下载，以及通讯录成员名单。配合定时任务与技能，跑通「每日 todo 话题评价」
「mentor 反馈归档」「月度考勤劳务费」「报销单据归档校验」等闭环。

## 二、统一架构（贯穿所有工具）

- **分层**：`tools/feishu_*.py` 薄壳（函数名=工具名，无 `_` 前缀，`async`，参数只用
  `str/int/bool`，Google docstring，返回 `_f.dumps_result(await _f.xxx_impl(...))`）
  + `tools/_feishu_impl.py` 共享实现层。
- **SDK**：复用 `lark-channel-sdk`（已在 pyproject）。`Client.builder().app_id().app_secret().build()`
  懒加载 + module 级缓存；`await client.arequest(BaseRequest)` 执行。
- **手搭请求**：SDK 只自带 cardkit/contact/drive/im/wiki builder，其余（docx/task/
  calendar/attendance/bitable/approval/contact-v3）全部手搭 `BaseRequest`
  （`.http_method` / `.uri`（`:name` 占位）/ `.paths` / `.add_query` / `.body` /
  `.token_types`）。
- **统一执行与返回**：`_invoke` 归一化为 `{ok, code, msg, data}`；成功 `{"ok": true, ...}`，
  失败原样带回飞书 `code`+`msg`；鉴权缺失返回 `ok=false` 而非抛异常。
- **鉴权**：绝大多数工具用 **tenant_access_token**（机器人身份，读 `PSI_FEISHU_APP_ID/SECRET`）。
  唯独**文档搜索**需 **user_access_token（UAT，用户 OAuth）**。
- **零新增依赖 / src 零改动**：除最早的 feishu **channel**（那次改了 `src/`）外，所有
  工具/技能/定时任务全部落在 `examples/haitun-workspace/` 内，不改 pyproject / nuitka /
  pyinstaller。
- **测试**：`tests/test_feishu.py`，用 `_CapturedInvoke`/`_PagedInvoke` mock `_invoke`，
  断言组装的 BaseRequest（method/uri/paths/query/body）与响应解析，**不打真实 API**。
  门禁：`ruff check` + `ruff format --check` + `ty check` + pytest 全绿。当前 96 passed。

## 三、能力清单（按域）

### 1. Channel（收发消息底座，改 src/）

飞书机器人通道：`src/psi_agent/channel/feishu/`（`ChannelFeishu` dataclass + `client.py`），
经 `cli.py`/`_run.py` 注册，卡片流式渲染。含处理状态表情（`Typing` 处理中、`CrossMark`
失败）。这是唯一改 `src/` 的部分，也是所有工具的凭据来源（`PSI_FEISHU_APP_ID/SECRET`）。

### 2. 文档读取 + 云文档评论（tenant）

- `feishu_doc_read(file_type, token, max_chars)` — 读 docx/doc/sheet 全文
- `feishu_drive_add_comment` / `feishu_drive_list_comments` /
  `feishu_drive_list_comment_replies` / `feishu_drive_reply_comment`（可选 @）
- `feishu_wiki_get_node(token)` — wiki 节点 → 底层 obj_token/obj_type

### 3. 消息与原生话题（tenant）

- `feishu_chat_find(name, exact)` — 按群名找 chat_id
- `feishu_chat_find_member(chat_id, name, ...)` — 群内按名字找 open_id
- `feishu_message_send` / `feishu_message_reply`（`reply_in_thread` 形成话题）/
  `feishu_message_list`
- `feishu_topic_start(...)` — 发起当日话题根消息（post 富文本 @，机器人不能用 text 消息 @）
- `feishu_thread_read(thread_id, ...)` — 读话题下全部回复（谁发了什么）

### 4. 原生任务 Tasks v2（tenant）

- `feishu_task_create(summary, description, due, assignees, followers)` — members 格式
  `{id, type:"user", id_type:"open_id", role:"assignee"|"follower"}`
- `feishu_task_list` / `feishu_task_get` / `feishu_task_update` / `feishu_task_complete`
- 语义坑：list 只能列**机器人自己负责**的 my_tasks；update_fields 列了但不给值=清空。

### 5. 日历日程（tenant）

- `feishu_calendar_create_event(summary, start, end, description, attendees, timezone)`
  — 建在机器人主日历（先 `primary` 拿 calendar_id），可加参与人。

### 6. 考勤查询（tenant，只读）

- `feishu_attendance_query(user_ids, date_from, date_to, employee_type, need_overtime)`
  — 查打卡结果（谁哪天几点打卡+Normal/Late/Lack）。**不做代打卡**。

### 7. 多维表格 Bitable（tenant，通用读写）

- `feishu_bitable_list_tables` / `feishu_bitable_list_records` /
  `feishu_bitable_create_record(app_token, table_id, fields_json)`
- 前提：把应用加为该 base 的协作者（可编辑），否则 403（1254302）。

### 8. 文档搜索（**user OAuth，唯一非 tenant**）

- `feishu_auth_start(scopes)` → 返回浏览器授权 URL（`accounts.feishu.cn/.../authorize`）
- `feishu_auth_complete(code)` → 授权码换 UAT，存 `<workspace>/.psi/feishu/uat.json`
- `feishu_docs_search(search_key, count, offset, docs_types)`
- 关键坑：国内版飞书**无设备流**（v2 端点 404），改用**授权码流 + 手动粘贴 code**；
  搜到的是**授权用户可见范围**的文档，非全局。

### 9. 审批 + 单据下载 + 通讯录（tenant，报告闭环）

- `feishu_approval_list_tasks` — 列某人的审批任务
- `feishu_approval_list_instances(approval_code, start_time, end_time)` — 列某审批流全部实例
- `feishu_approval_get(instance_id)` — 实例详情 + 解析出的 `attachments`
- `feishu_approval_decide(approve, ...)` — 代某审批人通过/拒绝
- `feishu_file_download(source, save_path, is_url)` — 下载文件/附件
- `feishu_department_members(department_id, ..., recursive)` — 部门/全员名单
- 关键坑：**审批表单附件是 12h 有效的直链 URL（kind=url），不是 drive token**；
  只有 document 控件回 drive token（kind=drive）。读详情后要立刻下。

## 四、定时任务（本地专属，不进 git、不部署到远端）

`schedules/daily-todo-topic`（每日发当日 todo 话题，截止 12:00）+
`schedules/todo-check`（12:00 后读回复逐条评价、@ 指定人）。
**这两个目录未被 git 跟踪，也不进 `git archive` 部署包——定时任务只在本地跑。**

## 五、技能（编排型，规则不写死）

- `skills/feishu-mentor-feedback` — mentor 反馈写入/汇总 bitable
- `skills/feishu-attendance-payroll` — 名单→考勤→按用户当次给的公式算劳务费出表
- `skills/feishu-reimbursement-archive` — 审批实例→下载单据到每笔一个文件夹→按用户
  当次给的清单校验→汇总表

## 六、用户侧前置（飞书后台汇总）

- 自建应用 + 机器人能力开启；`PSI_FEISHU_APP_ID/SECRET` 注入 gateway 环境。
- Scopes（按用到的域申请）：im 消息、docx/drive 文档评论、task:task:write、
  calendar、attendance:task:readonly（+考勤后台数据范围）、bitable:app（+base 协作者）、
  approval:approval:readonly、drive:drive:readonly、contact.base:readonly
  （+ user.employee_id:readonly，+通讯录权限范围=全部成员）。
- 文档搜索：注册 redirect_uri（如 `http://localhost/`）+ `offline_access` + 真机走一次授权。
- 报销技能：提供对应审批流的 `approval_code`。

## 七、非目标（YAGNI，跨域汇总）

- 不做代打卡（写考勤流水）；不做任务的 members/reminders/tasklist 增改；
  不做评论删除/解决；不做 bitable 记录删改/字段管理；不做多用户 UAT；
  不做 session 主动推送 / channel 轮询（定时任务靠工具自身发 API 绕过）；
  不在 API 层改飞书审批流定义（“设条件”只能靠 agent 作为审批人校验）。
