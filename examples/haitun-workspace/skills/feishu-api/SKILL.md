---
name: feishu-api
description: "Calling any Feishu/Lark Open Platform endpoint through the generic feishu_api tool — 通讯录/组织架构查人、考勤组与班次配置、培训报名记录、云文档全局搜索、审批实例与任务查询、日历日程查询、任务(Task)增删改查、群信息与成员、知识库空间与节点。Use when a Feishu capability has no dedicated feishu_* tool, or when the user asks 查某人信息/查部门成员/查考勤配置/搜文档/查审批状态/查日程/管任务/查群成员/查知识库. Carries the endpoint tables, the token strategy, and the rule for when a dedicated tool must be used instead."
category: integration
---

# 飞书通用 API 调用

用 `feishu_api` 打任意飞书开放平台端点。专用工具只覆盖「请求形状容易搞错」的那些
（二进制上传、表格坐标、回应 id 解析），其余端点走这里 —— 端点知识放在本文档，
不占常驻上下文。

回复用中文，除非用户明显在用其他语言。

## 先检查有没有专用工具

`feishu_api` 能打任意端点，包括写操作。**给错 URI 就是一次真实写入**，所以下面这些
必须用专用工具，不要手搓请求：

| 场景 | 用这个 | 为什么不能手搓 |
|---|---|---|
| 发图片/文件/语音/视频 | `feishu_message_send_image` / `_send_file` / `_send_audio` / `_send_video` | body 必须是真文件句柄，JSON 表达不了；`feishu_api` 会直接拒绝并指路 |
| 上传到云盘 | `feishu_drive_upload` | 同上 |
| 表格写入 | `feishu_sheet_write` / `_append` | 裸 `!A1` 区间会**静默丢数据** |
| 多维表格写入 | `feishu_bitable_*` | 列名对不上会被**静默丢弃** |
| 移除表情回应 | `feishu_message_unreact` | 要先按 emoji 解析出 reaction_id，多个命中必须拒绝 |
| OAuth 授权 | `feishu_auth_*` | 管着 UAT 存储与回调接收 |
| 发/编辑消息、卡片 | `feishu_message_send` / `_edit` / `_edit_card` | `<at>` 升级 post、卡片 update_multi 等组包细节 |
| 读/写群公告 | `feishu_chat_announcement` / `_set` / `_clear` | 公告是 **docx 文档**（不是 im/v1），根 block_id 就是 chat_id，每次写都要按 `revision_id` 乐观锁重读 |
| 改群设置 / 禁言 | `feishu_chat_update` / `feishu_chat_mute` | 加人权限与群名片权限**必须成对**；禁言根本不在群设置那个 body 里（写了会被静默忽略） |
| 解散群 / 转让群主 | `feishu_chat_dismiss` / `feishu_chat_transfer_owner` | 解散**不可逆且不保留群记录**，工具要求显式 `confirm="解散群"` |
| 群菜单 / 群标签页 | `feishu_chat_menu_*` / `feishu_chat_tab*` | 菜单是三层嵌套包装对象、带子菜单的一级菜单不能有链接；标签页 11 种类型只有 2 种能建 |
| 搜索消息 | `feishu_message_search` | 只吃 user token，且**只返回 message_id**，必须回查才有正文 |
| 建/改用户、办离职 | `feishu_user_manage` | 离职**不可逆**且无上级时日历/问卷被直接删除，工具要求 `confirm="离职用户"`；改用户没传的字段不能变成清空 |
| 建/改/删/移动部门 | `feishu_department_manage` | 删部门**不可逆**且要求部门先清空（有人 43011 / 有子部门 43012，只能最深层往上删），工具要求 `confirm="删除部门"` |
| 建/改/删用户组 | `feishu_user_group` | 删组会让引用它的文档权限/审批流失去主体，要求 `confirm="删除用户组"`；建组硬要求通讯录范围=全部成员 |
| 增删用户组成员 | `feishu_user_group_members` | 飞书**一次只收一个成员**，工具循环并逐人回报成败；三个 member_* 参数不一致就 41072 |
| 按手机号/邮箱查人 | `feishu_contact_find` | 是 **POST** 不是 GET；企业邮箱一律查不到；离职的人默认**静默漏掉** |
| 部门树 / 部门详情 | `feishu_department_tree` / `feishu_department_get` | 递归+分页+去重，且 43010「部门过大」必须暴露出来而不是静默少一层 |

