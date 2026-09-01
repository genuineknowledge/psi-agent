---
name: feishu-approval
description: 飞书审批（approval）接口表 —— 读定义表单、查待办任务、读实例详情、同意/拒绝、发起实例、订阅状态变更。用 feishu_api 按表调用。发起实例和读定义表单仍然是专用工具。
---

# 飞书审批接口

用 `feishu_api` 按下表调用。表里每一行都对应一个真实接口；`rules` 块是同一份知识的可执行副本，
参数不合规会在**发请求之前**被拦下来。

先分清两个 code，混用是本域最常见的报错来源：

- `approval_code` 是**审批定义**（"报销申请"这个流程本身），从审批后台或任务列表里拿。
- `instance_code`（有的接口叫 `instance_id`）是**某一次申请**（"张三 3 月 12 日那笔报销"）。

再分清三种身份，因为审批接口不像别的域那样"谁调用算谁"：

- **发起**实例：身份是 body 里的 `open_id` / `user_id`（申请人），机器人用 tenant token 提交即可，
  不需要申请人本人授权。
- **同意/拒绝**：身份是 body 里的 `user_id`（审批人），而且这个人必须是**当前任务的实际处理人**，
  否则飞书拒绝。代人审批要先确认这个 user_id 是真的审批人。
- **查待办**：`user_id` 是 query 参数，查谁的待办就传谁。

## 审批定义

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 读定义的表单模板 + 审批链 | GET | `/open-apis/approval/v4/approvals/:approval_code` | `user_id_type`、`with_admin_id` |
| 订阅状态变更（改成推送） | POST | `/open-apis/approval/v4/approvals/:approval_code/subscribe` | 无 |
| 取消订阅 | POST | `/open-apis/approval/v4/approvals/:approval_code/unsubscribe` | 无 |

订阅之后，这个定义下任何实例状态一变（通过/拒绝/撤回），飞书就往 app 的事件通道推
`approval_instance` 事件，通道层会主动私聊申请人。**所以不要轮询 `instances/:instance_id` 等结果**，
订阅一次就够：同一个 app 对同一个定义重复订阅是幂等的。

定义返回里的 `form` 是一个**JSON 字符串**（不是数组），里面是控件对象。填表要用的是每个控件的
`id` 和 `type` —— 字段 id 绝不能自己编，见下面「这些不走通用接口」。

## 任务与实例

| 要做的事 | method | endpoint | 关键参数 |
|---|---|---|---|
| 查某人的审批任务（待办等） | GET | `/open-apis/approval/v4/tasks/query` | `user_id`、`topic`、`user_id_type`、`page_size` |
| 列某定义下的实例 code | GET | `/open-apis/approval/v4/instances` | `approval_code`、`start_time`、`end_time`、`page_size` |
| 读一次申请的详情 | GET | `/open-apis/approval/v4/instances/:instance_id` | `user_id_type` |
| 同意 | POST | `/open-apis/approval/v4/tasks/approve` | `approval_code`、`instance_code`、`user_id`、`task_id`、`comment` |
| 拒绝 | POST | `/open-apis/approval/v4/tasks/reject` | 同上 |

`topic` 决定查哪一组：`1` 待办（最常用）、`2` 已办、`3` 我发起的、`17`/`18` 抄送我的。
返回在 `tasks` 键下（**不是 `items`**），每条里 `process_id` 才是 `instance_code`、
`definition_code`（有的返回给 `process_code`）才是 `approval_code`。

`status` / `process_status` 回的是数字，对照表：

| status（任务） | 含义 | process_status（实例） | 含义 |
|---|---|---|---|
| 1 | 待办 | 0 | none |
| 2 | 已办 | 1 | running（审批中） |
| 17 | 未读 | 2 | approved（通过） |
| 18 | 已读 | 3 | rejected（拒绝） |
| 33 | 处理中 | 4 | revoked（撤回） |
| 34 | 撤回 | 5 | terminated（终止） |

列实例返回在 `instance_code_list` 键下（也不是 `items`），而且**只有 code 没有内容** ——
要逐个再读详情。`start_time` / `end_time` 是 **Unix 毫秒的字符串**，两个都得给（飞书两个都要）；
想要"最近 30 天"就自己算出毫秒填进去。

