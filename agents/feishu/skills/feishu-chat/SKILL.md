---
name: feishu-chat
description: 飞书群（chat）接口表 —— 建群/拉人/踢人、群设置、禁言、转让群主、解散群、群菜单、群标签页。用 feishu_api 按表调用。
---

# 飞书群管理接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来，不会等飞书返回一个看起来成功的结果。

先用下面「按名字搜群」那一行（`GET /open-apis/im/v1/chats/search`）拿到 `chat_id`（`oc_...`）再操作。群里绝大多数写操作
只有**群主或管理员**能做，机器人不是 —— 除非群是它建的。被拒是 232017，把群主的 open_id
传 `user_key` 以他的身份调用。

## 群列表与建群

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 按名字搜群（机器人在的群） | GET | `/open-apis/im/v1/chats/search` | `query`、`page_size` |
| 列出机器人在的群 | GET | `/open-apis/im/v1/chats` | `sort_type=ByCreateTimeAsc`、`page_size` |
| 列出「我」在的群 | GET | `/open-apis/im/v1/chats` | 同上，但 `prefer="user"` + 本人 `user_key` |
| 建群并拉人 | POST | `/open-apis/im/v1/chats` | `name`、`user_id_list`、`owner_id`、`description` |

「我在哪些群」和「机器人在哪些群」是**同一个接口**，只有 token 不同。拿机器人的群列表回答
「我在哪些群」是个看起来合理的错答案 —— 问本人的群必须 `prefer="user"` 且带本人 `user_key`，
这样列出来的就是**那个人**所在的群，机器人在不在里面无关。切 token 的参数是 `prefer`，不是
`identity`（后者只在创建有归属的内容时才用）；只传 `identity` 会仍旧走机器人 token，看起来
像「用户的群列不出来」。

翻页固定按创建时间正序（`ByCreateTimeAsc`）：飞书文档说过，按活跃度排序的列表在翻页过程中
顺序会变，会漏群。

## 成员

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 列出群全员 | GET | `/open-apis/im/v1/chats/:chat_id/members` | `member_id_type`、`page_size` |
| 拉人进群 | POST | `/open-apis/im/v1/chats/:chat_id/members` | `id_list`、`member_id_type`、`succeed_type` |
| 移出群成员 | DELETE | `/open-apis/im/v1/chats/:chat_id/members` | `id_list`、`member_id_type` |

拉人/踢人一次最多 50 个用户（机器人 5 个）。`succeed_type` 默认 **1**：能加的都加上、其余单独报告；
0 会因为一个坏 id 整批失败。返回里三类失败要分开看，因为解法不同 —— `invalid_id_list`（不在通讯录
权限范围内、或人已离职）、`not_existed_id_list`（没这个 id）、`pending_approval_id_list`（群主批准后
**会**加进去，别重复加）。

加机器人用 `member_id_type=app_id` 传 App ID。群主**不能**被移出（232076），要先转让群主。

## 群设置

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 改群名/头像/权限 | PUT | `/open-apis/im/v1/chats/:chat_id` | 只传要改的字段 |
| 转让群主 | PUT | `/open-apis/im/v1/chats/:chat_id` | `owner_id` |
| 禁言设置 | PUT | `/open-apis/im/v1/chats/:chat_id/moderation` | `moderation_setting` |
| 解散群 | DELETE | `/open-apis/im/v1/chats/:chat_id` | 不可逆，须本人确认码 + `user_key` |

改群设置只传要改的字段，别整份覆盖 —— 否则改个群名会顺手把「谁可以加人」重置了。
`share_card_permission` 必须和 `add_member_permission` 一致，飞书拒绝不一致的组合。

**解散群必须先向本人确认，绝不能自己决定。** 飞书不保留已解散群的会话记录，群里的消息和文件
全部消失，任何工具都恢复不了。调用时把 `<feishu_context>` 里的 `sender_open_id` 作为 `user_key`
传进去：第一次调用**不会**解散，而是给本人私聊发一个 6 位确认码并返回 `need_confirmation`。
把「要解散哪个群、群里有多少人、后果是什么」讲清楚，等本人把确认码告诉你，再带
`confirm=<那6位数字>` 调一次。确认码只对**这一个** `chat_id` 有效、15 分钟过期、只能用一次，
你自己编不出来 —— 用户没给码就说明这事没被批准，别绕。

只想让群停用而不销毁内容，用移出成员或归档，别用解散。

**禁言是另一个接口**，这是最容易踩的一处：`谁可以发言` 这个值读得到，但在
`PUT /chats/:chat_id` 里改它会被**静默忽略** —— 必须走 `/moderation`。