判断方法：先用 `tool_search` 找一下有没有 `feishu_` 开头的对应工具；有就用它。

## 参数怎么填

```
feishu_api(
  method="GET",
  uri="/open-apis/contact/v3/users/:user_id",
  paths_json='{"user_id":"ou_abc"}',
  query_json='{"user_id_type":"open_id"}',
  user_key="<sender_open_id>",
)
```

- `uri` **保留 `:name` 占位符**，值放 `paths_json` —— 别自己拼进去，交给 SDK 转义。
  占位符没填会直接报 `missing_path_params`，不会打出一个 404。
- `query_json` 的值会被字符串化；列表值会重复同一个 key。
- `body_json` 只在 POST/PUT/PATCH 用。

## token 策略

- `prefer="tenant"`（默认）：先用机器人身份，只在确实被拒时回落到调用者的 user token。
  绝大多数查询用这个。
- `prefer="user"`：直接要求调用者授权。用于**读某人自己的数据**（本人日程、本人待办）
  和**应归属于本人**的写入。
- `user_key` 一律传 `<feishu_context>` 里的 `sender_open_id` —— 不传就没有可回落的 token。
- `identity="user"` / `"bot"` 只在创建有归属的内容时才需要显式选。

## 端点表

### 通讯录 / 组织架构

| 要什么 | method + uri | 说明 |
|---|---|---|
| 查一个人 | `GET /open-apis/contact/v3/users/:user_id` | `query_json='{"user_id_type":"open_id"}'`；拿手机/邮箱/部门 |
| 查部门成员 | `GET /open-apis/contact/v3/users/find_by_department` | `query: department_id, page_size(≤50), page_token` |
| 按名字全局搜人 | `GET /open-apis/search/v1/user` | `query: query, page_size`；**只支持 user token**，必须 `prefer="user"` + `user_key` |
| 部门列表 | `GET /open-apis/contact/v3/departments/:department_id/children` | `query: page_size` |
| 批量查部门 | `GET /open-apis/contact/v3/departments/batch` | `query: department_ids` **重复同名 key** 传多个（`?department_ids=a&department_ids=b`），一次最多 50 个 |
| 父部门链 | `GET /open-apis/contact/v3/departments/parent` | `query: department_id(必填), page_size(≤50)`；返回**子→父**顺序且不含根部门 |
| 搜索部门 | `POST /open-apis/contact/v3/departments/search` | body `{"query":"部门名"}`；**只吃 user token**（`prefer="user"` + `user_key`），只匹配中文名不匹配国际化名 |
| 恢复离职成员 | `POST /open-apis/contact/v3/users/:user_id/resurrect` | 办错离职的回退路径；用户被删太久可能已不可恢复 |
| 人员类型枚举 | `GET /open-apis/contact/v3/employee_type_enums` | 建用户的 `employee_type` 自定义枚举号从这儿查 |

根部门 id 是 `0`。`user_id_type` 不传默认可能不是 open_id，查人时显式写上。
部门树和部门详情用 `feishu_department_tree` / `feishu_department_get`（已含递归、分页、
父链拼接和 43010 处理），别用上面两行手搓。

#### 角色（functional_role）

**飞书没有「列出所有角色」的接口** —— 这是最容易凭直觉试错的地方。`role_id` 只能从
建角色的返回值拿，或让用户去管理后台「组织架构 > 角色管理」里抄。别去猜一个
`/functional_roles` 的 GET，那个端点不存在。

| 要什么 | method + uri | 说明 |
|---|---|---|
| 查角色下全部成员 | `GET /open-apis/contact/v3/functional_roles/:role_id/members` | `query: page_size(≤100), user_id_type`；返回 `members[]` 含 `scope_type`（All/Part/None）与 `department_ids`（仅 Part 时有） |
| 查某成员管理范围 | `GET /open-apis/contact/v3/functional_roles/:role_id/members/:member_id` | 单人的管理范围 |
| 建角色 | `POST /open-apis/contact/v3/functional_roles` | body `{"role_name":"考勤管理员"}`，租户内唯一；返回 `role_id`（**记下来**，没有列表接口可以再查） |
| 改角色名 | `PUT /open-apis/contact/v3/functional_roles/:role_id` | |
| 删角色 | `DELETE /open-apis/contact/v3/functional_roles/:role_id` | |
| 批量加角色成员 | `POST /open-apis/contact/v3/functional_roles/:role_id/members/batch_create` | body `{"members":["ou_..."]}`（1-100）；返回逐人 `reason`：1 成功 / 2 id 非法 / 3 无该用户权限 / 4 已在角色 / 5 不在角色 |
| 批量删角色成员 | `POST /open-apis/contact/v3/functional_roles/:role_id/members/batch_delete` | |
| 批量设管理范围 | `PATCH /open-apis/contact/v3/functional_roles/:role_id/members/scopes` | |

