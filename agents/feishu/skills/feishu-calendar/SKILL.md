---
name: feishu-calendar
description: 飞书日历（calendar v4）接口表 —— 建/改/删日程、读日程详情、日程列表与搜索、重复日程实例、参与人增删与 RSVP 回复、日历本身的建删改与列表/搜索/订阅、忙闲（free/busy）查询。用 feishu_api 按表调用，含「一人一场」批量建日程的做法。
---

# 飞书日历接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

本域先分清**两层对象**，混起来是问不出东西也写不进去的头号原因：

- **日历（calendar）**是容器：每个人有一个 `primary` 主日历，另可有若干 `shared` 共享日历、
  `resource` 会议室日历。`calendar_id` 标识它。
- **日程（event）**住在某个日历里，`event_id` 标识它。**任何日程操作都要同时给
  `calendar_id` 和 `event_id`** —— 光有 event_id 打不出任何一个端点。

## 先拿到 calendar_id

机器人自己的主日历要**先建后用**：`POST /calendars/primary` 不是「查询」而是
「获取或创建」，返回 `calendars[].calendar.calendar_id`。这一步几乎是所有写操作的第 0 步。

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 拿主日历 id | POST | `/open-apis/calendar/v4/calendars/primary` | query `user_id_type` |
| 列出这个身份的日历 | GET | `/open-apis/calendar/v4/calendars` | query `page_size`(50-1000)、`page_token`、`sync_token` |
| 读一个日历 | GET | `/open-apis/calendar/v4/calendars/:calendar_id` | 无 |
| 搜日历 | POST | `/open-apis/calendar/v4/calendars/search` | body `query`；query `page_size` |
| 建共享日历 | POST | `/open-apis/calendar/v4/calendars` | body `summary`、`description`、`permissions`、`color` |
| 改日历 | PATCH | `/open-apis/calendar/v4/calendars/:calendar_id` | body 同上，只传要改的 |
| 删共享日历 | DELETE | `/open-apis/calendar/v4/calendars/:calendar_id` | 需 owner；**不可恢复** |
| 订阅 / 退订日历 | POST | `/open-apis/calendar/v4/calendars/:calendar_id/subscribe` / `/unsubscribe` | 无 |

**用 `prefer="user"` + `user_key` 才是「那个人的」主日历**；默认 tenant 身份拿到的是
机器人自己的。这是「我的日程怎么一条都没有」最常见的原因 —— 读到的是机器人的空日历，
不是那个人的日历，而且接口**返回成功**。

日历列表返回的键是 **`calendar_list`**，不是 `items`。每条带 `role`
（owner/writer/reader/free_busy_reader）—— 决定你能不能往里写，读不到就先看这个字段。
`type` 为 `primary`/`shared` 之外的（`google`/`exchange`/`resource`）多半写不进去，
`is_third_party` 为真的只读。共享日历删了不可恢复，所以那条有 `confirm` 闸门。

## 日程：增删改查

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 建日程 | POST | `/open-apis/calendar/v4/calendars/:calendar_id/events` | body `start_time`、`end_time`(必填)、`summary`、`description`、`recurrence`、`reminders`、`visibility`、`attendee_ability`、`free_busy_status`、`location`；query `idempotency_key` |
| 读日程详情 | GET | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id` | query `need_attendee`、`max_attendee_num`、`need_meeting_settings` |
| 改日程 | PATCH | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id` | body 只传要改的字段 |
| 删日程 | DELETE | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id` | query `need_notification` |
| 日程列表（区间） | GET | `/open-apis/calendar/v4/calendars/:calendar_id/events` | query `start_time`、`end_time`(**秒级**)、`page_size`(50-1000) |
| 日程列表（增量） | GET | 同上 | query `anchor_time` / `page_token` / `sync_token` |
| 搜日程 | POST | `/open-apis/calendar/v4/calendars/:calendar_id/events/search` | body `query`、`filter`；query `page_size`(≤100) |
| 重复日程的实例 | GET | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/instances` | query `start_time`、`end_time`、`page_size` |
| 某区间的日程视图 | GET | `/open-apis/calendar/v4/calendars/:calendar_id/events/instance_view` | query `start_time`、`end_time` |

### 时间是**秒**级，而且 timestamp 与 date 互斥