转让群主用的是改群信息那个接口，传 `owner_id` 即可。新群主必须**已经在群里**（否则 232012）。
转完原群主就拿不回来了。

## 群菜单与标签页

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 读群菜单 | GET | `/open-apis/im/v1/chats/:chat_id/menu_tree` | — |
| 加群菜单 | POST | `/open-apis/im/v1/chats/:chat_id/menu_tree` | `menu_tree.chat_menu_top_levels` |
| 删群菜单 | DELETE | `/open-apis/im/v1/chats/:chat_id/menu_tree` | `chat_menu_top_level_ids` |
| 列群标签页 | GET | `/open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs` | — |
| 加群标签页 | POST | `/open-apis/im/v1/chats/:chat_id/chat_tabs` | `chat_tabs[]` |
| 删群标签页 | DELETE | `/open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs` | `tab_ids` |

加菜单是**追加**不是替换，所以想改现有菜单先读一遍，否则群里会出现两个「帮助」按钮。
带 `children` 的菜单是**分组标题**：它自己不能有 `url` 或 `image_key`，而且事后没法给已有的
顶级菜单加子项 —— 下拉菜单要一次建完。上限是 3 个顶级菜单、每个 5 个子项。删顶级菜单会连子项一起删。

标签页只能建 `doc` 和 `url` 两种。飞书其它标签页类型（pin / 会议纪要 / 任务 / 图片视频…）是内置只读的，
读得到但建不了，所以 `tab_id` 读出来不代表能删。每群上限 20 个自定义标签页。

## 这些不走通用接口

| 要做的事 | 用哪个工具 | 为什么 |
|---|---|---|
| 按名字在群里找人 | `feishu_chat_find_member` | 要的是筛完的结果，不是把整个群名册灌进上下文 |
| 读群详情（含设置） | `feishu_chat_get` | 非成员时飞书返回 200 但只给残缺数据，需要 `partial` 标出来；带 `user_key` 会自动以本人身份重读一次 |
| 读/写/清空群公告 | `feishu_chat_announcement*` | 公告是文档：要读元信息、翻页读块、并把 `revision_id` 串起来做乐观锁 |
| 上传群头像 | `feishu_chat_upload_avatar` | multipart 二进制上传，JSON 传不了文件句柄 |

群公告是**文档不是消息**，所以不会出现在消息历史里。群头像必须用 `image_type="avatar"` 上传，
拿消息图片的 key 去设群头像会得到 232021 —— 看起来像头像有问题，其实是上传方式错了。

「机器人不在这个群里」不等于「这个群看不到」。飞书按**发请求的那个 token**判断成员身份，而机器人
通常不在用户所在的群里 —— 所以带上本人 `user_key`（`feishu_chat_get` 会自动以其身份重读，其他读
接口用 `prefer="user"`）。别把「机器人不在」说成「无法查看」，也别把残缺结果里的 `user_count: 0`
当成群里真的没人。

