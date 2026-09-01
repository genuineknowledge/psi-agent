---
name: feishu-task
description: 飞书任务（task v2）接口表 —— 建任务派给人、列任务、搜任务（可查别人的）、读任务详情（含每个执行人完成情况）、改任务、标完成/重开、删任务、子任务、任务评论、成员/关注人增删、任务清单（tasklist）管理。用 feishu_api 按表调用。含在线学习（eLearning）学习记录读取。
---

# 飞书任务接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

飞书原生任务：给人派活、带截止时间、列出来、标完成。机器人自己的 tenant token 就能用
（`task:task:write`）。

## 任务本体

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 建任务（可同时派人） | POST | `/open-apis/task/v2/tasks` | query `user_id_type`；body `summary`、`description`、`due`、`members` |
| 列任务 | GET | `/open-apis/task/v2/tasks` | query `type`、`page_size`、`completed`、`page_token` |
| 读任务详情 | GET | `/open-apis/task/v2/tasks/:task_guid` | query `user_id_type` |
| 改任务 | PATCH | `/open-apis/task/v2/tasks/:task_guid` | body `task` + `update_fields` |
| 标完成 / 重开 | PATCH | `/open-apis/task/v2/tasks/:task_guid` | body `task.completed_at` + `update_fields` |
| **删任务** | DELETE | `/open-apis/task/v2/tasks/:task_guid` | 只有路径参数；**不可恢复** |
| **搜任务（能查别人的）** | POST | `/open-apis/task/v2/tasks/search` | body `query`、`filter`；query `page_size`(≤30) |
| **建子任务** | POST | `/open-apis/task/v2/tasks/:task_guid/subtasks` | body 同建任务 |
| **列子任务** | GET | `/open-apis/task/v2/tasks/:task_guid/subtasks` | query `page_size`(≤100) |

## 时间是毫秒字符串，而且要自己算

`due` 和 `completed_at` 都是**毫秒**epoch，而且是**字符串**不是数字：

```
"due": {"timestamp": "1786323600000", "is_all_day": false}
```

秒和毫秒差三个零，传秒进去任务会落在 1970 年。当前时间在每轮对话的上下文里给了，据此换算，
不要凭印象编一个时间戳。全天任务把 `is_all_day` 设 true。

**注意跟日历相反**：飞书**日程**的 `start_time.timestamp` 是**秒**级（见 `feishu-calendar`），
任务的 `due.timestamp` 是**毫秒**。同一个会话里一边建日程一边建任务时最容易串味。

**标完成就是把 `completed_at` 写成「现在」**，重开是把它写成字符串 `"0"`：

```
标完成：body = {"task": {"completed_at": "<现在的毫秒时间戳>"}, "update_fields": ["completed_at"]}
重开：  body = {"task": {"completed_at": "0"},                 "update_fields": ["completed_at"]}
```

## PATCH 必须带 update_fields，否则什么都不会变

改任务是「字段级」的：`task` 里放新值，`update_fields` 里列出**哪些字段这次要改**。
只写 `task` 不写 `update_fields`，飞书会当成「没有要改的字段」—— **返回成功，但一个字都没改**。
这是本域唯一的静默失败，两个数组必须一一对应：

```
body = {"task": {"summary": "新标题", "due": {...}}, "update_fields": ["summary", "due"]}
```

反过来，`update_fields` 里列了但 `task` 里没给值的字段，会被**清空**——想删掉截止时间就是这么删的，
但别误伤：改标题时顺手把 `due` 写进 `update_fields`，那条截止时间就没了。

**这套 `update_fields` 语义只属于任务域**。日程 PATCH 没有这个字段（不传即不改），
清单 PATCH 有但只认 `name` / `owner` 两个字段名。别把三者混起来。

## 派人：member 对象的三个键别对调

```
{"id": "ou_xxx", "type": "user", "id_type": "open_id", "role": "assignee"}
```

- `type` 是**成员类别**（`user` / `app`），
- `id_type` 是**id 形态**（`open_id` / `user_id`），
- `role` 是 `assignee`（执行人）或 `follower`（关注人）。

把 `type` 写成 `"open_id"` 会被拒成 **1470400** —— 那是把 id 形态填进了成员类别。
执行人和关注人放在**同一个 `members` 数组**里，靠 `role` 区分，不是两个字段。

