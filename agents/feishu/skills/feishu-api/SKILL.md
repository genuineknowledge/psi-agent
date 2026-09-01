---
name: feishu-api
description: "Calling any Feishu/Lark Open Platform endpoint through the generic feishu_api tool — 通讯录/组织架构查人、考勤组与班次配置、培训报名记录、云文档全局搜索、审批实例与任务查询、日历日程查询、任务(Task)增删改查、群信息与成员、知识库空间与节点。云盘文件管理看 feishu-drive、电子表格看 feishu-sheet。Use when a Feishu capability has no dedicated feishu_* tool, or when the user asks 查某人信息/查部门成员/查考勤配置/搜文档/查审批状态/查日程/管任务/查群成员/查知识库. Carries the endpoint tables, the token strategy, and the rule for when a dedicated tool must be used instead."
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
| 搜索消息 | `feishu_message_search` | 只吃 user token，且**只返回 message_id**，必须回查才有正文 |
| 增删用户组成员 | `feishu_user_group_members` | 飞书**一次只收一个成员**，工具循环并逐人回报成败；三个 member_* 参数不一致就 41072 |
| 按手机号/邮箱查人 | `feishu_contact_find` | 是 **POST** 不是 GET；企业邮箱一律查不到；离职的人默认**静默漏掉** |
| 部门树 / 部门详情 | `feishu_department_tree` / `feishu_department_get` | 递归+分页+去重，且 43010「部门过大」必须暴露出来而不是静默少一层 |

判断方法：先用 `tool_search` 找一下有没有 `feishu_` 开头的对应工具；有就用它。

有些域已经**从工具改成了接口表**：这些端点仍然走 `feishu_api`，但要先读对应的 skill，
因为不可逆操作的 `confirm` 闸门和静默失败的约束都写在那份 rules 里，发请求前会硬拦。

不可逆操作的闸门**不是一句口令**：带 `user_key` 调用后，本人会私聊收到一个 6 位确认码，
你拿不到也编不出来，必须由本人转告，再带 `confirm=<那6位数字>` 重调一次。码只对该目标有效、
15 分钟过期、只能用一次。用户没给码就等于没批准 —— 这时该做的是把后果讲清楚并等他回话。

| 域 | 读哪个 skill | 里面有什么闸门 |
|---|---|---|
| 群管理（建群/拉人/踢人/群设置/禁言/转让群主/解散群/群菜单/群标签页） | `feishu-chat` | 解散群要本人确认码；禁言不在群设置那个 body 里 |
| 通讯录（查人/搜人/建改用户/部门增删改/用户组） | `feishu-contact` | 离职、删部门、删用户组都要本人确认码 |
| 消息（撤回/表情回应/置顶/转发/消息列表） | `feishu-message` | 撤回时限、置顶权限、合并转发必须同源 |
| 多维表格（表/字段/记录） | `feishu-bitable` | 列名对不上会被静默丢弃，所以字段先校验再写 |
| 审批（读定义/查待办/列实例/同意拒绝/订阅） | `feishu-approval` | 同意拒绝要凑齐身份四元组；两张数字状态表在那份技能里 |
| 云盘与文档（元数据/建文件夹/列目录/复制移动改名/删文件/异步任务/评论） | `feishu-drive` | 删文件的 `type` 只认九个值；复制不支持文件夹而移动支持；改名没有统一接口；回收站无公开 API；上传下载导出是专用工具 |
| 电子表格（建表/改名/工作表增删/行列增删插/合并/查找替换） | `feishu-sheet` | 插行列 0-based 半开 vs 删行列 1-based 全闭，索引基准相反；读写单元格仍是专用工具 |
| 考勤（考勤组配置/班次配置） | `feishu-attendance` | `page_size` 上限 50 会硬拦；打卡记录是专用工具 |
| 云文档协作者权限（加人/列人/移人） | `feishu-permission` | 三个不同的东西都叫 `type`；`member_type` 是 `openid` 不是 `open_id` |
| 任务与培训（建改任务/完成/列任务/课程报名） | `feishu-task` | 空 `update_fields` 会被硬拦（飞书返回成功但一个字段都不改） |

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

用户组的增删改按 `feishu-contact` 里的接口表走（删组有 `confirm` 闸门），
成员增删用 `feishu_user_group_members`（飞书一次只收一个成员，工具循环并逐人回报）。
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
若用户说的「外部联系人」其实是外部群里的人，那是 `feishu-chat` 里的群成员列表接口，不是这套。

#### 通讯录写操作为什么老是失败

这一批写端点**只吃 tenant token**（scope `contact:contact` / `contact:group` /
`contact:functional_role`），让用户授权也没用。而失败最常见的真因不是参数写错：

- `40004` / `41050` / `42009` —— 应用的**通讯录权限范围**没覆盖到目标部门/用户/用户组。
  这是开发者后台配的，改代码没用。
- `42010` —— 建用户组硬要求范围 = **全部成员**（只有这个动作要求）。
- 用 tenant token 查根部门 `0` 的子部门同样要求范围 = 全部成员，否则**返回空而不报错**。

### 考勤

考勤组和班次的配置**看 `feishu-attendance` 那份接口表**，那里的规则会在发请求之前拦下
超出上限的 `page_size` 和不认的 id 类型。打卡记录用专用工具 `feishu_attendance_query`
（它把两层嵌套的打卡数组摊平成一人一天一行，并单独给出查不到的人）。