`start_time` / `end_time` 是对象，不是字符串：

```
定时日程：{"timestamp": "1786341600", "timezone": "Asia/Shanghai"}
全天日程：{"date": "2026-08-10",       "timezone": "Asia/Shanghai"}
```

- **秒不是毫秒**。这跟飞书任务（`due.timestamp` 是**毫秒**）正好相反 —— 同一个会话里
  一边建任务一边建日程时最容易串味。多三个零会落到公元 58000 年，少三个零落到 1970 年。
- `timestamp` 和 `date` **不能同时给**。全天日程只给 `date`，全天日程的时区固定按 UTC+0 处理。
- **当前时间在每轮对话的上下文里给了，据此换算**，不要凭印象编一个时间戳。
- `timezone` 是**给飞书用来解释这个时间的**，它不会替你做时区换算 —— 你算 `timestamp`
  的时候必须自己按目标时区算。这一点以前的 `feishu_calendar_create_event` 工具做错了：
  它拿 `datetime.strptime(...).timestamp()`（跟着**服务器机器**的本地时区），所以传
  `timezone="America/New_York"` 得到的时间戳跟传 `Asia/Shanghai` 一模一样，
  给纽约的人建的会议会差 12 小时，而且返回成功。现在算时间戳是你的事，请显式按目标时区算。

区间查询用秒级 `start_time` + `end_time`；**增量同步用 `anchor_time`/`page_token`/`sync_token`，
两条路互斥** —— 混着传会报 190009 或者**丢日程数据**。日程列表返回键是 `items`，`page_size`
下限是 **50**（不是 1），上限 1000。

### 改日程：省略即不改，但时间必须成对

日程 PATCH **没有 `update_fields`**（那是任务域的东西，别混）。语义是「不传的字段就不改」。
两个例外：

- `start_time` 与 `end_time` **必须同时传才生效**，只传一个等于没改时间。
- `schemas` 是整体覆盖，传 `[]` 就是清空。

改 `visibility` / `free_busy_status` / `color` / `reminders` **只对当前身份生效**，不影响别人看到的样子。
组织者能改全部字段，普通参与人只能改这四个。**日程组织者**才能删日程 —— 不是组织者会撞 193002。

### 重复日程的 event_id 带后缀

`recurrence` 用 RFC 5545 规则串，如 `FREQ=WEEKLY;INTERVAL=1;BYDAY=MO`。`COUNT` 和 `UNTIL`
不能同时出现。重复日程的 `event_id` 形如 `<uid>_<original_time>`，普通日程后缀是 `_0`；
要改/删**某一次**而不是整条重复规则，得先用 `/instances` 列出实例拿到带时间戳后缀的那个 id。
`is_exception` 为真表示这是重复日程的一个例外实例，`recurring_event_id` 指回原重复日程。

## 参与人与 RSVP

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 加参与人 | POST | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees` | body `attendees`、`need_notification` |
| 列参与人 | GET | 同上 | query `page_size`、`page_token` |
| 删参与人 | POST | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees/batch_delete` | body `attendee_ids` 或 `delete_ids` |
| 本人回复日程 | POST | `/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/reply` | body `rsvp_status` |

参与人对象**按 type 换 id 字段名**，这是本域第二个高频错：

```
{"type": "user",        "user_id": "ou_xxx"}          # 人
{"type": "chat",        "chat_id": "oc_xxx"}          # 整个群
{"type": "resource",    "room_id": "omm_xxx"}         # 会议室
{"type": "third_party", "third_party_email": "a@b.c"} # 外部邮箱
```

写成 `{"type": "user", "id": "ou_xxx"}` 会被拒成 **194004**（参与人类型无效）——
飞书这里没有统一的 `id` 键。单次最多 1000 人（会议室 100），单个日程上限 3000 人。
新参与人必须与组织者同企业。

删参与人要的是 **`attendee_id`，不是 open_id** —— 从加参与人的返回值或参与人列表里取；
不想去查 id 就用 `delete_ids`，按上面那套 `type`+对应 id 字段写，一次最多 500 个。

**会议室是异步预约**：加参与人返回成功不等于订到了房间，要回查 `rsvp_status`
（`needs_action` 预约中 / `accept` 成功 / `decline` 失败）。