## 建完之后改人：add_members / remove_members

建任务时 `members` 里给的人，后来要加要减**不能用 PATCH** —— `update_fields` 改不了成员，
得走这两个专门的端点：

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 加执行人/关注人 | POST | `/open-apis/task/v2/tasks/:task_guid/add_members` | body `members`、`client_token` |
| 减执行人/关注人 | POST | `/open-apis/task/v2/tasks/:task_guid/remove_members` | body `members` |

**移除时 `role` 是必填的**，而且要跟这个人当初的角色对上 —— 同一个人可以既是执行人又是
关注人，只写 id 不写 role 飞书不知道该摘掉哪一个。两个方向都是**幂等的**：加已在的人、
移不在的人都被**自动忽略且返回成功**，所以「成功」不等于「变化发生了」——
要确认结果就读返回的 `data.task.members`，那是操作后的最终成员列表。

加人一次最多 50 个（去重后），移人一次最多 500 个。加人的 `client_token`（10-100 字符）
是幂等键，但**别拿同一个 token 并发调**，会撞 1470422。

## 子任务

建子任务的 body **跟建任务完全一样**（`summary` 必填），只是多一个父任务的 `task_guid` 路径参数。
子任务本身也是个完整任务，有自己的 guid、成员、截止时间，也能再标完成。
父任务详情里的 `subtask_count` 是子任务数，`parent_task_guid` 反指父任务。

## 任务评论

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 写评论 | POST | `/open-apis/task/v2/comments` | body `content`(必填)、`resource_id`、`resource_type`、`reply_to_comment_id` |
| 列评论 | GET | `/open-apis/task/v2/comments` | query `resource_id`(**必填**)、`direction`、`page_size`(≤100) |
| 读一条评论 | GET | `/open-apis/task/v2/comments/:comment_id` | query `user_id_type` |
| 改评论 | PATCH | `/open-apis/task/v2/comments/:comment_id` | body `comment` + `update_fields` |
| 删评论 | DELETE | `/open-apis/task/v2/comments/:comment_id` | 只有路径参数 |

评论的 endpoint **不带 task_guid** —— 任务是靠 body/query 里的 `resource_id` 指定的
（`resource_type` 只有 `task` 一个值，默认就是它）。写评论时 `resource_id` 填任务 guid；
**列评论时 `resource_id` 是必填 query 参数**，漏了不会「列出所有评论」而是直接报错。
`content` 不能为空，上限 3000 字符。回复某条评论用 `reply_to_comment_id`，且被回复的评论
必须在同一个任务下。`direction` 默认 `asc`（按发表时间升序）。

## 任务清单（tasklist）

清单是任务的集合，用来把一摊活归到一个项目下。**任务和清单是多对多**：一个任务能同时在几个清单里，
所以「把任务放进清单」是任务侧的独立端点，不是建任务时的字段。

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 建清单 | POST | `/open-apis/task/v2/tasklists` | body `name`(必填)、`members` |
| 列清单 | GET | `/open-apis/task/v2/tasklists` | query `page_size`(≤100) |
| 读清单 | GET | `/open-apis/task/v2/tasklists/:tasklist_guid` | query `user_id_type` |
| 改清单（含换 owner） | PATCH | `/open-apis/task/v2/tasklists/:tasklist_guid` | body `tasklist` + `update_fields` + `origin_owner_to_role` |
| 删清单 | DELETE | `/open-apis/task/v2/tasklists/:tasklist_guid` | **不可恢复** |
| 列清单里的任务 | GET | `/open-apis/task/v2/tasklists/:tasklist_guid/tasks` | query `completed`、`created_from`/`created_to`、`page_size`(≤100) |
| 加清单成员 | POST | `/open-apis/task/v2/tasklists/:tasklist_guid/add_members` | body `members` |
| 减清单成员 | POST | `/open-apis/task/v2/tasklists/:tasklist_guid/remove_members` | body `members` |
| 把任务放进清单 | POST | `/open-apis/task/v2/tasks/:task_guid/add_tasklist` | body `tasklist_guid`(必填)、`section_guid` |
| 把任务移出清单 | POST | `/open-apis/task/v2/tasks/:task_guid/remove_tasklist` | body `tasklist_guid` |