```rules
- endpoint: GET /open-apis/im/v1/chats/search
  token: tenant
  required: [query]
  paginate: {page_size: 50}
  pitfalls:
    - 只搜得到机器人已经在的群；搜不到不等于群不存在。

- endpoint: GET /open-apis/im/v1/chats
  token: tenant
  paginate: {page_size: 100}
  fields:
    sort_type: {default: ByCreateTimeAsc, in: query, choices: [ByCreateTimeAsc, ByActiveTimeDesc]}
  pitfalls:
    - 问「我在哪些群」要 prefer="user" + 本人 user_key; 用机器人 token 回答是错答案。
    - 切 token 的是 prefer 不是 identity; 只传 identity 仍走机器人 token, 会误以为用户的群列不出来。
    - 以本人身份列群时机器人在不在那些群里无关; 别把「机器人不在」说成「列不出来」。
    - 不返回单聊(p2p)，飞书的会话列表只有群。

- endpoint: POST /open-apis/im/v1/chats
  token: tenant
  required: [name]
  fields:
    user_id_list: {max_items: 50}
    chat_mode: {in: body, choices: [group, topic]}
    set_bot_manager: {default: "true", in: query, choices: ["true", "false"]}
  pitfalls:
    - owner_id 默认应传请求人的 open_id，让提出需求的人当群主，机器人留管理员继续发消息。
    - set_bot_manager 默认 true 是有意的：群主交给请求人后，机器人还得是管理员才发得出消息。
    - 返回里的 invalid_user_ids 是没能拉进来的人(通常在通讯录权限范围外)。

- endpoint: GET /open-apis/im/v1/chats/:chat_id/members
  token: tenant
  paginate: {page_size: 100}
  fields:
    member_id_type: {default: open_id, in: query, choices: [open_id, union_id, user_id]}

- endpoint: POST /open-apis/im/v1/chats/:chat_id/members
  token: tenant
  required: [id_list]
  fields:
    id_list: {max_items: 50}
    succeed_type: {default: 1, in: query, choices: [0, 1, 2]}
    member_id_type: {default: open_id, in: query, choices: [open_id, union_id, user_id, app_id]}
  pitfalls:
    - 多数群只有群主/管理员能加人(232017)，传群主 user_key 以他身份调用。
    - pending_approval_id_list 里的人群主批准后会自动加入，不要重复加。

- endpoint: DELETE /open-apis/im/v1/chats/:chat_id/members
  token: tenant
  required: [id_list]
  fields:
    id_list: {max_items: 50}
    member_id_type: {default: open_id, in: query, choices: [open_id, union_id, user_id, app_id]}
  pitfalls:
    - 群主移不掉(232076)，要先转让群主。任何人都可以移出自己。
    - 移人对全群可见且本接口无法撤销，只能重新拉回来。

- endpoint: PUT /open-apis/im/v1/chats/:chat_id
  token: tenant
  fields:
    add_member_permission: {in: body, choices: [all_members, only_owner]}
    at_all_permission: {in: body, choices: [all_members, only_owner]}
    edit_permission: {in: body, choices: [all_members, only_owner]}
    membership_approval: {in: body, choices: [approval_required, no_approval_required]}
    chat_type: {in: body, choices: [private, public]}
  pitfalls:
    - 只传要改的字段，别整份覆盖。
    - share_card_permission 必须与 add_member_permission 一致，否则飞书拒绝。
    - 「谁可以发言」在这里改会被静默忽略，必须走 /moderation。
    - 传 owner_id 即为转让群主：新群主必须已在群内(232012)，转完拿不回来。
    - avatar 只接受 image_type=avatar 上传的 key，消息图片的 key 会得到 232021。

- endpoint: PUT /open-apis/im/v1/chats/:chat_id/moderation
  token: tenant
  required: [moderation_setting]
  fields:
    moderation_setting: {in: body, choices: [all_members, only_owner, moderator_list]}
  pitfalls:
    - moderator_list 时必须给 moderator_added_list 指定谁可以发言。
    - 同一个 id 不能同时出现在 added 和 removed 两个列表里，飞书会拒绝。
    - 只有群主(或建群的机器人)能改(232017)；群里正在开会时改不了(232092)。

- endpoint: DELETE /open-apis/im/v1/chats/:chat_id
  token: tenant
  confirm: 解散群
  pitfalls:
    - 飞书不保留会话记录，群里的消息和文件全部消失，任何工具都恢复不了。
    - 必须先向本人确认: 传 user_key 后本人会私聊收到 6 位确认码, 带 confirm=<码> 才会执行。
    - 只想停用可以改用移出成员或归档。232009 表示群已经解散过了。

- endpoint: GET /open-apis/im/v1/chats/:chat_id/menu_tree
  token: tenant

- endpoint: POST /open-apis/im/v1/chats/:chat_id/menu_tree
  token: tenant
  pitfalls:
    - 这是追加不是替换；想改现有菜单先读一遍，否则会出现两个同名按钮。
    - 带 children 的菜单是分组标题，自己不能有 url 或 image_key。
    - 子项没法事后加，下拉菜单要一次建完。上限 3 个顶级菜单 x 5 个子项。
    - url 必须 http(s) 开头；image_key 必须是本机器人上传的。

- endpoint: DELETE /open-apis/im/v1/chats/:chat_id/menu_tree
  token: tenant
  required: [chat_menu_top_level_ids]
  pitfalls:
    - 按 id 删而不是按名字，两个菜单可能同名，删错立刻全群可见。
    - 删顶级菜单会连它的子项一起删。

- endpoint: GET /open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs
  token: tenant
  pitfalls:
    - 内置标签页(pin / 会议纪要 / 任务 / 图片视频…)读得到但删不掉，只有 doc 和 url 能删。

- endpoint: POST /open-apis/im/v1/chats/:chat_id/chat_tabs
  token: tenant
  required: [chat_tabs]
  pitfalls:
    - tab_type 只能是 doc 或 url，其余类型是飞书内置只读的。
    - tab_name 最长 60 字；tab_content 必须 http(s) 开头。
    - doc 类型机器人需要有该文档的权限，否则 232051。每群上限 20 个自定义标签页。

- endpoint: DELETE /open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs
  token: tenant
  required: [tab_ids]
  pitfalls:
    - 内置标签页会被飞书拒绝，而不是假装删掉了。
```