读详情返回的 `form` 同样是 JSON 字符串。里面的文件类控件（`attachmentV2` / `image` / `imageV2` 等）
`value` 里是**直链**，只有约 12 小时有效期，要下就马上用 `feishu_file_download(is_url=True)` 下；
只有 `document` 类控件给的是云盘 token，那种用 `is_url=False`。

同意和拒绝是**两个不同的 endpoint**，不是一个接口带参数。`task_id` 从任务列表或实例详情的
`task_list` 里拿。审批动作**不可撤销**，所以代人同意/拒绝之前必须先把申请内容（金额、事由、申请人）
讲给用户听、拿到明确指令再发。

## 这些不走通用接口

| 工具 | 为什么必须是工具 |
|---|---|
| `feishu_approval_create` | 发起实例。`form` 要求的是**一个 JSON 字符串**，而里面装的是数组 —— 直接把数组塞进 body 会被飞书拒。工具负责这层"字符串里套 JSON"的封装，并挡住"申请人 id 一个都没给"（那样实例会记到没人名下）。 |
| `feishu_approval_get_definition` | 读定义表单。返回的 `form` 是 JSON 字符串，工具把它解析成 `{id, custom_id, name, type, required}` 的干净列表 —— 字段 id 必须照抄，让模型自己去解析字符串就是在鼓励它编 id。 |

```rules
- endpoint: GET /open-apis/approval/v4/approvals/:approval_code
  prefer_tool: feishu_approval_get_definition
  hard: true
  why: 返回的 form 是 JSON 字符串套数组, 工具解析成 {id, custom_id, name, type, required}; 让模型自己解字符串会导致它编字段 id, 而编出来的 id 提交时才报错。

- endpoint: POST /open-apis/approval/v4/approvals/:approval_code/subscribe
  pitfalls:
    - '同一个 app 对同一个定义重复订阅是幂等的, 一个定义调一次就够。'
    - '订阅后状态变更走事件通道推送并私聊申请人; 不要再轮询实例详情。'

- endpoint: POST /open-apis/approval/v4/approvals/:approval_code/unsubscribe

- endpoint: GET /open-apis/approval/v4/tasks/query
  required: [user_id]
  fields:
    topic: {default: '1', choices: ['1', '2', '3', '17', '18']}
    user_id_type: {default: open_id, choices: [open_id, union_id, user_id]}
    page_size: {default: 100, max: 200}
  paginate: {items: tasks, page_size: 100}
  pitfalls:
    - '返回在 tasks 键下, 不是 items。'
    - '每条里 process_id 才是 instance_code, definition_code (或 process_code) 才是 approval_code。'
    - 'status/process_status 是数字, 对照表在技能正文里。'

- endpoint: GET /open-apis/approval/v4/instances
  required: [approval_code]
  fields:
    page_size: {default: 100, max: 100}
  paginate: {items: instance_code_list, page_size: 100}
  pitfalls:
    - 'start_time 和 end_time 都是 Unix 毫秒的字符串, 飞书两个都要; 要「最近 30 天」自己算毫秒。'
    - '返回在 instance_code_list 键下, 只有 code 没有内容, 要逐个读详情。'

- endpoint: GET /open-apis/approval/v4/instances/:instance_id
  prefer_tool: feishu_approval_get
  hard: true
  why: 返回的 form 是 JSON 字符串, 工具解析出 attachments 列表并区分 kind=url(直链, 约 12 小时失效)和 kind=drive(云盘 token) —— 两种下载方式不同, 归档类技能直接依赖这个派生结果。

- endpoint: POST /open-apis/approval/v4/tasks/approve
  required: [approval_code, instance_code, user_id, task_id]
  pitfalls:
    - 'user_id 必须是当前任务的实际处理人, 否则飞书拒绝。'
    - '审批动作不可撤销; 代人同意前先把金额/事由/申请人讲给用户听。'

- endpoint: POST /open-apis/approval/v4/tasks/reject
  required: [approval_code, instance_code, user_id, task_id]
  pitfalls:
    - '拒绝和同意是两个不同 endpoint, 不是一个接口带参数。'
    - 'user_id 必须是当前任务的实际处理人; 动作不可撤销。'

- endpoint: POST /open-apis/approval/v4/instances
  prefer_tool: feishu_approval_create
  hard: true
  why: form 要求「一个 JSON 字符串」而内容是数组, 直接塞数组会被飞书拒; 工具还挡住「申请人 id 一个都没给」, 那样实例会记到没人名下。
```