**清单成员的角色跟任务成员完全不是一套**：清单是 `editor` / `viewer`（默认 `viewer`），
任务是 `assignee` / `follower`。往清单 `add_members` 里写 `role: "assignee"` 是错的。
清单成员的 `type` 还多一个 `chat`（把整个群加进来），任务成员没有。

**清单的 owner 换不了成员端点**：`add_members` 明确改不了所有者，换 owner 要走 PATCH
（`update_fields: ["owner"]`，`owner.role` 必须写 `"owner"`），并用 `origin_owner_to_role`
（`editor`/`viewer`/`none`，默认 `none`）决定原所有者留不留 —— 默认是**直接踢出清单**。
新 owner 如果原本是 editor/viewer，会**自动从成员列表消失**（一人一个角色）。

`created_from` / `created_to` 是**毫秒**时间戳的闭区间，跟 `due` 一样是毫秒。

## 列任务 vs 搜任务：查别人的活走搜索

`GET /tasks` 的 `type=my_tasks` 里，"my" 指的是**发请求的那个身份**。用机器人 token 调，
列出来的是机器人负责的任务 —— **不是某个员工的任务清单**。

想查**别人**的任务，有两条路，都别用 `GET /tasks`：

1. **`POST /tasks/search`** —— `filter.assignee_ids` / `creator_ids` / `follower_ids`
   （各最多 500 个）能按人筛。但它**只吃 user token**（`prefer="user"` + `user_key`），
   而且能搜到的范围就是**那个授权用户本来看得见的任务** —— 拿 A 的授权填 B 的 id，
   搜不出 A 看不到的东西，这不是越权通道。`page_size` 默认 15、**上限 30**（比别处都小）。
2. **读任务详情** —— 想知道「我派给张三的活他做完了没」，不要去列张三的任务：
   `assignee_related[]` 里每个执行人各带自己的 `completed_at`，多人任务里谁做完了一目了然。
   详情里的 `status` 是整个任务的状态，和单个执行人的完成情况不是一回事。

那个 `completed_at` 是完成度判定里**唯一的 E1 级硬证据**来源。要给出「某人 todo 完成情况」
的结论时，档位怎么定、取不到 E1 该怎么降档、多人怎么保证同一把尺子，一律以
[`todo-completion-standard`] 为准 —— 本 skill 只管怎么把数据取出来。

`completed` 只接受字符串 `"true"` / `"false"`，不传表示全都要。

## 删除是真的删

删任务和删清单**都不可恢复**，飞书任务没有回收站：

- **删任务**（`DELETE /tasks/:task_guid`）：删完就再也 GET 不到了。需要对该任务有编辑权限，
  没有则 1470403。飞书文档**没有说明**子任务和评论会不会跟着删 —— 别向用户承诺哪一种，
  要留证据就先把详情和评论读出来。
- **删清单**（`DELETE /tasklists/:tasklist_guid`）：需要清单的**所有者**权限（不是编辑权限），
  1470403 就是这个。清单删了之后对它的任何操作都不行了。飞书文档同样**没写**清单里的任务
  会不会一起消失 —— 不确定就先 `GET /tasklists/:guid/tasks` 把任务列出来存下。

因为不可逆而且飞书自己没交代清楚连带影响，这两条都挂了 `confirm` 闸门：本人会私聊收到一个
6 位确认码，必须由本人转告后带 `confirm=<那6位数字>` 重调。用户没给码就等于没批准。

## 在线学习（eLearning）学习记录

同一份技能里顺带这一个只读接口，因为它也只是一个平铺的 GET：

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 读课程报名/学习记录 | GET | `/open-apis/elearning/v2/course_registrations` | `user_ids`（可重复）、`user_id_type`、`page_size`、`page_token` |

`user_ids` 是**可重复的 query 参数**，不是逗号分隔的一个值 —— 传数组，`feishu_api` 会把
一个列表值展开成重复的键：`{"user_ids": ["ou_a", "ou_b"]}`。不传 `user_ids` 就是全部人。