日期是 **整数** `YYYYMMDD`，不是字符串。`user_ids` 要的是 employee_id 体系，跟 open_id 不同。
以上都是只读，但除了 scope 之外**还要在考勤管理后台单独授一次数据权限范围**，否则回
1220004 / 1220005 —— 那不是参数错，别改参数重试。

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

**看 `feishu-calendar` 那份接口表**（建/改/删日程、日程详情、日程列表与搜索、重复日程实例、
参与人增删与 RSVP、日历本身的建删改与列表/订阅、忙闲查询都在里面）。挪过去是因为这个域
有三处「照着别处写就会错」：**日程时间是秒级**而任务的 `due` 是毫秒；`timestamp` 与 `date`
互斥；参与人对象**按 type 换 id 键名**（`user`→`user_id`、`chat`→`chat_id`、
`resource`→`room_id`），写成统一的 `id` 会被拒成 194004。删日程和删共享日历都不可恢复，
后者带 `confirm` 闸门。

### 任务 (Task v2)

任务的建/改/完成/列表**看 `feishu-task` 那份接口表**。挪过去是因为这个域的失败方式是
静默的：`PATCH` 靠 `update_fields` 决定改什么，空数组时飞书返回成功却一个字段都不改，
那份 rules 会在发请求之前拦下来。时间戳是**毫秒字符串**（写成秒会落到 1970 年），
成员对象里的 `type` 填 `open_id` 会报 1470400 —— 这些也都在那份技能里。

### 群 / 知识库

| 要什么 | method + uri |
|---|---|
| 搜我在的群 | `GET /open-apis/im/v1/chats/search` — query: `query`, `page_size` |
| 群成员 | `GET /open-apis/im/v1/chats/:chat_id/members` — query: `page_size`(≤100), `page_token` |
| 知识空间列表 | `GET /open-apis/wiki/v2/spaces` — query: `page_size` |
| 空间节点 | `GET /open-apis/wiki/v2/spaces/:space_id/nodes` — query: `parent_node_token`, `page_size` |
| 节点详情 | `GET /open-apis/wiki/v2/spaces/get_node` — query: `token`（wiki node_token） |

wiki 节点的 `obj_token` 才是文档 id，读内容要用它而不是 `node_token`。
建 wiki 文档用 `feishu_wiki_create_doc*`。

**知识库读的空结果不代表没有**：机器人通常不是任何知识库的成员，这时飞书**返回成功但内容是空的**
（不是报没权限）。所以上面三个 wiki 读端点拿到空 `items` / 空 `node` 时，别当成「没有知识库」
或「节点不存在」—— 带上 `user_key` 用 `prefer="user"` 再问一次，以那个人的身份看。第二次
还是空，才是真的空。

### 电子表格

**看 `feishu-sheet` 那份接口表**（建表、改名、列工作表、工作表增删复制、行列增删插、合并拆分、
查找替换、查保护范围都在里面）。挪过去是因为这个域有一处**索引基准相反**的坑：插行列是 0-based
半开、删行列是 1-based 全闭，照抄另一个端点会多删一行或插错位置，而两边都返回成功。
读区间、写入、套格式仍是专用工具（`feishu_sheet_read` / `_write` / `_append` / `_format`），
裸 `!A1` 会静默丢数据；写入是 **PUT** 不是 POST。

**群的运营看 `feishu-chat` 那份接口表**（建群拉人、群列表、群设置、禁言、转让群主、
解散群、群菜单、群标签页都在里面）。这些端点各自都有一个「照着文档写也会错」的地方，
所以护栏在那份 rules 里 —— 禁言不在群设置那个 body 里、加人权限和群名片权限必须成对、
解散群要 `confirm="解散群"`。群公告是唯一还留着工具的（`feishu_chat_announcement`/`_set`/
`_clear`），因为公告是 docx 文档且要按 revision 乐观锁。

### 培训

课程报名记录跟着任务一起搬走了，**看 `feishu-task` 那份接口表**。`user_ids` 是
**重复同名 key** 的查询参数，逗号拼成一个串会得到一页空结果而不是报错。

## 一条有护栏的端点

上面绝大多数端点是纯转发，填错了飞书会报错。这条不是 —— 它的错法是静默的
（返回成功但内容是空的），所以写成可执行的 `rules`，发请求之前就把这件事讲在眼前。
表格那条 `sheets/query` 的护栏跟着接口表搬进了 `feishu-sheet`。

```rules
- endpoint: GET /open-apis/wiki/v2/spaces/get_node
  token: tenant_then_user
  required: [query.token]
  pitfalls:
    - token 是网址 /wiki/ 后面那段 node token, 放 query 而不是 uri 占位符(这个端点没有占位符)。
    - obj_token 才是文档 id, 读正文用它;obj_type 是 docx/sheet/bitable 等, 决定后面用哪个读法。
    - 机器人不是知识库成员时飞书返回成功但 data.node 是空的, 不是报错。空了要带 user_key 用 prefer=user 再问一次, 别当成节点不存在。
```

`token: tenant_then_user` 只在**被拒**时回落到用户身份，而空结果不是被拒 —— 所以那个
空结果重试要你自己发起，rules 只负责把这件事讲在你眼前。

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
