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
（`.psi/` 已 gitignore），**按 `user_key`（用户 open_id）分槽存储**，多人授权互不覆盖（见 §9）。

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
| `feishu_auth_start(user_key)` | 拼 `accounts.feishu.cn/.../authorize` URL，state 存 pending。**scope 固定不暴露给 LLM**（见 §9.2） |
| `feishu_auth_complete(code, user_key)` | app_access_token 换 UAT，按 `user_key` 存 FileTokenStore；支持粘整段 URL |
| `feishu_docs_search(search_key, count, offset, docs_types, user_key)` | `POST /suite/docs-api/search/object`（`token_types={USER}`）；以 `user_key` 对应用户身份搜索 |

`user_key` = 消息发送者 open_id（来自 channel 注入的 `<feishu_context>.sender_open_id`）；空则回落 `default`。详见 §9。

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
记录删改/字段管理；不做 session 主动推送 / channel 轮询；不在 API 层改
飞书审批流定义（“设条件”靠 agent 作为审批人校验）。

> 注：原“不做多用户 UAT”已在 §9 落地（按 `user_key` 隔离）。仍未做的相关项见 §9.3。

---

## 9. 后续增强：多用户 UAT 隔离 + scope 固定 + 建知识库 + 写入类以用户身份调用 + 一步建带内容文档（2026-07-20）

**分支**：`feishu-per-user-uat`。场景：公司里每人与 agent 各有对话框，用全局搜索查
知识库 / 审阅交付物，需每人各自授权、各搜自己可见的文档，互不覆盖。

### 9.1 按用户隔离 UAT

- 此前 UAT 存储 key 写死常量 `"default"`，多人授权互相覆盖。底层 lark SDK 的
  `FileTokenStore` 本就支持一个 JSON 多 user key，只是没用上。
- `auth_start_impl` / `auth_complete_impl` / `_get_valid_uat` / `search_docs_impl` 加
  `user_key` 参数；三个对外工具（`feishu_auth_start` / `feishu_auth_complete` /
  `feishu_docs_search`）暴露 `user_key`。
- `_norm_user_key(user_key)`：空 → `"default"`（向后兼容单用户 / 本地 dev）。
- `_pending_auth_path(user_key)` 按用户分文件（正则清洗非 `[A-Za-z0-9_-]` 字符，防路径
  穿越），避免并发授权互相清掉对方的 pending 文件。
- **工具本身不知道调用者身份**（纯函数），故 `user_key` 必须作显式参数：agent 从 channel
  注入的 `<feishu_context>.sender_open_id` 取值传入，同一用户三处工具须传相同 `user_key`。

### 9.2 scope 固定，不暴露给 LLM（修 20043）

- 现象：agent 调 `feishu_auth_start` 时自行编造无效 scope（如 `drive:drive:drive:readonly`），
  飞书授权页报错 20043 拒绝整个授权。
- 修复：`feishu_auth_start` 工具签名**去掉 `scopes`**，LLM 碰不到；wrapper 恒传空串，由
  impl 回落到固定 `_DEFAULT_SCOPES = "docs:doc:readonly drive:drive:readonly offline_access"`。
  impl 仍保留 `scopes` 参数供内部 / 测试。该组 scope 仍需在飞书后台权限管理开通并发版。

### 9.3 仍未做（诚实边界）

- OAuth 回调仍需用户手动从地址栏回传 code（无自动回调服务）。
- UAT 仍明文存（`FileTokenStore` 本就 dev-only，会告警）；生产需自定义 TokenStore。
- `auth_complete_impl` 不校验 CSRF state（沿用既有行为，仅把 pending 文件按用户分开）。

### 9.4 创建知识库（wiki space，复用 UAT）

此前 wiki 工具只能往**已有**知识库里建文档（`feishu_wiki_create_doc`），不能建**新**知识库。

- `feishu_wiki_create_space(name, description, open_sharing, user_key)` →
  `POST /open-apis/wiki/v2/spaces`。**该接口只吃 UAT（不支持 tenant token）**，新库归授权
  用户所有——正好复用 §9.1 的 UAT 按用户隔离机制（`_get_uat_client` + `_get_valid_uat(user_key)`
  + `RequestOption.user_access_token`，与 `feishu_docs_search` 同款）。
- `open_sharing` 仅接受 `open` / `closed`（或空），impl 侧校验非法值直接报错，不打 API。
- 限流约 10 次/分钟（飞书侧）；需 `wiki:space:write_only` 或 `wiki:wiki` scope。
- 未授权返回 `need_auth=True`，与搜索一致，走「先问再授权」流程。

