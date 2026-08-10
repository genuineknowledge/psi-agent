---
name: feishu-wiki
description: 飞书知识库（wiki / 知识空间）接口表 —— 全文搜索节点、移动节点（含跨空间）、复制节点、把云文档搬进知识库、读空间详情、改空间设置（谁能建页面/能不能导出/能不能评论）、成员与管理员增删。用 feishu_api 按表调用。列空间、列节点、建文档、建空间仍然是专用工具（它们要处理「成功但返回空」）。
---

# 飞书知识库接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

先分清三个 token，这一域大半的错都是拿错了 token：

| 名字 | 哪来的 | 是什么 |
|---|---|---|
| `space_id` | `feishu_wiki_list_spaces`，或节点详情返回里 | 一个知识空间（知识库）的 id，纯数字串 |
| `node_token` | `feishu.cn/wiki/<这一段>` | 知识库里的**节点**（那一页在目录树上的位置） |
| `obj_token` | `GET /open-apis/wiki/v2/spaces/get_node` 的返回 | 节点背后**真正的文档** id，读正文用它 |

知识库页面是个壳：`/wiki/<node_token>` 打开的内容其实存在一篇 docx / sheet / bitable 里。
所以「读这一页」永远是两步 —— 先 `get_node` 换出 `obj_token` + `obj_type`，再按 `obj_type`
用对应的读法（docx 用 `feishu_doc_read`、bitable 当 `app_token` 用）。那个端点和它的护栏在
**`feishu-api`** 技能里，这里不重复声明，免得两份 rules 抢同一个端点。

**这一域最大的坑：机器人通常不是任何知识空间的成员，飞书这时返回「成功 + 空内容」而不是报没权限。**
所以下面每个读接口拿到空结果都不能当成「没有」—— 带上 `user_key` 用 `prefer="user"`
以那个人的身份再问一次，第二次还空才是真的空。这条对搜索尤其致命：机器人搜知识库
永远搜不到东西，而且不报错。

## 全文搜索

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 在知识库里全文搜 | POST | `/open-apis/wiki/v1/nodes/search` | body `query`、`space_id`、`node_id`；query `page_size`、`page_token` |

**只吃 user token**，必须带 `user_key` + `prefer="user"`。飞书文档明确把「搜索 Wiki」
和「创建知识库」列为 wiki 里唯二不支持 tenant token 的接口，拿机器人身份调必然失败。
搜到的是**那个人有权限看**的内容，所以同一个词不同人搜出来的结果本来就不一样。

`space_id` 留空是搜全部能看到的知识库，给了就只搜那一个；`node_id` 再收窄到某个节点的子树。
先窄后宽通常更快出结果：知道大概在哪个知识库就把 `space_id` 填上。

这个端点是 `wiki/v1`（v2 没有搜索），路径里是 `nodes/search` 而不是 `spaces/...`。
它在开放平台文档站上没有独立页面，但**在官方 SDK 里有**（`lark_oapi` 的
`wiki.v1.node.search`，token 类型写死 USER），本条按 SDK 的签名写。

搜云文档（不含知识库）是另一个接口：工具 `feishu_docs_search`，见 `feishu-api`。
两者搜的范围不重叠 —— 找不到就换另一个试，别在同一个上反复改词。

## 节点：移动、复制、搬进来

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 移动节点（可跨空间） | POST | `/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/move` | `target_parent_token`、`target_space_id` |
| 复制节点 | POST | `/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/copy` | `target_parent_token`、`target_space_id`、`title` |
| 把云文档搬进知识库 | POST | `/open-apis/wiki/v2/spaces/:space_id/nodes/move_docs_to_wiki` | `obj_token`、`obj_type`、`parent_wiki_token`、`apply` |
| 查搬移任务结果 | GET | `/open-apis/wiki/v2/tasks/:task_id` | query `task_type=move` |

移动**带着子节点一起走**，两个 body 字段都可选：只给 `target_parent_token` 是在同一个空间里
换父节点，加上 `target_space_id` 就是跨知识库搬。要移到某个空间的顶层，`target_parent_token`
留空、只给 `target_space_id`。

移动要**三处**编辑权限：节点本身、原来的父容器、目标父容器。缺哪一处都是 131006，
返回里的 `no source parent node permission` / `no destination parent node permission`
直接告诉你缺的是哪一边 —— 照着它去要权限，别改参数重试。

`move_docs_to_wiki` 是**异步**的：返回里回一个 `task_id`，搬移没有当场完成。拿它打
`GET /open-apis/wiki/v2/tasks/:task_id` 且**必须带 `task_type=move`**（这个参数只有这一个值）。
返回 `task.move_result[]` 里 `status` 是 0 成功 / 1 处理中 / -1 失败，失败的 `status_msg`
会说明白（`already in wiki`、`permission denied`、`source not exist`、`tree limit`）。
**只有发起任务的那个身份能查结果** —— 用哪个 `user_key` 发起就用同一个查。

