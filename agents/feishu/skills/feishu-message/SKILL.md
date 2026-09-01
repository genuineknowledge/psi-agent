---
name: feishu-message
description: 飞书消息（message）接口表 —— 撤回、回复、表情回应、消息列表、置顶、转发/合并转发。用 feishu_api 按表调用。发消息/发卡片/发图发文件仍然是专用工具。
---

# 飞书消息接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

这个域和别的域不一样：**发消息本身不在表里**。发送要处理代人带话的隐私改写、`<at>` 转 post、
卡片回调快照、二进制上传，这些拿 JSON 拼不出来，所以留成了专用工具（见文末「这些不走通用接口」）。
表里是**已经发出去的消息**上能做的操作。

消息 id 一律是 `om_` 开头，来自发消息的返回、`<feishu_context>`，或下面的消息列表。
把 `chat_id`（`oc_`）或 `open_id`（`ou_`）当消息 id 传是最常见的错，会被表拦下来。

## 回复与撤回

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 回复某条消息 | POST | `/open-apis/im/v1/messages/:message_id/reply` | `content`、`msg_type`、`reply_in_thread` |
| 撤回消息 | DELETE | `/open-apis/im/v1/messages/:message_id` | 只要路径参数 |

`content` 是**字符串不是对象** —— 一段 JSON 文本，例如 `msg_type=text` 时传
`"{\"text\":\"内容\"}"`。直接把 `{"text": "内容"}` 当对象放进 `content` 会被飞书拒绝。

`reply_in_thread=true` 会把这条回复变成**话题（thread）的开头或续话**，返回里带 `thread_id`
（`omt_` 开头）。这是不可逆的：一条消息一旦起了话题就一直是话题。

撤回只能撤**机器人自己发的**消息（230026），而且有时限（230009，通常 24 小时内）。
撤回后飞书返回空 `data`，所以「成功」这件事只能靠没报错来判断。别拿撤回当「改一下」用 ——
撤回全群可见地消失，改内容应该用编辑（见文末）。

## 表情回应

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 加表情回应 | POST | `/open-apis/im/v1/messages/:message_id/reactions` | `reaction_type.emoji_type` |
| 列出表情回应 | GET | `/open-apis/im/v1/messages/:message_id/reactions` | `reaction_type`、`page_size`（≤50） |

body 的形状是**嵌一层**的：`{"reaction_type": {"emoji_type": "THUMBSUP"}}`，
不是 `{"emoji_type": ...}`。

`emoji_type` 必须是飞书的**键名**，而且大小写不规则、猜不出来：`THUMBSUP`、`OK`、`DONE`
全大写，但 `OnIt`（收到）、`Fire`（火）、`CheckMark`（对勾）、`CrossMark`（叉）是驼峰。
传 `赞`、`👍` 或 `thumbsup` 都会得到 231001。常用对照：

| 想表达 | emoji_type |
|---|---|
| 赞 / 👍 | `THUMBSUP` |
| 好的 / 👌 | `OK` |
| 完成 / ✅ | `DONE` |
| 收到 / 在办 / 处理中 | `OnIt` |
| 感谢 | `THANKS` |
| 鼓掌 / 👏 | `APPLAUSE` |
| 庆祝 / 🎉 | `PARTY` |
| 火 / 🔥 | `Fire` |
| 心 / ❤️ | `HEART` |
| 笑 / 😄 | `SMILE` |
| 加油 | `JIAYI` |
| 对勾 | `CheckMark` |
| 叉 | `CrossMark` |

删表情回应要 `reaction_id`，不是 emoji 名字，所以走专用工具（见文末）。

## 消息列表

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 列群/话题里的消息 | GET | `/open-apis/im/v1/messages` | `container_id_type`、`container_id`、`sort_type` |

`container_id_type` 是 `chat`（读群历史）或 `thread`（读某个话题下的回复），
对应的 `container_id` 分别是 `oc_` 和 `omt_`。两个参数都必填，且必须配套 ——
拿 `oc_` 配 `thread` 不会报错，只会返回空列表，看起来像「群里没消息」。

读出来的每条消息 `body.content` 是 JSON 字符串，要再 parse 一次才拿到文本。
只想要一条话题的纯文本，用 `feishu_thread_read`（见文末）省掉这层。