`reply` 是**本人**接受/拒绝/待定（`accept`/`decline`/`tentative`），所以要
`prefer="user"` + 那个人的 `user_key`；用机器人身份调是机器人在回复自己那份邀请。

## 忙闲查询：找一个大家都空的时间

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 查某人/某会议室忙闲 | POST | `/open-apis/calendar/v4/freebusy/list` | body `time_min`、`time_max`(必填)、`user_id`、`room_id` |

这条的时间格式**和日程完全不同** —— 是 RFC 3339 字符串而不是秒级时间戳：

```
{"time_min": "2026-08-10T09:00:00+08:00", "time_max": "2026-08-10T18:00:00+08:00", "user_id": "ou_xxx"}
```

跨度不能超过 **90 天**。`user_id` 和 `room_id` **二选一传**，都传时 `user_id` 生效。
返回键是 `freebusy_list`，只有时间段没有日程标题 —— 这正是它的用途：查得到别人有空没空，
但看不到人家日程的内容，所以查一屋子人的空档不需要任何人给你日历读权限。
`only_busy` 默认 true。**一次只能查一个人**，多人得循环。

排会议的正常顺序是：逐人 freebusy → 自己挑交集 → 建日程 → 加参与人。

## 「一人一场」批量建日程

给 N 个人各自建**独立**日程（各自的排班/一对一，而不是把 N 个人拉进同一场会），
表格表达不了循环，所以这是调用方的活：**对每个 open_id 各打一次建日程 + 各打一次加参与人**。
建一场共享的会则相反 —— 建一次日程，把所有人一次性加进去。

分批要点：某个人失败不该让其余人回滚（那需要删掉已建的，反而更糟），**逐人记成败最后一起汇报**，
并明确告诉用户哪几个人没建上。带上 `idempotency_key`（32-128 字符）可以让重试不产生重复日程。

## 飞书没有的

- **没有「删除日程的撤销」**，也没有日程回收站；删了就没了（`193003` 是「已删除」）。
- **没有跨日历的全局日程搜索**：搜日程要指定 `calendar_id`，一次搜一个日历。
- **富文本描述改不了**：`description` 支持 HTML 标签，但客户端里编过富文本的日程再用 API
  更新会**丢掉原有格式**，这一点飞书自己文档里写明了。