`apply=true` 是「没权限时自动发起申请」，默认 false。搬进知识库的文档**原来的链接会变**，
这件事要先跟用户说：`/docx/<token>` 变成 `/wiki/<node_token>`，别人收藏的旧链接会失效。

**重命名节点在 `feishu-drive`**（`POST .../nodes/:node_token/update_title`，只支持
doc / docx / shortcut 三种节点类型）。那份技能里连着讲了「飞书没有统一改名接口」的另两种情况，
护栏也写在那边。

## 空间：详情与设置

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 读空间详情 | GET | `/open-apis/wiki/v2/spaces/:space_id` | query `lang` |
| 改空间设置 | PUT | `/open-apis/wiki/v2/spaces/:space_id/setting` | `create_setting`、`security_setting`、`comment_setting` |

详情返回 `name`、`description`、`space_type`（`team` 团队空间 / `person` 个人空间）、
`visibility`（`public` 全租户可见 / `private`）。这两个字段决定成员能怎么加（见下节），
所以加人之前先读一次。

三个设置项各只有两个值：

| 字段 | 值 | 管什么 |
|---|---|---|
| `create_setting` | `admin_and_member` / `admin` | 谁能在空间里建顶层页面 |
| `security_setting` | `allow` / `not_allow` | 有阅读权限的人能不能复制/打印/导出 |
| `comment_setting` | `allow` / `not_allow` | 有阅读权限的人能不能评论 |

三个都可选，只传要改的那个。**调用方必须是这个知识空间的管理员** ——
机器人一般不是，所以这个接口基本都要带 `user_key` 以本人身份调，否则 131006。

## 成员与管理员

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 列出成员 | GET | `/open-apis/wiki/v2/spaces/:space_id/members` | `page_size` |
| 加成员/管理员 | POST | `/open-apis/wiki/v2/spaces/:space_id/members` | `member_type`、`member_id`、`member_role`；query `need_notification` |
| 移除成员 | DELETE | `/open-apis/wiki/v2/spaces/:space_id/members/:member_id` | `member_type`、`member_role` |

`member_type` 决定 `member_id` 填什么：`openid`（`ou_...`，最常用）、`userid`、`unionid`、
`email`、`openchat`（群 id，整群加进来）、`opendepartmentid`（部门 id）。
`member_role` 只有 `admin` 和 `member`。`need_notification=true` 会给对方发通知。

**两条按空间类型来的限制**，加人之前先读空间详情：

- `visibility=public` 的空间对全租户可见，所以**不能再加成员**，只能加管理员 —— 加成员返回 131101。
- `space_type=person`（个人空间）反过来：能加成员，但**不能加别的管理员**，应用和机器人也不行。

用 tenant token 时**不能按部门 id 加人**（`opendepartmentid` 不支持），要按部门加就得带
`user_key` 以本人身份调。调用方本身必须是空间管理员（131006），重复添加返回 131008。

移除成员的 `member_id` 在 uri 占位符里，但 `member_type` / `member_role` 还要在 **body** 里
再给一遍 —— 这个接口的签名就是这样，少给会 131002。

## 这些是工具，不是表格行

| 工具 | 为什么必须是工具 |
|---|---|
| `feishu_wiki_list_spaces` | 列知识库。机器人不是成员时飞书**返回成功但 items 是空的**，工具检测到空就自动以 `user_key` 的身份重发一次 —— rules 的 token 回落只在**被拒**时触发，空结果不是被拒，表格表达不了这个重试。 |
| `feishu_wiki_list_nodes` | 列节点，同上：空结果自动换身份重试。 |
| `feishu_wiki_create_doc` | 建节点 + 拼 wiki URL 返回。 |
| `feishu_wiki_create_doc_with_content` | 建节点**并写正文**，两个接口串起来；正文写失败也把 `node_token`/`obj_token` 带回来，免得留下一个找不着的空页面。 |
| `feishu_wiki_create_space` | 建知识库。只吃 user token 且要走本项目的 UAT 存储 + 授权引导（`need_auth` 流程），不是一次转发。 |
| `feishu_api` 打 `wiki/v2/spaces/get_node` | 声明在 `feishu-api` 技能里（那里连着讲空结果要换身份再问），端点只能被一份 skill 声明。 |

