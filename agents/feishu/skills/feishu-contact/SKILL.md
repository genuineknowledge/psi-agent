---
name: feishu-contact
description: "飞书通讯录与组织架构 —— 查人(open_id/手机/邮箱)、查部门成员、部门树、用户组、角色、关联组织，以及建改用户/部门/用户组与办离职。Use when the user asks 查某人信息/某人手机号邮箱/查部门有谁/部门树/组织架构/建用户/改部门/办离职/用户组. 端点表与校验规则都在本文档，通过 feishu_api 调用。"
category: integration
---

# 飞书通讯录 / 组织架构

所有端点通过 `feishu_api` 调用。本文档既是给你看的端点表，也是 `feishu_api` 执行时
**真正会跑的校验规则**（见每节末尾的 `rules` 块）—— 违反约束的调用在请求发出前就被退回，
不会变成一次静默的错误写入。

回复用中文，除非用户明显在用其他语言。

## 怎么调

```
feishu_api(
  method="GET",
  uri="/open-apis/contact/v3/users/:user_id",
  paths_json='{"user_id":"ou_abc"}',
  user_key="<sender_open_id>",
)
```

`uri` **保留 `:name` 占位符**，值放 `paths_json`，别自己拼进去。
`user_key` 一律传 `<feishu_context>` 里的 `sender_open_id`。

`user_id_type` 不传时飞书默认可能不是 open_id，查人一律显式写上 —— 这条已经做成默认值，
不填会自动补 `open_id`。

## 查人

| 要什么 | method + uri | 说明 |
|---|---|---|
| 查一个人 | `GET /open-apis/contact/v3/users/:user_id` | 拿手机/邮箱/部门/上级 |
| 批量查人 | `GET /open-apis/contact/v3/users/batch` | `query: user_ids` 重复同名 key 传多个，一次 ≤ 50 |
| 按手机/邮箱查 | `POST /open-apis/contact/v3/users/batch_get_id` | **是 POST 不是 GET**；body `{"mobiles":[...]}` 或 `{"emails":[...]}` |
| 按名字全局搜 | `GET /open-apis/search/v1/user` | **只支持 user token**，必须带 `user_key` |
| 查部门成员 | `GET /open-apis/contact/v3/users/find_by_department` | `query: department_id, page_size(≤50), page_token` |
| 人员类型枚举 | `GET /open-apis/contact/v3/employee_type_enums` | 建用户的 `employee_type` 自定义枚举号从这儿查 |

按手机/邮箱查有两个坑：**企业邮箱一律查不到**（只能用个人邮箱），
**离职的人默认静默漏掉**。查不到人先确认是不是这两种情况，别直接回「查无此人」。

```rules
- endpoint: GET /open-apis/contact/v3/users/batch
  fields:
    user_ids: {max_items: 50, on_fail: "user_ids 一次最多 50 个，超了要分批"}
    user_id_type: {default: open_id}
- endpoint: POST /open-apis/contact/v3/users/batch_get_id
  pitfalls:
    - 企业邮箱查不到，只能用个人邮箱
    - 离职成员默认静默漏掉，查不到不等于没这个人
  fields:
    user_id_type: {default: open_id}
- endpoint: GET /open-apis/search/v1/user
  token: user
  required: [query]
  pitfalls:
    - 只吃 user token，没有 user_key 就没有可用的身份
- endpoint: GET /open-apis/contact/v3/users/find_by_department
  required: [department_id]
  paginate: {page_size: 50}
  fields:
    page_size: {max: 50, on_fail: "page_size 上限 50，写大了飞书会报错"}
    user_id_type: {default: open_id}
- endpoint: GET /open-apis/contact/v3/users/:user_id
  fields:
    user_id_type: {default: open_id}
```

## 部门

| 要什么 | method + uri | 说明 |
|---|---|---|
| 部门详情 | `GET /open-apis/contact/v3/departments/:department_id` | 根部门 id 是 `0` |
| 子部门列表 | `GET /open-apis/contact/v3/departments/:department_id/children` | `query: page_size(≤50)` |
| 批量查部门 | `GET /open-apis/contact/v3/departments/batch` | `query: department_ids` 重复同名 key，一次 ≤ 50 |
| 父部门链 | `GET /open-apis/contact/v3/departments/parent` | `query: department_id` 必填；返回**子→父**顺序且不含根部门 |
| 搜索部门 | `POST /open-apis/contact/v3/departments/search` | **只吃 user token**；只匹配中文名，不匹配国际化名 |

用 tenant token 查根部门 `0` 的子部门要求应用的通讯录权限范围 = 全部成员，
否则**返回空而不报错**。拿到空列表先怀疑这个，别报告「该部门没有子部门」。

