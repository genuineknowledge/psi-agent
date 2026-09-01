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
与基础文档工具，它们各有独立规格）。全部落在 `agents/feishu/` 内，`src/`
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
| `feishu_approval_subscribe(approval_code)` | `POST /approval/v4/approvals/:approval_code/subscribe`（tenant，幂等，每定义订阅一次，开启状态变化主动推送——详见 channel 规格 §15） |
| `feishu_approval_unsubscribe(approval_code)` | `POST /approval/v4/approvals/:approval_code/unsubscribe` |
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
| `feishu-todo-board-sync` | 个人 ToDoList docx→按 `@人名` 拆条目→写进团队看板表格各人行的指定列（详见 §13） |

---

## 8. 非目标（YAGNI）

不做代打卡；不做任务 members/reminders/tasklist 增改；不做评论删除/解决；不做 bitable
记录删改/字段管理；不做 session 主动推送 / channel **轮询**；不在 API 层改
飞书审批流定义（“设条件”靠 agent 作为审批人校验）。

> 注：原“不做多用户 UAT”已在 §9 落地（按 `user_key` 隔离）。仍未做的相关项见 §9.3。
>
> 注（2026-07-25）：“不做 channel 轮询”仍成立，但审批状态变化通知已由 channel 层
> **事件推送**（非轮询、非 session-push）实现——`feishu_approval_subscribe` 开订阅、
> channel 收 `approval_instance` 事件后 DM 申请人。设计详见 channel 规格 §15。

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

- ~~OAuth 回调仍需用户手动从地址栏回传 code（无自动回调服务）。~~ 已解决，见第 14 节：
  回调自动落地（Gateway 中继 / 本机回环），手工贴码降级为兜底。
- ~~`auth_complete_impl` 不校验 CSRF state。~~ 已解决，见第 14 节：state 现在真校验，并启用 PKCE S256。
- UAT 仍明文存（`FileTokenStore` 本就 dev-only，会告警）；生产需自定义 TokenStore。

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

---

## 12. 后续增强：文件下载支持 user_key（读知识库里的 PDF/附件）

**现象**：用户问"企业章程",agent 已能在知识库搜到那份 `章程.pdf`,但**下载失败** → 只能让用户
手动复制粘贴。根因:`feishu_file_download`(media 下载)写死机器人 tenant token,而该 PDF 在用户
的知识库/云盘、机器人无权限。与 `list_spaces` 返回空同类——读类工具没接 user_key。

- `_download_media_bytes(file_token, user_key="")`:user_key 非空时用 `_get_uat_client()` +
  `_get_valid_uat(user_key)` + `RequestOption.user_access_token`(与 `_invoke_as_user` 同款)以用户身份
  下载;否则维持 tenant client。未授权返回带 need_auth 的错误。`_build_media_download_request` 的
  token_types 已含 USER。
- `download_file_impl(source, save_path, is_url=False, user_key="")`:仅 is_url=False(media token)
  时透传 user_key;is_url=True(直链)不需 token、不受影响。
- 工具 `feishu_file_download` 暴露 `user_key`。
- **读 PDF 本就有解**:下载后用 `ocr-and-documents` 技能(PyMuPDF 核心依赖)抽文本。完整链路:
  `feishu_wiki_get_node(user_key)` → `feishu_file_download(..., user_key)` → ocr 抽文本。

### 12.1 文件变更

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `tools/_feishu_impl.py` | `_download_media_bytes` / `download_file_impl` 加 `user_key`(media 下载走 UAT/tenant) |
| 修改 | `tools/feishu_drive.py` | `feishu_file_download` 暴露 `user_key` |
| 修改 | `tests/test_feishu.py` | media 下载 user_key 走 UAT / 未授权 need_auth / 空 user_key 走 tenant |
| 修改 | `TOOLS.md` | 第 10 条:读知识库 PDF/附件 = get_node→download(带 user_key)→ocr 抽文本 |

---

## 13. 后续增强：电子表格区域读 + 个人 ToDoList 搬进团队看板（2026-07-27）