### 9.6 写入类工具以用户身份调用（修"机器人非协作者"权限不足）

现象：用户用自己的 UAT 建了知识库（库归用户），机器人（tenant token）默认不是协作者，
往里建文档 / 写正文时权限不足；且机器人应用默认不在组织架构里，无法手动加为协作者。

- 共享 `_invoke(request, user_key=None)` 加可选 `user_key`：传了（非空）就走
  `_invoke_as_user`（`_get_uat_client` + `_get_valid_uat(user_key)` + `RequestOption
  .user_access_token`），否则保持原 tenant token 行为（向后兼容）。抽出 `_resp_to_result`
  统一解析响应。飞书文档节点 / docx / bitable 写入等接口均支持 UAT。
- **写入类** impl + 工具 wrapper 加 `user_key` 透传：`create_wiki_node` / `create_docx` /
  `append_doc_content` / bitable(`create_record` / `delete_records` / `clear_table` /
  `delete_fields`) / task(`create` / `update` / `complete`) / drive 评论(`add_comment` /
  `reply_comment`)。
- **刻意不加 UAT**：日历(`primary` 是机器人日历)、消息发送/回复(机器人身份回复才对)、
  考勤(只读、仅认 tenant)、纯读取类——这些以机器人身份或本就不支持 UAT，强上 UAT 反而错。
- agent 从 `<feishu_context>.sender_open_id` 取 `user_key`；一条"建库→建文档→写正文"
  的链路要全程传同一个 `user_key`（都以该用户身份操作,才有权限）。

### 9.7 建带内容的 wiki 文档：一步到位（修"空节点"）

现象：往知识库建带正文的文档是「建节点 + 写正文」两步 LLM 工具调用，第二步失败/漏调时
会留下**空节点**。

- 新增 `create_wiki_doc_with_content_impl(space_id, title, content, parent_node_token, user_key)`
  + 工具 `feishu_wiki_create_doc_with_content`：一次调用内部先 `create_wiki_node_impl` 再
  `append_doc_content_impl`。
- 部分失败**不静默**：正文写入失败时仍返回 `node_token`/`obj_token` + 错误（`body_written=False`，
  含 `need_auth`），便于用相同 `user_key` 调 `feishu_doc_append_content` 补写；正文为空（或纯空行）
  按成功处理并标 `note`，不误报。
- TOOLS.md 引导优先用该原子工具；旧的 `feishu_wiki_create_doc` / `feishu_doc_append_content` 保留。

### 9.8 文件变更

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `tools/_feishu_impl.py` | `_invoke` 加可选 `user_key`(UAT 分支) + `_invoke_as_user` + `_resp_to_result`；`_norm_user_key`；`_pending_auth_path` 按用户分文件 + 防穿越；新增 `create_wiki_space_impl`、`create_wiki_doc_with_content_impl`；写入类 impl 透传 `user_key` |
| 修改 | `tools/feishu_auth.py` | `feishu_auth_start`（去 `scopes`、固定 scope）/ `feishu_auth_complete` 暴露 `user_key` |
| 修改 | `tools/feishu_docs.py` | `feishu_docs_search` 暴露 `user_key` |
| 修改 | `tools/feishu_wiki.py` | 新增 `feishu_wiki_create_space`、`feishu_wiki_create_doc_with_content`；`feishu_wiki_create_doc` 暴露 `user_key` |
| 修改 | `tools/feishu_doc.py` | `feishu_doc_create` / `feishu_doc_append_content` 暴露 `user_key` |
| 修改 | `tools/feishu_bitable.py` | `create_record` / `delete_records` / `clear_table` / `delete_fields` 暴露 `user_key` |
| 修改 | `tools/feishu_task.py` | `create` / `update` / `complete` 暴露 `user_key` |
| 修改 | `tools/feishu_drive.py` | `add_comment` / `reply_comment` 暴露 `user_key` |
| 修改 | `tests/test_feishu.py` | 按用户隔离 / pending 防穿越 / 转发 user_key / scope 恒为默认值 / `_invoke` tenant·UAT·need_auth / 原子建文档(成功·正文失败回报 node·空正文·建节点失败短路) 等测试；fake `_invoke`/`_CapturedInvoke`/`_PagedInvoke` 接受 `user_key` |
| 修改 | `TOOLS.md` | 引导 agent 传 `sender_open_id` 作 `user_key`、先问再授权、建文档链路全程同一 `user_key`、优先用一步到位建文档工具 |
| 修改 | `tests/test_feishu.py` | 按用户隔离 / pending 防穿越 / search+建库+建文档节点 转发 user_key / scope 恒为默认值 / `_invoke` 空 user_key 走 tenant、非空走 UAT、未授权 need_auth 等测试；fake `_invoke`/`_CapturedInvoke`/`_PagedInvoke` 接受 `user_key` |
| 修改 | `TOOLS.md` | 引导 agent 传 `sender_open_id` 作 `user_key`，先问再授权，建文档链路全程同一 `user_key` |