只读**报名和学习记录**（谁报了名、完成状态、进度、分数）。**建课程、发布课程、指派给全员
是在 eLearning 管理后台做的**，开放平台没有对应写接口，别去找。

## 飞书任务没有的

- **没有任务回收站**，删了不可恢复（上面说过，这里再钉一遍，因为最容易被想当然）。
- **改任务成员不能走 PATCH**，`update_fields` 里写 `members` 是无效的，必须走
  `add_members` / `remove_members`。
- **清单里的自定义分组（section）建不了**：`add_tasklist` 能把任务放进已有的 `section_guid`，
  但本表没有建分组的行；要新建分组去客户端做。

```rules
- endpoint: POST /open-apis/task/v2/tasks
  token: tenant_then_user
  required: [summary]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - due.timestamp 是毫秒 epoch 的字符串, 传秒会落在 1970 年。
    - member 对象:type 是成员类别(user/app), id_type 是 id 形态(open_id/user_id), role 是 assignee/follower。
    - type 写成 "open_id" 会被拒成 1470400。
    - 执行人和关注人在同一个 members 数组里靠 role 区分。

- endpoint: GET /open-apis/task/v2/tasks
  token: tenant_then_user
  fields:
    type: {default: my_tasks}
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    completed: {choices: ["true", "false"]}
    page_size: {default: 50, max: 100, min: 1, on_fail: "page_size 取值 1-100"}
  paginate: {items: items, page_size: 50}
  pitfalls:
    - my_tasks 是"发请求的身份"自己的任务;机器人 token 列不出某个员工的任务清单。
    - 要看某人的任务须用那个人的授权(prefer=user + user_key)。

- endpoint: GET /open-apis/task/v2/tasks/:task_guid
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - assignee_related[] 里每个执行人各带自己的 completed_at, 这才是"某人做完没"的依据。
    - task.status 是整个任务的状态, 和单个执行人的完成情况不是一回事。

- endpoint: DELETE /open-apis/task/v2/tasks/:task_guid
  token: tenant_then_user
  confirm: 删除任务
  pitfalls:
    - 删完再也 GET 不到, 飞书任务没有回收站, 不可恢复。
    - 飞书文档没说明子任务和评论会不会跟着删 —— 别向用户承诺, 要留证据先把详情和评论读出来。
    - 1470403 是没有该任务的编辑权限;1470404 是任务不存在或已被删过。

- endpoint: POST /open-apis/task/v2/tasks/search
  token: user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    query: {pattern: '^[\s\S]{0,50}$', on_fail: "query 最长 50 字符"}
    page_size: {default: 15, max: 30, min: 1, on_fail: "page_size 取值 1-30(这个端点上限是 30, 比别处小)"}
  paginate: {items: items, page_size: 15}
  pitfalls:
    - 只吃 user_access_token, 必须 prefer=user + user_key;机器人 tenant token 调不了。
    - 搜得到的范围就是那个授权用户本来看得见的任务;拿 A 的授权填 B 的 id 搜不出 A 看不到的东西。
    - filter 里 creator_ids/assignee_ids/follower_ids 各最多 500 个;due_time 用 ISO 8601 字符串。
    - page_size 上限 30, 不是别处的 100。

- endpoint: POST /open-apis/task/v2/tasks/:task_guid/subtasks
  token: tenant_then_user
  required: [summary]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - body 跟建任务完全一样(summary 必填), 只多一个父任务的 task_guid 路径参数。
    - due.timestamp 同样是毫秒字符串。
    - 需要对父任务有编辑权限, 否则 1470403。

- endpoint: GET /open-apis/task/v2/tasks/:task_guid/subtasks
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    page_size: {default: 50, max: 100, min: 1, on_fail: "page_size 取值 1-100"}
  paginate: {items: items, page_size: 50}

- endpoint: POST /open-apis/task/v2/tasks/:task_guid/add_members
  token: tenant_then_user
  required: [members]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    members: {max_items: 50, min_items: 1, on_fail: "members 一次 1-50 个(去重后)"}
    client_token: {pattern: '^[\s\S]{10,100}$', on_fail: "client_token 长度 10-100 字符"}
  pitfalls:
    - 改成员不能走 PATCH, update_fields 里写 members 是无效的, 必须用这条。
    - 幂等:已在任务里的人被自动忽略且返回成功, "成功"不等于"有变化", 读 data.task.members 确认。
    - role 是 assignee/follower(不是清单那套 editor/viewer);type 是 user/app。
    - 同一个 client_token 并发调会撞 1470422。
    - 1470610/1470611 分别是执行人/关注人数量超上限。

- endpoint: POST /open-apis/task/v2/tasks/:task_guid/remove_members
  token: tenant_then_user
  required: [members]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    members: {max_items: 500, min_items: 1, on_fail: "members 一次 1-500 个"}
  pitfalls:
    - 每项的 role 是**必填**且要跟这人当初的角色对上 —— 一个人可能既是执行人又是关注人。
    - 幂等:移不在任务里的人被自动忽略且返回成功, 读 data.task.members 确认结果。

- endpoint: POST /open-apis/task/v2/comments
  token: tenant_then_user
  required: [content]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    content: {pattern: '^[\s\S]{1,3000}$', on_fail: "content 不能为空, 上限 3000 字符"}
    resource_type: {default: task, choices: [task]}
  pitfalls:
    - endpoint 不带 task_guid;任务靠 body 里的 resource_id 指定(填任务 guid)。
    - 回复某条评论用 reply_to_comment_id, 且被回复的评论必须在同一个任务下。

- endpoint: GET /open-apis/task/v2/comments
  token: tenant_then_user
  required: [query.resource_id]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    resource_type: {default: task, choices: [task]}
    direction: {default: asc, choices: [asc, desc]}
    page_size: {default: 50, max: 100, min: 1, on_fail: "page_size 取值 1-100"}
  paginate: {items: items, page_size: 50}
  pitfalls:
    - resource_id 是**必填 query 参数**(任务 guid);漏了不会列出全部评论而是直接报错。
    - direction 默认 asc(按发表时间升序)。

- endpoint: GET /open-apis/task/v2/comments/:comment_id
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}

- endpoint: PATCH /open-apis/task/v2/comments/:comment_id
  token: tenant_then_user
  required: [comment, update_fields]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    update_fields: {min_items: 1, on_fail: "update_fields 不能为空, 否则飞书返回成功但一个字段都不改"}
  pitfalls:
    - 和改任务一样是字段级的:comment 放新值, update_fields 列出这次要改哪些。

- endpoint: DELETE /open-apis/task/v2/comments/:comment_id
  token: tenant_then_user
  pitfalls:
    - 删的是评论本身, 不影响任务;comment_id 从列评论接口取。

- endpoint: POST /open-apis/task/v2/tasklists
  token: tenant_then_user
  required: [name]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    name: {pattern: '^[\s\S]{1,100}$', on_fail: "name 必填且最长 100 字符"}
    members: {max_items: 500, on_fail: "members 最多 500 个"}
  pitfalls:
    - 清单成员的 role 是 editor/viewer(默认 viewer), 不是任务那套 assignee/follower。
    - 清单成员的 type 多一个 chat(把整个群加进来), 任务成员没有。
    - 创建者自动成为 owner;把创建者写进 members 也仍是 owner 且不出现在成员列表里。

- endpoint: GET /open-apis/task/v2/tasklists
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    page_size: {default: 50, max: 100, min: 1, on_fail: "page_size 取值 1-100"}
  paginate: {items: items, page_size: 50}
  pitfalls:
    - 列的是"调用身份自己可读"的清单;机器人 token 列不出某个员工的清单。

- endpoint: GET /open-apis/task/v2/tasklists/:tasklist_guid
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}

- endpoint: PATCH /open-apis/task/v2/tasklists/:tasklist_guid
  token: tenant_then_user
  required: [tasklist, update_fields]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    update_fields: {min_items: 1, on_fail: "update_fields 不能为空, 否则飞书返回成功但一个字段都不改"}
    origin_owner_to_role: {choices: [editor, viewer, none]}
  pitfalls:
    - update_fields 只支持 name 和 owner 两个字段名(跟任务那套不一样)。
    - 换 owner 时 owner.role 必须写 "owner", 且 type 只能是 user/app(不能是 chat)。
    - origin_owner_to_role 默认 none, 意思是原所有者被**直接踢出清单**;要留人就显式写 editor/viewer。
    - 新 owner 原本是 editor/viewer 的话会自动从成员列表消失(一人一个角色)。
    - 改名只需编辑权限, 换 owner 需要所有者权限。
    - 这条管不了成员增删, 那是 add_members / remove_members。

- endpoint: DELETE /open-apis/task/v2/tasklists/:tasklist_guid
  token: tenant_then_user
  confirm: 删除任务清单
  pitfalls:
    - 清单删了不可恢复, 之后对它的任何操作都不行。
    - 需要清单的**所有者**权限(不是编辑权限), 1470403 就是这个。
    - 飞书文档没写清单里的任务会不会一起消失 —— 不确定就先 GET /tasklists/:guid/tasks 存下来。

- endpoint: GET /open-apis/task/v2/tasklists/:tasklist_guid/tasks
  token: tenant_then_user
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    completed: {choices: ["true", "false"]}
    page_size: {default: 50, max: 100, min: 1, on_fail: "page_size 取值 1-100"}
  paginate: {items: items, page_size: 50}
  pitfalls:
    - created_from / created_to 是**毫秒**时间戳的闭区间。
    - 任务按客户端的拖拽顺序返回;中间某页可能合法地为空, 要靠 has_more 判断而不是靠条数。

- endpoint: POST /open-apis/task/v2/tasklists/:tasklist_guid/add_members
  token: tenant_then_user
  required: [members]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    members: {max_items: 500, min_items: 1, on_fail: "members 一次 1-500 个"}
  pitfalls:
    - role 只能是 editor/viewer(默认 viewer);写 assignee/follower 是把任务那套用错了地方。
    - 这条**改不了所有者** —— 换 owner 要走 PATCH /tasklists/:tasklist_guid。
    - 已是成员且角色相同则忽略;角色不同等于更新角色;已是所有者则忽略。
    - 1470612 是清单成员数量超限。

- endpoint: POST /open-apis/task/v2/tasklists/:tasklist_guid/remove_members
  token: tenant_then_user
  required: [members]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    members: {max_items: 500, min_items: 1, on_fail: "members 一次 1-500 个"}

- endpoint: POST /open-apis/task/v2/tasks/:task_guid/add_tasklist
  token: tenant_then_user
  required: [tasklist_guid]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - 需要**同时**具备该任务和该清单的编辑权限, 否则 1470403。
    - 任务已在清单里也返回成功(幂等)。
    - section_guid 不填就进默认分组;本表没有建分组的接口, 新建分组去客户端做。

- endpoint: POST /open-apis/task/v2/tasks/:task_guid/remove_tasklist
  token: tenant_then_user
  required: [tasklist_guid]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
  pitfalls:
    - 移出清单不等于删任务, 任务还在;删任务是 DELETE /tasks/:task_guid。

- endpoint: PATCH /open-apis/task/v2/tasks/:task_guid
  token: tenant_then_user
  required: [task, update_fields]
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    update_fields: {min_items: 1, on_fail: "update_fields 不能为空, 否则飞书返回成功但一个字段都不改"}
  pitfalls:
    - 只写 task 不写 update_fields 会静默不改:返回成功, 数据没动。
    - update_fields 里列了而 task 里没给值的字段会被清空(删截止时间就是这么删的)。
    - 标完成 = completed_at 写成现在的毫秒时间戳;重开 = 写字符串 "0"。

- endpoint: GET /open-apis/elearning/v2/course_registrations
  token: tenant
  fields:
    user_id_type: {default: open_id, choices: [open_id, user_id, union_id]}
    page_size: {default: 100, max: 100, min: 1, on_fail: "page_size 取值 1-100"}
  paginate: {items: items, page_size: 100}
  pitfalls:
    - user_ids 是可重复的 query 参数, 传数组而不是逗号分隔的字符串。
    - 只读报名和学习记录;建课程/发布/指派全员在 eLearning 管理后台, 开放平台没有写接口。
```

授权与权限：任务需要 `task:task`（写要 `task:task:write`）；eLearning 那条只认机器人的
tenant token（`elearning:course_registration:read`）。任务的建/改/完成想以员工本人身份出现在
他的任务列表里，就传 `user_key` 并用 `prefer=user`；否则任务的创建者是机器人。