**分支**：`add-feishu-skills1`（PR #496）。场景：团队每人一篇飞书 docx 周志 / ToDoList
（按日期倒序分段，`大目标 / 子目标 / ToDo` 层级缩进，任务行用 `@人名` 点名协作者），
另有一张团队看板电子表格（**列 = 日期，行 = 人**，每格放该人当天一整段待办文本）；
把前者搬进后者此前只能人工复制粘贴。

**根因**：读电子表格只有 `feishu_doc_read(file_type="sheet")` 一条路——它把**整本工作簿
一次性倒成文本**，既定位不到「某人在第几行」，也没法在写之前确认「目标格是否已被填过」。
写侧（`feishu_sheet_write` / `append` / `format`）本就有，缺的是**区域读与工作表寻址**。

### 13.1 电子表格区域读（两个新工具）

- `list_sheet_tabs_impl(token, user_key)` + 工具 `feishu_sheet_tabs`：
  `GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query`，列
  `sheet_id`/`title`/`index`/行列数。**`SHEET_ID` 不在表格 URL 里**，而所有区域都寻址成
  `"SHEET_ID!A1:B2"`，所以不写死某张表就必须先查它。兼容 `sheet_id` / `sheetId` 两种拼写。
- `read_sheet_range_impl(token, range_, max_chars, user_key)` + 工具 `feishu_sheet_read`：
  复用既有 `_build_sheet_values_request`（`GET .../sheets/v2/.../values/:range`），返回
  `rows`（行数组）+ `row_count` + `truncated`；`max_chars` 按累计字符预算截断（0 = 不限）。
- `_flatten_sheet_cell(cell)`：**飞书单元格不都是标量** —— `@某人` 是
  `{"type":"mention","name":...}` dict，带样式文本是 run 段 list（`{"type":"text","text":...,
  "segmentStyle":...}`），另有 bool / 数字。统一拍平成可见文字，否则人名列读出来是一坨 JSON
  没法匹配人（拍平后是 `"@张三"`，**匹配前要去掉开头的 `@`**）。
- **鉴权**：两者都是读类，按 §11 / §12 的口径接 `user_key` 并走 `_invoke(..., prefer="tenant")`
  —— tenant 优先、仅在机器人被拒时回落该用户 UAT。表格归个人时不带 `user_key` 会 403。

### 13.2 技能 `feishu-todo-board-sync`（编排型，结构不写死）

7 步：换 token 认类型（`/wiki/` 后是 **node_token 不是文档 id**；源常是 docx、目标常是 sheet）
+ 查 `SHEET_ID` → 读源 docx 切最新日期段 → 按 `@人名` 拆归属 → 认表头行/人名列并定位每人行
→ 组装单元格文本 → 探目标格是否已占 → 写入。三条硬规则：

1. **归属按 `@人名` 拆**：条目里 @谁归谁，一条 @多人则分别进各人；没 @任何人的归**文档主人**
   （标题「XXToDoList」里的 XX）。`大目标 / 子目标` 是**共有背景**，不拆给个人。
2. **目标列由调用方显式给**：绝不从源文档日期推——表头是 `7.24`（点分）、源文档是 `7-27`
   （横线分），格式本就不一致，推错就写串行。
3. **目标单元格非空先报警**：把现有内容摆给用户、等明确确认才覆盖，多个人一次性列全。

**结构每次现场探**：表头在第几行、人名在第几列、`SHEET_ID` 一律读出来；技能里的示例值
（那两个 wiki token / `46a582` / E 列 / 第 7 行）只作说明，并有测试锁住这点。
**@到的人不在表里则如实报告跳过，不新增行**。抽 `@人名` **禁用「@ + 2~4 汉字」贪心正则**
（原文有 `@王浦丞的ToDo`，会截出「王浦丞的」这个不存在的人），改用表内人名清单反向匹配。

### 13.3 非目标

不改源 docx；不给看板新增行/列；不自动推目标列；不静默覆盖；不一次搬所有历史日期段
（默认只搬最新一段，要别的段由调用方明说）。