```rules
- endpoint: GET /open-apis/contact/v3/departments/batch
  fields:
    department_ids: {max_items: 50, on_fail: "department_ids 一次最多 50 个"}
- endpoint: GET /open-apis/contact/v3/departments/parent
  required: [department_id]
  fields:
    page_size: {max: 50}
  pitfalls:
    - 返回是子→父顺序，且不含根部门
- endpoint: POST /open-apis/contact/v3/departments/search
  token: user
  required: [query]
  pitfalls:
    - 只匹配中文名，不匹配国际化名
- endpoint: GET /open-apis/contact/v3/departments/:department_id/children
  paginate: {page_size: 50}
  fields:
    page_size: {max: 50}
  pitfalls:
    - tenant token 查根部门 0 的子部门，权限范围不足时返回空而不报错
```

## 用户组与角色

| 要什么 | method + uri | 说明 |
|---|---|---|
| 用户组详情 | `GET /open-apis/contact/v3/group/:group_id` | 路径是**单数 `group`**，没有 `/groups` |
| 用户组列表 | `GET /open-apis/contact/v3/group/simplelist` | `query: type` 1 普通 2 动态 |
| 组内成员 | `GET /open-apis/contact/v3/group/:group_id/member/simplelist` | |
| 查角色成员 | `GET /open-apis/contact/v3/functional_roles/:role_id/members` | `page_size ≤ 100` |
| 建角色 | `POST /open-apis/contact/v3/functional_roles` | 返回 `role_id`，**记下来** |

**飞书没有「列出所有角色」的接口。** `role_id` 只能从建角色的返回值拿，或让用户去
管理后台「组织架构 > 角色管理」抄。别去猜一个 `/functional_roles` 的 GET，那个端点不存在。

```rules
- endpoint: GET /open-apis/contact/v3/group/simplelist
  paginate: {items: grouplist, page_size: 100}
  fields:
    page_size: {max: 100}
    type: {choices: [1, 2]}
- endpoint: GET /open-apis/contact/v3/group/:group_id/member/simplelist
  paginate: {items: memberlist, page_size: 100}
  fields:
    page_size: {max: 100}
    member_type: {choices: [user, department]}
- endpoint: GET /open-apis/contact/v3/functional_roles/:role_id/members
  paginate: {items: members, page_size: 100}
  fields:
    page_size: {max: 100}
- endpoint: POST /open-apis/contact/v3/functional_roles
  required: [role_name]
  pitfalls:
    - role_name 租户内唯一；返回的 role_id 要记下来，没有列表接口可以再查
```

## 写操作

建/改用户、办离职、建/改/删部门、用户组增删，都用 `feishu_api` 打下面的端点。
**这批写端点只吃 tenant token**（scope `contact:contact` / `contact:group` /
`contact:functional_role`），让用户授权也没用。

| 要什么 | method + uri | 不可逆? |
|---|---|---|
| 建用户 | `POST /open-apis/contact/v3/users` | |
| 改用户 | `PATCH /open-apis/contact/v3/users/:user_id` | |
| 办离职 | `DELETE /open-apis/contact/v3/users/:user_id` | **不可逆** |
| 恢复离职 | `POST /open-apis/contact/v3/users/:user_id/resurrect` | 办错离职的回退路径 |
| 建部门 | `POST /open-apis/contact/v3/departments` | |
| 改部门 | `PATCH /open-apis/contact/v3/departments/:department_id` | |
| 删部门 | `DELETE /open-apis/contact/v3/departments/:department_id` | **不可逆** |
| 建用户组 | `POST /open-apis/contact/v3/group` | |
| 删用户组 | `DELETE /open-apis/contact/v3/group/:group_id` | **不可逆** |
| 增删组成员 | `POST /open-apis/contact/v3/group/:group_id/member/add` / `remove` | 一次只收一个成员 |

**离职、删部门、删用户组这三个必须先向用户确认再调。** 它们不可逆：
离职且无上级时该人的日历/问卷被直接删除；删部门要求部门先清空
（有人 43011 / 有子部门 43012，只能最深层往上删）；删用户组会让引用它的
文档权限和审批流失去主体。

确认走的是**验证码**，不是你自己声明一句就算：调用时把 `<feishu_context>` 里的
`sender_open_id` 作为 `user_key` 传进去，第一次调用不会执行，而是给本人私聊发一个
6 位码并返回 `need_confirmation`；讲清后果、等本人把码告诉你，再带 `confirm=<那6位数字>`
调一次。码只对这一个目标有效、15 分钟过期、只能用一次。

改用户时**没传的字段不能变成清空** —— 只传要改的那几个字段。

增删组成员飞书**一次只收一个**，多人要循环调用并逐人回报成败；
三个 `member_*` 参数不一致会 41072。

写操作失败最常见的真因不是参数写错：`40004` / `41050` / `42009` 是应用的
**通讯录权限范围**没覆盖到目标部门/用户，这是开发者后台配的，改代码没用。
`42010` 是建用户组硬要求范围 = 全部成员（只有这个动作要求）。