## 置顶

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 置顶消息 | POST | `/open-apis/im/v1/pins` | body `message_id` |
| 取消置顶 | DELETE | `/open-apis/im/v1/pins/:message_id` | 路径参数 |
| 列出群里的置顶 | GET | `/open-apis/im/v1/pins` | `chat_id`、`start_time`、`end_time`、`page_size`（≤50） |

注意两个形状不一样：**置顶把 `message_id` 放 body**，取消置顶把它放**路径**。

取消置顶对「本来就没置顶」也返回成功，所以调完不能断言「原来有个置顶被取消了」，
只能说「现在没有置顶」。置顶列表只能按群查（`chat_id` 必填，`oc_` 开头），
而且只返回置顶记录（谁在什么时候置顶了哪条），**不含消息内容** ——
要正文得另外按消息 id 读。

## 转发

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 转发一条消息 | POST | `/open-apis/im/v1/messages/:message_id/forward` | query `receive_id_type`、body `receive_id` |
| 合并转发多条 | POST | `/open-apis/im/v1/messages/merge_forward` | query `receive_id_type`、body `receive_id` + `message_id_list` |

`receive_id_type` 在 **query** 里，`receive_id` 在 **body** 里，分开的。
取值按目标前缀选：`oc_`→`chat_id`、`ou_`→`open_id`、`on_`→`union_id`、
邮箱→`email`、`omt_`→`thread_id`。

**转发是唯一能把内容投进话题（`omt_`）的接口** —— 发消息接口不收 `thread_id`。

合并转发要求所有 id 来自**同一个会话**，否则 230069。一次最多 100 条。
被飞书单独拒掉的 id 会出现在返回的 `invalid_message_id_list` 里，不会静默丢 ——
拿到这个字段要如实说哪几条没转过去。

## 这些不走通用接口

| 要做的事 | 用哪个工具 | 为什么 |
|---|---|---|
| 发消息 | `feishu_message_send` | 代人带话必须私发：目标是群时要改投本人 DM，定不出收件人就拒发。还要把 `<at>` 转成 post 才渲染得出真 @ |
| 发卡片 | `feishu_message_send_card` | 卡片按钮的回调要先落一份快照，否则用户点了没人认得这张卡 |
| 编辑消息 / 编辑卡片 | `feishu_message_edit` / `_edit_card` | 文本和卡片是两个不同端点，形状不通用 |
| 删表情回应 | `feishu_message_unreact` | 要按 emoji 反查 `reaction_id`；同一个表情有多人回应时会拒绝而不是乱删一个 |
| 发图/文件/语音/视频 | `feishu_message_send_image` / `_send_file` / `_send_audio` / `_send_video` | multipart 二进制上传，JSON 传不了文件句柄 |
| 单独上传图片/文件 | `feishu_message_upload_image` / `_upload_file` | 同上；拿到 key 才能塞进卡片或富文本 |
| 发富文本 | `feishu_message_send_post` | 要把块按段落分组拼成 post 结构 |
| 起一个话题 | `feishu_topic_start` | 要先构造 post 再起话题 |
| 全局搜消息 | `feishu_message_search` | 只吃 user token，且要补齐发送人和会话名 |
| 读一整个话题的纯文本 | `feishu_thread_read` | 翻页 + 从每条 `content` 里抽纯文本 |
| 下载消息里的图片/文件 | `feishu_image_get` | 二进制落盘，JSON 返不回来 |
| 查谁读了 | `feishu_message_read_status` | 未读要拿群名册减已读名单自己算，飞书不给未读 |

发消息（`POST /open-apis/im/v1/messages`）在 rules 里是**硬拦**的：代人带话的隐私改写只在
工具里，绕过去就可能把私话发进群。