scope 是 `contact:functional_role`（只读用 `contact:functional_role:readonly`）；
只吃 tenant token。`41202` = role_id 不存在，`41209` = 角色成员超 1000。

#### 用户组查询

用户组的增删改和成员增删用 `feishu_user_group` / `feishu_user_group_members`。
补充两个只读端点：详情 `GET /open-apis/contact/v3/group/:group_id`、
列表 `GET /open-apis/contact/v3/group/simplelist`（`query: type` 1 普通 2 动态）——
注意路径是**单数 `group`**，没有 `/groups`。

反查「这个人在哪些用户组」的端点是 `GET /open-apis/contact/v3/group/member_belong`
（本文档未逐字核对其 query 参数名，第一次调用先看飞书的报错提示）。

#### 关联组织（外部联系人）

飞书 `contact/v3` 里**没有 `external_user` 端点**。组织级的「外部联系人」是
**关联组织**（trust_party），scope `trust_party:collaboration.tenant:readonly`：

| 要什么 | method + uri |
|---|---|
| 可见关联组织列表 | `GET /open-apis/trust_party/v1/collaboration_tenants` — `query: page_size(1-100, 默认10), page_token` |
| 关联组织详情 | `GET /open-apis/trust_party/v1/collaboration_tenants/:tenant_key` |
| 对方可见部门/成员 | `GET /open-apis/trust_party/v1/collaboration_tenants/:tenant_key/visible_organization` |
| 对方部门详情 | `GET .../collaboration_tenants/:tenant_key/collaboration_departments/:department_id` |
| 对方成员详情 | `GET .../collaboration_tenants/:tenant_key/collaboration_users/:user_id` |

`1970011` = page_size 越界，`1970012` = page_token 非法。
若用户说的「外部联系人」其实是外部群里的人，那是 `feishu_chat_list_members`，不是这套。

#### 通讯录写操作为什么老是失败

这一批写端点**只吃 tenant token**（scope `contact:contact` / `contact:group` /
`contact:functional_role`），让用户授权也没用。而失败最常见的真因不是参数写错：

- `40004` / `41050` / `42009` —— 应用的**通讯录权限范围**没覆盖到目标部门/用户/用户组。
  这是开发者后台配的，改代码没用。
- `42010` —— 建用户组硬要求范围 = **全部成员**（只有这个动作要求）。
- 用 tenant token 查根部门 `0` 的子部门同样要求范围 = 全部成员，否则**返回空而不报错**。

### 考勤

| 要什么 | method + uri |
|---|---|
| 打卡记录 | `POST /open-apis/attendance/v1/user_tasks/query` — body: `{"user_ids":[...],"check_date_from":20260801,"check_date_to":20260807}`，query: `{"employee_type":"employee_id"}` |
| 考勤组列表 | `POST /open-apis/attendance/v1/groups/list` |
| 考勤组配置 | `GET /open-apis/attendance/v1/groups/:group_id` |
| 班次列表 | `POST /open-apis/attendance/v1/shifts/list` |
| 班次配置 | `GET /open-apis/attendance/v1/shifts/:shift_id` |

日期是 **整数** `YYYYMMDD`，不是字符串。`user_ids` 要的是 employee_id 体系，跟 open_id 不同。

### 云文档搜索

| 要什么 | method + uri |
|---|---|
| 全局搜文档 | `POST /open-apis/suite/docs-api/search/object` — body: `{"search_key":"关键词","count":20}` |

**只支持 user token**：`prefer="user"` + `user_key`，搜到的是那个人有权限看的东西。

### 审批（查询部分）