```rules
- endpoint: POST /open-apis/wiki/v1/nodes/search
  token: user
  required: [query]
  paginate: {items: items, page_size: 20}
  pitfalls:
    - '只吃 user token: 必须带 user_key 且 prefer="user"。飞书把「搜索 Wiki」和「创建知识库」列为 wiki 里唯二不支持 tenant token 的接口。'
    - 'space_id 留空搜全部可见知识库, 给了只搜那一个; node_id 再收窄到某个子树。'
    - '搜到的是这个人有权限看的内容, 换个人结果就不一样; 空结果先确认换过身份再说「没有」。'
    - '搜云文档(不含知识库)是另一个接口 feishu_docs_search, 两者范围不重叠。'

- endpoint: POST /open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/move
  token: tenant_then_user
  pitfalls:
    - '两个 body 字段都可选: 只给 target_parent_token 是同空间换父节点, 加 target_space_id 才是跨知识库; 移到目标空间顶层就只给 target_space_id。'
    - '子节点跟着一起走。要三处编辑权限(节点本身/原父容器/目标父容器), 缺哪处都是 131006 —— 返回里会写 no source 还是 no destination, 照着要权限别改参数重试。'
    - '131003 是撞了上限: 单层最多 2000 个节点、目录树最深 50 层、一次最多搬 2000 个节点(含子节点)。'

- endpoint: POST /open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/copy
  token: tenant_then_user
  pitfalls:
    - 'title 留空则沿用原标题; target_space_id 留空是复制到同一个知识库。'
    - '复制出来的是新节点新 token, 原节点的评论和历史不跟过来。'

- endpoint: POST /open-apis/wiki/v2/spaces/:space_id/nodes/move_docs_to_wiki
  token: tenant_then_user
  required: [obj_token, obj_type]
  pitfalls:
    - '异步接口: 返回 task_id, 搬移没有当场完成。拿它打 GET /open-apis/wiki/v2/tasks/:task_id 且必须带 task_type=move。'
    - '只有发起任务的身份能查结果 —— 用哪个 user_key 发起就用同一个查。'
    - '搬进来之后链接会变(/docx/<token> → /wiki/<node_token>), 旧链接失效。这件事先告诉用户再动手。'
    - 'apply=true 才会在没权限时自动发起申请, 默认 false。'

- endpoint: GET /open-apis/wiki/v2/tasks/:task_id
  token: tenant_then_user
  required: [query.task_type]
  fields:
    task_type:
      choices: [move]
      in: query
      on_fail: 'task_type 只有 move 一个值(移动云空间文档至知识空间), 且是必填'
  pitfalls:
    - 'move_result[] 里 status: 0 成功 / 1 处理中 / -1 失败; 失败看 status_msg(already in wiki / permission denied / source not exist / tree limit)。'
    - '只有任务创建者查得到(131006)。'

- endpoint: GET /open-apis/wiki/v2/spaces/:space_id
  token: tenant_then_user
  pitfalls:
    - '加成员之前先读这里: space_type(team/person) 和 visibility(public/private) 决定成员能怎么加。'
    - '机器人不是空间成员时可能返回成功但内容是空的, 带 user_key 用 prefer=user 再问一次。'

- endpoint: PUT /open-apis/wiki/v2/spaces/:space_id/setting
  token: user
  fields:
    create_setting:
      choices: [admin_and_member, admin]
      on_fail: 'create_setting 只有 admin_and_member(管理员和成员都能建顶层页面) / admin(仅管理员)'
    security_setting:
      choices: [allow, not_allow]
      on_fail: 'security_setting 只有 allow / not_allow(能否复制、打印、导出)'
    comment_setting:
      choices: [allow, not_allow]
      on_fail: 'comment_setting 只有 allow / not_allow(能否评论)'
  pitfalls:
    - '三个都可选, 只传要改的那个。'
    - '调用方必须是这个知识空间的管理员, 机器人一般不是 —— 带 user_key 以本人身份调, 否则 131006。'

- endpoint: GET /open-apis/wiki/v2/spaces/:space_id/members
  token: tenant_then_user
  paginate: {items: members, page_size: 50}

- endpoint: POST /open-apis/wiki/v2/spaces/:space_id/members
  token: user
  required: [member_type, member_id, member_role]
  fields:
    member_type:
      choices: [openid, userid, unionid, email, openchat, opendepartmentid]
      on_fail: 'member_type 只有 openid/userid/unionid/email/openchat(群)/opendepartmentid(部门) 六种, 它决定 member_id 填什么'
    member_role:
      choices: [admin, member]
      on_fail: 'member_role 只有 admin(管理员) / member(成员)'
  pitfalls:
    - 'visibility=public 的空间不能加成员(对全租户已可见), 只能加管理员, 否则 131101; space_type=person 反过来: 能加成员但不能加别的管理员。加之前先读空间详情。'
    - 'tenant token 不支持按部门 id(opendepartmentid) 加人, 要按部门加就带 user_key 以本人身份调。'
    - '调用方必须是空间管理员(131006); 重复添加返回 131008。'
    - 'need_notification=true 会通知对方, 放 query。'

- endpoint: DELETE /open-apis/wiki/v2/spaces/:space_id/members/:member_id
  token: user
  required: [member_type, member_role]
  pitfalls:
    - 'member_id 在 uri 占位符里, 但 member_type / member_role 还要在 body 里再给一遍, 少给会 131002。'
    - '移除成员不删他建的文档 —— 文档还在知识库里, 只是他不能再访问。'
```