### 13.4 文件变更

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `tools/_feishu_impl.py` | 新增 `list_sheet_tabs_impl`（sheets/v3 sheets/query）、`read_sheet_range_impl`（sheets/v2 values + `max_chars` 预算截断）、`_flatten_sheet_cell`（mention/富文本/bool/数字 → 可见文字）；两者均接 `user_key` 走 tenant 优先 |
| 修改 | `tools/feishu_sheet.py` | 新增工具 `feishu_sheet_tabs` / `feishu_sheet_read`（含 `user_key`）；模块 docstring 从「只写」改为「区域读 + 写」 |
| 新增 | `skills/feishu-todo-board-sync/SKILL.md` | 个人 ToDoList docx → 团队看板表格的 7 步搬运技能（三条硬规则 + 结构现场探） |
| 修改 | `tests/test_feishu.py` | sheet_tabs 请求组装 / `sheetId` 兼容 / 缺 token；sheet_read 请求组装 / **mention 与富文本拍平** / `max_chars` 截断与 0 不限 / 缺 token·range；两读类工具转发 `user_key` 且 `prefer="tenant"`；工具面名单补两个新工具 |
| 新增 | `tests/test_feishu_todo_board_sync.py` | 技能校验：frontmatter 合规 / 引用的 `feishu_*` 工具真实存在 / 流程必需 5 工具都点到 / 三条硬规则仍在正文 / 两种文件类型与 node_token·obj_token 区别 / 结构靠现场探不写死 |
| 修改 | `TOOLS.md` | 补两条：`feishu_sheet_tabs`（`SHEET_ID` 不在 URL 里）、`feishu_sheet_read`（区域读、mention 拍平、用于定位人名行与写前探目标格） |
| 修改 | `agents/feishu/AGENTS.md` | 工具表补 `feishu_sheet` 行（此前连写工具都没登记）；技能列表补 `feishu-todo-board-sync` |

## 14. 后续增强：授权免手抄 code（回调自动落地 + PKCE + state 校验，2026-07-28）

**分支**：`fix-oauth-easy-auth`。场景：用户点完「同意授权」后，还要自己盯浏览器**地址栏**、
把 `code=` 后面那串复制回对话——每次授权都要手工搬一次，多用户/手机端尤其难交代。
这补掉 §9.3 里挂着的两条「诚实边界」。

**根因**：`redirect_uri` 默认指向 `http://localhost/` 这个**没人监听**的地址。
第三方只把 `code` 拼在 `redirect_uri` 上跳一次浏览器，落地无人接收，于是**用户成了传输层**。
不是 OAuth 协议本身麻烦，是回调这一段断了。

**先排除**：飞书中国区**没有** device flow（RFC 8628）——`authen/v2/oauth/device_authorization`
在 `open.feishu.cn` / `passport.feishu.cn` / `accounts.feishu.cn` 上实测全 404，
SDK 里那套用不上，别再试。故走 RFC 8252 回调落地（`gh` / `gcloud` / `aws sso` 同款）。

### 14.1 两条自动接收通道（`plan_receiver` 自动择优）

`tools/_oauth_receiver.py` 按环境选，顺序 `gateway → loopback → manual`：

- **`gateway`**：配了 `PSI_OAUTH_CALLBACK_BASE` 即启用。回调打到 Gateway 的
  `GET /oauth/callback`，`OAuthRelay` 以 `state` 为键一次性暂存，工具侧用同一个 `state`
  去 `GET /oauth/code` 取件。**浏览器与 agent 不必同机**——手机上点授权也能自动回流，
  是飞书多用户部署唯一可行的一条。
- **`loopback`**：在 `127.0.0.1:PSI_OAUTH_LOOPBACK_PORT`（默认 `17860`）起**一次性**
  HTTP 监听。仅同机成立，本机部署零配置。端口被占则不抢，直接降级。
- **`manual`**：两条都不可用才回落原手工贴码，行为不变——只是不再是唯一选择。

无论走哪条，`redirect_uri` 都必须先登记到飞书后台重定向 URL 列表，否则跳转前就被拒（20071）。

**Gateway 侧刻意不做 token 交换**：它没有 `app_secret`、没有 PKCE verifier、也不知道是哪个
飞书用户；那些都留在发起方，中继只搬运一次性 code，故 `OAuthRelay` 零持久化、无跨用户鉴权
（`state` 本身即高熵取件码）。TTL 600s，`_MAX_PENDING=256` 满则淘汰最旧一条。