---

## 10. 后续增强：删除云文档/文件（复用 user_key）

**目标**：给 agent 加删除飞书文档/文件能力，与其它写入类工具一致——先用用户身份(UAT,
`user_key`)，未传则回退机器人 tenant token。

- 接口 `DELETE /open-apis/drive/v1/files/:file_token?type=...`，**tenant / user token 都支持**，
  scope `drive:drive` 或 `space:document:delete`。删除进**回收站(可恢复)**；删文件夹异步返回 task_id。
- `_build_delete_file_request(file_token, file_type)` + `delete_file_impl(file_token, file_type, user_key="")`：
  校验 file_token 非空、file_type ∈ {file, docx, doc, sheet, bitable, mindnote, slides, folder, shortcut}；
  走共享 `_invoke(..., user_key=user_key)`（非空→UAT）；成功回 `{file_token, type, task_id?}`。
- 工具 `feishu_drive_delete_file(file_token, file_type, user_key)`。
- **删 wiki 里的文档（刻意为之）**：飞书 wiki v2 **无**独立删节点 API，删知识库文档 = 删其底层
  docx——`feishu_wiki_get_node(token)` 取 `obj_token`/`obj_type` → `feishu_drive_delete_file`。不新增
  "删 wiki 节点"工具，靠组合覆盖，TOOLS.md 写清这条路径。

### 10.1 非目标
- 不做彻底删除（接口本就是删到回收站，可恢复）。
- 不做文件夹删除的异步 task 状态轮询（仅透传 task_id）。

### 10.2 文件变更

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `tools/_feishu_impl.py` | 新增 `_build_delete_file_request` + `delete_file_impl(user_key)`（走 `_invoke` UAT/tenant） |
| 修改 | `tools/feishu_drive.py` | 新增 `feishu_drive_delete_file` 工具 |
| 修改 | `tests/test_feishu.py` | DELETE 请求组装 / 空 token / 非法 type / 文件夹 task_id / user_key 走 UAT / 未授权 need_auth |
| 修改 | `TOOLS.md` | 第 8 条：删除文档/文件用 `feishu_drive_delete_file`，删 wiki 文档走 get_node→delete_file |

---

## 11. 后续增强：wiki 读工具支持 user_key（能以用户身份访问知识库）

**现象**：`feishu_wiki_list_spaces` 只用机器人 tenant token，机器人不是任何知识库的成员 → 返回空，
agent 误判"企业没有知识库"或让用户手动把机器人加为协作者。根因是**读类 wiki 工具没接 user_key**。

- `list_wiki_spaces_impl` / `get_wiki_node_impl` 加 `user_key`，走 `_invoke(..., user_key=...)`（UAT）。
- 新增 `list_wiki_nodes_impl` + 工具 `feishu_wiki_list_nodes(space_id, page_size, page_token,
  parent_node_token, user_key)`：`GET /wiki/v2/spaces/:space_id/nodes`，列知识库里的文档/页面，可下钻。
  这补上了"浏览知识库内容"的缺口（此前只能建、不能列内节点）。
- 完整读链路：`feishu_wiki_list_spaces(user_key)` → `feishu_wiki_list_nodes(space_id, user_key)`
  → `feishu_wiki_get_node(token, user_key)` → `feishu_doc_read`。

### 11.1 文件变更

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `tools/_feishu_impl.py` | `list_wiki_spaces_impl` / `get_wiki_node_impl` 加 `user_key`；新增 `_build_list_wiki_nodes_request` + `list_wiki_nodes_impl` |
| 修改 | `tools/feishu_wiki.py` | `feishu_wiki_list_spaces` / `feishu_wiki_get_node` 暴露 `user_key`；新增 `feishu_wiki_list_nodes` |
| 修改 | `tests/test_feishu.py` | list_spaces user_key 走 UAT / get_node 转发 user_key / list_nodes 请求组装 + 必填校验 |
| 修改 | `TOOLS.md` | 第 9 条：访问/浏览知识库要带 user_key，list_spaces 空不代表没库、先带 user_key 重试 |