```rules
- endpoint: POST /open-apis/im/v1/messages/:message_id/reply
  token: tenant
  required: [content, msg_type]
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id; chat_id(oc_)/open_id(ou_) 不是消息 id。"}
    msg_type: {in: body, choices: [text, post, image, interactive, share_chat, share_user, audio, media, file, sticker]}
  pitfalls:
    - content 是 JSON 字符串不是对象; text 类型传 "{\"text\":\"内容\"}"。
    - reply_in_thread=true 会把回复变成话题, 不可逆; 返回里的 thread_id 是 omt_ 开头。

- endpoint: GET /open-apis/im/v1/messages/:message_id/reactions
  token: tenant
  paginate: {page_size: 50}
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id。"}
    page_size: {max: 50, in: query}

- endpoint: POST /open-apis/im/v1/messages/:message_id/reactions
  token: tenant
  required: [reaction_type]
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id。"}
  pitfalls:
    - 'body 要嵌一层: {"reaction_type": {"emoji_type": "THUMBSUP"}}。'
    - emoji_type 是飞书键名且大小写不规则: THUMBSUP/OK/DONE 全大写, 但 OnIt/Fire/CheckMark/CrossMark 是驼峰。
    - 传 赞 / 👍 / thumbsup 都会得到 231001; 对照表在这份 skill 的「表情回应」一节。

- endpoint: POST /open-apis/im/v1/messages/:message_id/forward
  token: tenant
  required: [receive_id]
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id。"}
    receive_id_type: {in: query, choices: [open_id, user_id, union_id, email, chat_id, thread_id]}
  pitfalls:
    - receive_id_type 在 query 里, receive_id 在 body 里, 两处分开。
    - 按前缀选类型: oc_→chat_id, ou_→open_id, on_→union_id, 邮箱→email, omt_→thread_id。
    - 这是唯一能把内容投进话题(omt_)的接口, 发消息接口不收 thread_id。

- endpoint: POST /open-apis/im/v1/messages/merge_forward
  token: tenant
  required: [receive_id, message_id_list]
  fields:
    message_id_list: {max_items: 100}
    receive_id_type: {in: query, choices: [open_id, user_id, union_id, email, chat_id, thread_id]}
  pitfalls:
    - 所有 message_id 必须来自同一个会话, 否则 230069。
    - 每条都得是 om_ 开头; 被单独拒掉的 id 在返回的 invalid_message_id_list 里, 要如实报出来。

- endpoint: DELETE /open-apis/im/v1/messages/:message_id/reactions/:reaction_id
  token: tenant
  prefer_tool: feishu_message_unreact
  hard: true
  why: 删表情回应要 reaction_id 而不是 emoji 名字; 工具会按 emoji 反查, 并在同一表情有多人回应时拒绝而不是乱删一个。

- endpoint: DELETE /open-apis/im/v1/messages/:message_id
  token: tenant
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id; chat_id(oc_)/open_id(ou_) 不是消息 id。"}
  pitfalls:
    - 撤回全群可见地消失且本接口无法恢复; 只想改内容用 feishu_message_edit。
    - 只能撤机器人自己发的(230026), 且有时限(230009, 通常 24 小时)。
    - 成功时飞书返回空 data, 没报错就是撤了。

- endpoint: POST /open-apis/im/v1/pins
  token: tenant
  required: [message_id]
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id。"}
  pitfalls:
    - message_id 放 body; 取消置顶时它在路径里, 两个端点形状不一样。
    - 重复置顶不报错, 飞书会把已有的置顶原样返回。

- endpoint: DELETE /open-apis/im/v1/pins/:message_id
  token: tenant
  fields:
    message_id: {pattern: "^om_", on_fail: "message_id 必须是 om_ 开头的消息 id。"}
  pitfalls:
    - 本来没置顶也返回成功, 所以调完只能说「现在没置顶」, 不能断言取消掉了一个。

- endpoint: GET /open-apis/im/v1/pins
  token: tenant
  required: [chat_id]
  paginate: {page_size: 50}
  fields:
    chat_id: {pattern: "^oc_", in: query, on_fail: "chat_id 必须是 oc_ 开头的群 id; 置顶列表只支持按群查。"}
    page_size: {max: 50, in: query}
  pitfalls:
    - 只返回置顶记录(谁何时置顶了哪条), 不含消息正文; 要正文另按消息 id 读。

- endpoint: GET /open-apis/im/v1/messages
  token: tenant
  required: [container_id_type, container_id]
  paginate: {page_size: 50}
  fields:
    container_id_type: {in: query, choices: [chat, thread]}
    sort_type: {default: ByCreateTimeAsc, in: query, choices: [ByCreateTimeAsc, ByCreateTimeDesc]}
    page_size: {max: 50, in: query}
  pitfalls:
    - container_id 要和 container_id_type 配套: chat 配 oc_, thread 配 omt_。配错不报错, 只回空列表。
    - 每条消息的 body.content 是 JSON 字符串, 要再 parse 一次才拿到文本。

- endpoint: POST /open-apis/im/v1/messages
  token: tenant
  prefer_tool: feishu_message_send
  hard: true
  why: 代人带话必须私发本人(目标是群时要改投 DM, 定不出收件人就拒发), 且 <at> 要转成 post 才渲染得出真 @ —— 这两件事只在工具里, 绕过去会把私话发进群。
```