```rules
- endpoint: POST /open-apis/contact/v3/users
  token: tenant
  required: [name, mobile, department_ids]
  fields:
    employee_type: {default: 1, in: body, choices: [1, 2, 3, 4, 5]}
    department_ids: {max_items: 50}
  pitfalls:
    - 40004/41050/42009 = 应用通讯录权限范围没覆盖目标部门，改代码没用
    - mobile 租户内唯一；中国大陆外要带 + 和国家码(如 +8190...)
    - employee_type 1 正式 2 实习 3 外包 4 劳务 5 顾问；自定义人员类型传后台配的枚举号
- endpoint: DELETE /open-apis/contact/v3/users/:user_id
  token: tenant
  confirm: 离职用户
  fields:
    email_processing_type: {choices: ["1", "2", "3"]}
    email_acceptor_user_id: {in: query}
  pitfalls:
    - 离职不可逆；无上级时该人的日历/问卷被直接删除
    - email_processing_type='1'(转移) 时必须同时给 email_acceptor_user_id
- endpoint: POST /open-apis/contact/v3/users/:user_id/resurrect
  token: tenant
  pitfalls:
    - 办错离职的唯一回退路径
- endpoint: PATCH /open-apis/contact/v3/users/:user_id
  token: tenant
  pitfalls:
    - 没传的字段不能变成清空，只传要改的那几个
- endpoint: POST /open-apis/contact/v3/departments
  token: tenant
  required: [name, parent_department_id]
  fields:
    name: {forbid: "/", on_fail: "部门名不能含斜杠 '/'，飞书会报 43029"}
    custom_department_id: {forbid: "^(od-|0$|1$)", on_fail: "custom_department_id 不能以 'od-' 开头，也不能是 '0' 或 '1'(飞书保留)"}
  pitfalls:
    - 建在组织根下就传 parent_department_id='0'
- endpoint: PATCH /open-apis/contact/v3/departments/:department_id
  token: tenant
  fields:
    name: {forbid: "/", on_fail: "部门名不能含斜杠 '/'，飞书会报 43029"}
  pitfalls:
    - 只传要改的字段；换 parent_department_id 就是移动整棵子树
    - 根部门 '0' 不能修改，飞书返回 40002
- endpoint: DELETE /open-apis/contact/v3/departments/:department_id
  token: tenant
  confirm: 删除部门
  pitfalls:
    - 不可逆，且要求部门先清空(有人 43011 / 有子部门 43012)，只能最深层往上删
    - 根部门 '0' 删不了，飞书返回 40002
- endpoint: POST /open-apis/contact/v3/group
  token: tenant
  required: [name]
  fields:
    # pattern 而不是 max: max 用 float() 转换值, 对字符串一律放过, 长度根本没被拦。
    name: {pattern: '^[\s\S]{1,100}$', on_fail: 用户组名最长 100 字符}
  pitfalls:
    - 42010 = 建用户组硬要求通讯录范围为全部成员(只有这个动作要求)
    - name 租户内唯一
- endpoint: PATCH /open-apis/contact/v3/group/:group_id
  token: tenant
  pitfalls:
    - name 和 description 至少给一个，否则这一趟什么也没改
- endpoint: DELETE /open-apis/contact/v3/group/:group_id
  token: tenant
  confirm: 删除用户组
  pitfalls:
    - 不可逆；引用该组的文档权限/审批流会失去主体
- endpoint: POST /open-apis/contact/v3/group/:group_id/member/add
  token: tenant
  fields:
    member_type: {choices: [user]}
  pitfalls:
    - 飞书一次只收一个成员，多人要循环并逐人回报成败
    - 三个 member_* 参数不一致会 41072
    - 增删组成员目前只支持 member_type='user'，部门做主体不支持
- endpoint: POST /open-apis/contact/v3/group/:group_id/member/remove
  token: tenant
  fields:
    member_type: {choices: [user]}
  pitfalls:
    - 一次只收一个成员；只支持 member_type='user'
```

## 关联组织（外部联系人）

飞书 `contact/v3` 里**没有 `external_user` 端点**。组织级的「外部联系人」是
**关联组织**（trust_party），scope `trust_party:collaboration.tenant:readonly`：

| 要什么 | method + uri |
|---|---|
| 可见关联组织列表 | `GET /open-apis/trust_party/v1/collaboration_tenants` — `page_size 1-100` |
| 关联组织详情 | `GET /open-apis/trust_party/v1/collaboration_tenants/:tenant_key` |
| 对方可见部门/成员 | `GET /open-apis/trust_party/v1/collaboration_tenants/:tenant_key/visible_organization` |

若用户说的「外部联系人」其实是外部群里的人，那是群成员列表（`GET /open-apis/im/v1/chats/:chat_id/members`，见 `feishu-chat` skill），不是这套。

```rules
- endpoint: GET /open-apis/trust_party/v1/collaboration_tenants
  fields:
    page_size: {max: 100, min: 1, on_fail: "page_size 取值 1-100，越界会 1970011"}
```