| 要什么 | method + uri |
|---|---|
| 我的待办 | `POST /open-apis/approval/v4/tasks/query` — body: `{"user_id":"ou_...","page_size":20}` |
| 实例列表 | `POST /open-apis/approval/v4/instances/query` — body 带 `approval_code` / 时间区间 |
| 实例详情 | `GET /open-apis/approval/v4/instances/:instance_id` |
| 审批定义 | `GET /open-apis/approval/v4/approvals/:approval_code` | 拿表单字段结构，代人提交前必读 |

发起、同意/拒绝、订阅仍用 `feishu_approval_create` / `_decide` / `_subscribe`。

### 日历

| 要什么 | method + uri |
|---|---|
| 日程列表 | `GET /open-apis/calendar/v4/calendars/:calendar_id/events` — query: `start_time`/`end_time`（**秒级时间戳字符串**）、`page_size` |
| 主日历 id | `POST /open-apis/calendar/v4/calendars/primary` | `prefer="user"` 拿本人主日历 |

建日程仍用 `feishu_calendar_create_event` / `_create_per_person`。

### 任务 (Task v2)

| 要什么 | method + uri |
|---|---|
| 建任务 | `POST /open-apis/task/v2/tasks` — body: `{"summary":"...","due":{"timestamp":"..."},"members":[{"id":"ou_...","role":"assignee"}]}` |
| 查任务 | `GET /open-apis/task/v2/tasks/:task_guid` |
| 列任务 | `GET /open-apis/task/v2/tasks` — query: `page_size`, `completed` |
| 改任务 | `PATCH /open-apis/task/v2/tasks/:task_guid` — body: `{"task":{...},"update_fields":["summary"]}` |
| 完成任务 | `PATCH` 同上，`update_fields:["completed_at"]`，`completed_at` 为毫秒字符串 |

改任务**必须**带 `update_fields`，不带则什么都不会变。

### 群 / 知识库

| 要什么 | method + uri |
|---|---|
| 搜我在的群 | `GET /open-apis/im/v1/chats/search` — query: `query`, `page_size` |
| 群成员 | `GET /open-apis/im/v1/chats/:chat_id/members` — query: `page_size`(≤100), `page_token` |
| 知识空间列表 | `GET /open-apis/wiki/v2/spaces` — query: `page_size` |
| 空间节点 | `GET /open-apis/wiki/v2/spaces/:space_id/nodes` — query: `parent_node_token`, `page_size` |
| 节点详情 | `GET /open-apis/wiki/v2/spaces/get_node` — query: `token`（wiki node_token） |

wiki 节点的 `obj_token` 才是文档 id，读内容要用它而不是 `node_token`。
建群拉人用 `feishu_chat_create`；建 wiki 文档用 `feishu_wiki_create_doc*`。

**群的运营几乎都有专用工具了，别手搓**：群列表 `feishu_chat_list`、群公告
`feishu_chat_announcement`/`_set`/`_clear`、群设置 `feishu_chat_update`、禁言
`feishu_chat_mute`、转让群主 `feishu_chat_transfer_owner`、解散群
`feishu_chat_dismiss`、群菜单 `feishu_chat_menu_*`、群标签页 `feishu_chat_tab*`。
这些端点各自都有一个「照着文档写也会错」的地方（公告是 docx 文档且按 revision 乐观锁、
禁言不在群设置那个 body 里、加人权限和群名片权限必须成对、解散不可逆），
所以护栏在工具里，不在这张表里。

### 培训

| 要什么 | method + uri |
|---|---|
| 课程报名记录 | `GET /open-apis/elearning/v2/course_registrations` — query: `page_size`, `user_id_type` |

## 分页

返回里有 `has_more: true` 就带上 `page_token` 再问一次。`page_size` 各端点上限不同
（多数 50，群成员 100），超了会报错而不是截断。

## 报错怎么读

`feishu_api` 会把已知错误码翻成 `hint` 字段 —— 先读它。常见的：

- `99991663` / `99991661`：token 无效或缺失 → 传 `user_key`，或该端点只吃 user token 时加 `prefer="user"`
- `1254302` / `1254303`：没权限 → 需要在应用后台加 scope，或让本人授权
- `230002`：没有该资源权限 → 机器人不在群里/不是文档协作者
- `code="use_dedicated_tool"`：打到了上传端点，按返回的 `tool` 字段换工具
- `code="missing_path_params"`：`uri` 里的 `:name` 没在 `paths_json` 填

权限不足时不要反复重试同一个调用 —— 先用 `feishu_auth_*` 确认授权状态。