### 14.2 顺带补上的两个既有缺口

- **state 真校验**（原 §9.3 第 3 条）：此前 `state` 只写进 pending 文件、从不比对。
  现在回环 handler 对不上即回 400 且**不写结果、继续等真回调**（别的进程或恶意页面打过来的
  回调不能顶替真授权结果）；Gateway 侧 `state` 就是中继键。熵从 `os.urandom(8)` 提到 24 字节。
- **启用 PKCE S256**：authorize 带 `code_challenge` / `code_challenge_method=S256`，
  换 token 带 `code_verifier` + `redirect_uri`。verifier 与 redirect_uri 必须与 authorize
  阶段一致（不一致飞书报 20071；verifier 不匹配报 20049），故一并写进 pending 文件。

### 14.3 非目标

不做 device flow（飞书中国区无此接口）；不改 UAT 明文存储这条边界（§9.3 仍成立）；
Gateway 不参与 token 交换；不给回调中继加持久化或跨进程共享。

### 14.4 文件变更

| 操作 | 文件 | 说明 |
|---|---|---|
| 新增 | `src/psi_agent/gateway/_oauth_manager.py` | `OAuthRelay` — `state → {code, error}` 一次性信箱（TTL 600s、上限 256、进程内存不落盘） |
| 修改 | `src/psi_agent/gateway/server.py` | `app["oauth"]`；`GET /oauth/callback`（收回调、回「授权成功」页）+ `GET /oauth/code`（发起方取件，一次性） |
| 修改 | `src/psi_agent/gateway/_openapi.py` | 两个 `/oauth/*` 端点的 schema |
| 新增 | `agents/feishu/tools/_oauth_receiver.py` | `plan_receiver`（通道择优）/ `wait_loopback`（一次性回环监听，校验 state）/ `poll_gateway`（轮询取件） |
| 修改 | `tools/_feishu_impl.py` | `auth_start_impl` 选通道 + 写 PKCE verifier/redirect_uri/mode 进 pending，返回 `auto_receive`/`mode`/`next_step`；新增 `auth_wait_impl`（10-600s）+ `_read_pending` + `_new_pkce_pair` + `_explicit_redirect_uri`；`auth_complete_impl` 带上 verifier/redirect_uri；删已无用的 `_redirect_uri` |
| 修改 | `tools/feishu_auth.py` | 新增工具 `feishu_auth_wait(user_key, timeout_seconds)`；`feishu_auth_complete` 降级为兜底路径 |
| 修改 | `TOOLS.md` | 「引导用户授权」改「默认免复制」：按 `auto_receive` 分支，并记两种部署配置 |
| 修改 | `agents/feishu/AGENTS.md` | 工具表补 `feishu_auth` 行；环境变量表补两个 `PSI_OAUTH_*` |
| 修改 | `src/psi_agent/gateway/AGENTS.md` | 架构图 + 模块表补 `OAuthRelay`；REST 表补两个 `/oauth/*`；新增 `## OAuthRelay` 段（含「刻意不做 token 交换」的理由） |
| 修改 | `AGENTS.md` | 目录树补 `_oauth_manager.py`（并补此前漏登记的 `_router_manager` / `_feishu_manager` / `_attention`） |
| 新增 | `tests/psi_agent/gateway/test_oauth_manager.py` | 一次性取件 / 未知 state / 空 state 报错 / 错误透传 / TTL 清理 / 上限淘汰（6） |
| 新增 | `tests/psi_agent/gateway/test_oauth_endpoints.py` | 成功页含「不用复制」/ 缺 state 400 / 错误记录 / 取件后 404（7） |
| 新增 | `agents/feishu/tests/test_oauth_receiver.py` | 通道择优 6 例 + 真实回环往返（成功 / state 不匹配后仍等真回调 / 错误回调 / 超时 / 无基址不轮询）（11） |
| 修改 | `agents/feishu/tests/test_feishu.py` | authorize URL 带 PKCE 且 verifier 落盘、`auto_receive` 两路径、换 token 带 verifier/redirect_uri、`auth_wait` 5 例 |