```rules
- endpoint: POST /open-apis/calendar/v4/calendars/primary
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - 这是"获取或创建", 不是纯查询;返回 calendars[].calendar.calendar_id。
    - 默认拿到的是机器人自己的主日历;要某个人的必须 prefer=user + 他的 user_key。

- endpoint: GET /open-apis/calendar/v4/calendars
  token: tenant_then_user
  fields:
    page_size: {default: 500, max: 1000, min: 50, on_fail: "page_size 取值 50-1000"}
  paginate: {items: calendar_list, page_size: 500}
  pitfalls:
    - 返回键是 calendar_list 而不是 items。
    - role 字段(owner/writer/reader/free_busy_reader)决定能不能写, 读不到先看它。
    - sync_token 不能和 page_token 之外的限制参数混用, 否则 190009;过期报 190008, 置空重来。

- endpoint: POST /open-apis/calendar/v4/calendars/search
  token: tenant_then_user
  required: [query]
  fields:
    page_size: {default: 20, max: 100, min: 1}
  paginate: {items: items, page_size: 20}

- endpoint: POST /open-apis/calendar/v4/calendars
  token: tenant_then_user
  fields:
    summary: {pattern: '^[\s\S]{1,255}$', on_fail: "summary 最长 255 字符"}
    description: {pattern: '^[\s\S]{1,255}$', on_fail: "description 最长 255 字符"}
    permissions: {choices: [private, show_only_free_busy, public]}
  pitfalls:
    - 建的是 shared 共享日历;主日历用 /calendars/primary, 不是这条。
    - 建完当前身份自动订阅它;单个身份订阅上限 1000 个日历。

- endpoint: PATCH /open-apis/calendar/v4/calendars/:calendar_id
  token: tenant_then_user
  fields:
    permissions: {choices: [private, show_only_free_busy, public]}
  pitfalls:
    - 只传要改的字段;color / summary_alias 只对当前身份生效。

- endpoint: DELETE /open-apis/calendar/v4/calendars/:calendar_id
  token: tenant_then_user
  confirm: 删除共享日历
  pitfalls:
    - 日历删了不可恢复, 里面的日程一起没了, 飞书没有日历回收站。
    - 只能删 shared 共享日历且当前身份必须是 owner;删主日历会报 191004(日历类型错误)。

- endpoint: POST /open-apis/calendar/v4/calendars/:calendar_id/events
  token: tenant_then_user
  required: [start_time, end_time]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    summary: {pattern: '^[\s\S]{1,1000}$', on_fail: "summary 最长 1000 字符"}
    visibility: {choices: [default, public, private]}
    attendee_ability: {choices: [none, can_see_others, can_invite_others, can_modify_event]}
    free_busy_status: {choices: [busy, free]}
    recurrence: {pattern: '^[\s\S]{1,2000}$', on_fail: "recurrence 最长 2000 字符"}
    idempotency_key: {pattern: '^.{32,128}$', on_fail: "idempotency_key 长度 32-128 字符"}
  pitfalls:
    - start_time/end_time 是对象:定时 {timestamp(秒), timezone}, 全天 {date, timezone};timestamp 与 date 不能同时给。
    - 时间戳是**秒**级 —— 飞书任务的 due 是毫秒, 两者相反, 别串。
    - timezone 只是告诉飞书怎么解释这个时间, 不会帮你换算;算 timestamp 要自己按目标时区算。
    - 建日程不带参与人, 加人要另打 .../attendees。
    - 190007 是没开机器人能力, 不是参数错。

- endpoint: GET /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    need_attendee: {choices: ["true", "false"]}
  pitfalls:
    - 参与人默认不返回, 要 need_attendee=true 并配 max_attendee_num。
    - 重复日程的 event_id 形如 <uid>_<original_time>, 普通日程后缀 _0。
    - is_exception 为真是重复日程的例外实例, recurring_event_id 指回原重复日程。

- endpoint: PATCH /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    visibility: {choices: [default, public, private]}
    attendee_ability: {choices: [none, can_see_others, can_invite_others, can_modify_event]}
    free_busy_status: {choices: [busy, free]}
  pitfalls:
    - 这里没有 update_fields(那是任务域的);语义是"不传的字段就不改"。
    - start_time 与 end_time 必须同时传才生效, 只传一个等于没改时间。
    - schemas 是整体覆盖, 传 [] 即清空。
    - visibility/free_busy_status/color/reminders 只对当前身份生效;非组织者也只能改这四个。
    - API 更新会丢掉客户端编辑过的富文本 description 格式。

- endpoint: DELETE /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id
  token: tenant_then_user
  fields:
    need_notification: {choices: ["true", "false"]}
  pitfalls:
    - 删了不可恢复, 飞书没有日程回收站;再操作会报 193003(日程已删除)。
    - 只有日程组织者能删;不是组织者报 193002, 那不是参数错。
    - need_notification 默认 true, 会给全体参与人发 Bot 通知。
    - 要删重复日程的某一次, 先用 /instances 拿带时间戳后缀的 event_id, 否则删的是整条重复日程。

- endpoint: GET /open-apis/calendar/v4/calendars/:calendar_id/events
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    page_size: {default: 500, max: 1000, min: 50, on_fail: "page_size 取值 50-1000"}
  paginate: {items: items, page_size: 500}
  pitfalls:
    - start_time/end_time 是**秒级**时间戳字符串, 不是毫秒也不是对象。
    - 区间查询(start_time+end_time)与增量拉取(anchor_time/page_token/sync_token)互斥, 混用报 190009 或丢数据。
    - 只有传 anchor_time 才会返回 page_token;区间方式一次返回, 受 page_size 截断。

- endpoint: POST /open-apis/calendar/v4/calendars/:calendar_id/events/search
  token: tenant_then_user
  required: [query]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    query: {pattern: '^[\s\S]{1,200}$', on_fail: "query 最长 200 字符, 且别传空串"}
    page_size: {default: 20, max: 100, min: 10, on_fail: "page_size 取值 10-100"}
  paginate: {items: items, page_size: 20}
  pitfalls:
    - 只搜指定 calendar_id 这一个日历, 飞书没有跨日历的全局日程搜索。
    - 只模糊匹配日程标题;名称含下划线时必须精准查询, 模糊搜不到。
    - filter 里 start_time/end_time 同样是 {timestamp, timezone} 对象。

- endpoint: GET /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/instances
  token: tenant_then_user
  fields:
    page_size: {default: 500, max: 1000, min: 50}
  paginate: {items: items, page_size: 500}
  pitfalls:
    - 这是把一条重复日程展开成一个个实例, 改/删某一次前先来这里拿带后缀的 event_id。

- endpoint: GET /open-apis/calendar/v4/calendars/:calendar_id/events/instance_view
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - start_time/end_time 必填且是秒级时间戳;这条不分页, 区间大了会被截断。

- endpoint: POST /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees
  token: tenant_then_user
  required: [attendees]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    attendees: {max_items: 1000, min_items: 1, on_fail: "attendees 一次 1-1000 个(会议室最多 100)"}
  pitfalls:
    - 参与人对象按 type 换 id 键名:user→user_id, chat→chat_id, resource→room_id, third_party→third_party_email。
    - 写成 {"type":"user","id":"ou_xxx"} 会被拒成 194004, 飞书这里没有统一的 id 键。
    - 单个日程参与人上限 3000;新参与人必须与组织者同企业。
    - 会议室是异步预约, 返回成功不等于订到, 要回查 rsvp_status(needs_action/accept/decline)。
    - 194002 是没有新增参与人的权限, 需要是组织者或日程开了"参与人可邀请他人"。

- endpoint: GET /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    page_size: {default: 100, max: 1000, min: 1}
  paginate: {items: items, page_size: 100}
  pitfalls:
    - 这里才能拿到删参与人要用的 attendee_id;它不是 open_id。

- endpoint: POST /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees/batch_delete
  token: tenant_then_user
  fields:
    attendee_ids: {max_items: 500, on_fail: "一次最多删 500 个参与人"}
    delete_ids: {max_items: 500, on_fail: "一次最多删 500 个参与人"}
  pitfalls:
    - attendee_ids 要的是 attendee_id 不是 open_id;不想查 id 就用 delete_ids 按 type+对应 id 字段写。
    - 两个字段都不必填, 但数量上限合并计算(500)。
    - 194003 是没有删参与人的权限, 先确认 calendar_id 是组织者的日历。

- endpoint: POST /open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/reply
  token: user
  required: [rsvp_status]
  fields:
    rsvp_status: {choices: [accept, decline, tentative]}
  pitfalls:
    - 这是"本人"回复邀请, 用机器人身份调是机器人在回复自己那份;要 prefer=user + 本人 user_key。

- endpoint: POST /open-apis/calendar/v4/freebusy/list
  token: tenant_then_user
  required: [time_min, time_max]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - 时间是 RFC 3339 字符串(2026-08-10T09:00:00+08:00), 和日程的秒级时间戳完全不同。
    - time_min 到 time_max 跨度不能超过 90 天。
    - user_id 与 room_id 二选一传, 都传时 user_id 生效;一次只能查一个人, 多人要循环。
    - 返回键是 freebusy_list, 只有时间段没有日程内容 —— 所以查空档不需要对方的日历读权限。

- endpoint: POST /open-apis/calendar/v4/calendars/:calendar_id/subscribe
  token: tenant_then_user
  pitfalls:
    - 单个身份订阅上限 1000 个日历;退订是 /unsubscribe。
```

授权与权限：读日历/日程需 `calendar:calendar:readonly`（或 `calendar:calendar.event:read`），
写需 `calendar:calendar`（细分有 `calendar:calendar.event:update` / `:delete` / `:reply`、
`calendar:calendar:create` / `:delete`），忙闲另有 `calendar:calendar.free_busy:read`。
应用身份调用**必须开启机器人能力**，否则一律 `190007`（那不是参数错，别改参数重试）。
日程的写操作还要求当前身份对该日历有 **writer 或 owner** 权限、日历类型是 `primary` 或
`shared`（`191002` 无权限 / `191004` 类型不对）。

要以员工本人身份读写他自己的日程，传他的 `user_key` 并用 `prefer="user"`；
否则读到的、写进去的都是机器人自己那本日历 —— 而且**返回成功**。
