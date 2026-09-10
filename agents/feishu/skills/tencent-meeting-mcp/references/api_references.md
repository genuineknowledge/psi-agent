# 腾讯会议 MCP 工具调用示例

本文件提供各工具的调用示例。参数说明已集成到 MCP 工具 Schema 中，可通过 `tools/list` 查看。

---

## 目录

- [会议管理](#会议管理)
  - [创建会议](#schedule_meeting--创建会议)
  - [修改会议](#update_meeting--修改会议)
  - [取消会议](#cancel_meeting--取消会议)
  - [查询会议详情](#get_meeting--查询会议详情)
  - [通过会议号查询](#get_meeting_by_code--通过会议号查询)
- [成员管理](#成员管理)
  - [获取参会成员明细](#get_meeting_participants--获取参会成员明细)
  - [获取受邀成员列表](#get_meeting_invitees--获取受邀成员列表)
  - [查询等候室成员](#get_waiting_room--查询等候室成员)
  - [管理会中等候室成员](#manage_waiting_room--管理会中等候室成员)
  - [查询用户会议列表](#get_user_meetings--查询用户会议列表)
  - [查询已结束会议](#get_user_ended_meetings--查询已结束会议)
  - [搜索会议列表](#search_meetings--搜索会议列表)
  - [导出参会成员统计](#export_participants--导出参会成员统计)
  - [获取异步任务结果](#get_job_result--获取异步任务结果)
- [录制与转写](#录制与转写)
  - [查询录制列表](#get_records_list--查询录制列表)
  - [搜索录制文件](#search_records--搜索录制文件)
  - [获取录制下载地址](#get_record_addresses--获取录制下载地址)
  - [查询转写详情](#get_transcripts_details--查询转写详情)
  - [查询转写段落](#get_transcripts_paragraphs--查询转写段落)
  - [搜索转写内容](#search_transcripts--搜索转写内容)
  - [获取智能纪要](#get_smart_minutes--获取智能纪要)
  - [搜索元宝纪要](#search_minutes--搜索元宝纪要)
  - [查询元宝纪要详情](#get_minutes--查询元宝纪要详情)
- [录制权限申请](#录制权限申请)
  - [申请录制权限-预览](#apply_record_permission_prepare--申请录制权限-预览)
  - [申请录制权限-提交](#apply_record_permission_commit--申请录制权限-提交)
- [通讯录](#通讯录)
    - [搜索企业通讯录](#contact_search--搜索企业通讯录)
    - [按手机号批量查找用户](#contact_lookup_by_phone--按手机号批量查找用户)
    - [按邮箱批量查找用户](#contact_lookup_by_email--按邮箱批量查找用户)
- [受邀人管理](#受邀人管理)
    - [添加受邀人](#meeting_invitees_add--添加受邀人)
    - [移除受邀人](#meeting_invitees_remove--移除受邀人)
    - [替换受邀人](#meeting_invitees_replace--替换受邀人)
- [会中控制](#会中控制)
    - [呼叫成员入会](#meeting_control_call--呼叫成员入会)
    - [踢出会议成员](#meeting_control_kick--踢出会议成员)
- [反馈](#反馈)
  - [提交反馈（Agent 意见箱）](#submit_feedback--提交反馈agent-意见箱)

---

## 会议管理

### `schedule_meeting` — 创建会议

#### 调用示例

```bash
# 普通会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "schedule_meeting",
  "arguments": {
    "subject": "产品周会",
    "start_time": "2026-03-25T15:00:00+08:00",
    "end_time": "2026-03-25T16:00:00+08:00"
  }
}'

# 周期性会议（每周开会，共重复5次）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "schedule_meeting",
  "arguments": {
    "subject": "每周例会",
    "start_time": "2026-03-25T15:00:00+08:00",
    "end_time": "2026-03-25T16:00:00+08:00",
    "meeting_type": 1,
    "recurring_rule": {
      "recurring_type": 2,
      "until_type": 1,
      "until_count": 5
    }
  }
}'

# 创建会议时同时指定受邀人（invitees 为 open_id 数组，最多 100）
# open_id 需先通过 contact_search / contact_lookup_by_phone / contact_lookup_by_email 获取
python3 scripts/tencent_meeting.py tools/call '{
  "name": "schedule_meeting",
  "arguments": {
    "subject": "产品评审会",
    "start_time": "2026-03-25T15:00:00+08:00",
    "end_time": "2026-03-25T16:00:00+08:00",
    "invitees": ["open_id_xxx", "open_id_yyy"]
  }
}'
```

---

### `update_meeting` — 修改会议

#### 调用示例

```bash
# 修改非周期性会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "update_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "subject": "新主题",
    "start_time": "2026-03-25T16:00:00+08:00",
    "end_time": "2026-03-25T17:00:00+08:00"
  }
}'

# 修改周期性会议其中一场子会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "update_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "start_time": "2026-03-26T10:00:00+08:00",
    "end_time": "2026-03-26T11:00:00+08:00",
    "meeting_type": 1,
    "recurring_rule": {
      "sub_meeting_id": "yyy"
    }
  }
}'

# 修改会议时同步增量添加受邀人（invitees_operate_type="add"）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "update_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "invitees": ["open_id_aaa", "open_id_bbb"],
    "invitees_operate_type": "add"
  }
}'

# 修改会议时同步整体替换受邀人（invitees_operate_type="replace"，传空数组=清空）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "update_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "invitees": ["open_id_ccc"],
    "invitees_operate_type": "replace"
  }
}'
```

> `invitees_operate_type` 取值（字符串枚举）：`"add"`（增量添加）/ `"remove"`（批量移除）/ `"replace"`（整体替换）。
> `invitees` 与 `invitees_operate_type` 必须**成对出现**，缺一报参数错误。
> 仅做受邀人变更（无其他字段修改）时，推荐使用 `meeting_invitees_add` / `_remove` / `_replace` 专用工具。

---

### `cancel_meeting` — 取消会议

#### 调用示例

```bash
# 取消普通会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "cancel_meeting",
  "arguments": {
    "meeting_id": "xxx"
  }
}'

# 取消周期性会议的某个子会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "cancel_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "sub_meeting_id": "yyy"
  }
}'

# 取消整场周期性会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "cancel_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "meeting_type": 1
  }
}'
```

---

### `get_meeting` — 查询会议详情

参数以 MCP schema 为准（`tools/list`）。

---

### `get_meeting_by_code` — 通过会议号查询

参数以 MCP schema 为准（`tools/list`）。

---

## 成员管理

### `get_meeting_participants` — 获取参会成员明细

#### 调用示例

```bash
# 分页接续：首次不传 page_token；响应 has_more=true 时，将 next_page_token 回填继续翻页
# 可选按参会时间过滤：叠加传 start_time / end_time 即可
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_meeting_participants",
  "arguments": {
    "meeting_id": "xxx",
    "page_size": 20,
    "page_token": "上一次响应中的next_page_token"
  }
}'
```

---

### `get_meeting_invitees` — 获取受邀成员列表

参数以 MCP schema 为准（`tools/list`）。

---

### `get_waiting_room` — 查询等候室成员

参数以 MCP schema 为准（`tools/list`）。

---

### `manage_waiting_room` — 管理会中等候室成员

#### 调用示例

```bash
# 将等候室成员移入会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "manage_waiting_room",
  "arguments": {
    "meeting_id": "xxx",
    "user": ["openid1"],
    "operate_type": "enter-meeting"
  }
}'

# 将会中成员移入等候室
python3 scripts/tencent_meeting.py tools/call '{
  "name": "manage_waiting_room",
  "arguments": {
    "meeting_id": "xxx",
    "user": ["openid1"],
    "operate_type": "back-to-waiting"
  }
}'

# 将等候室成员移出等候室，并允许再次加入会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "manage_waiting_room",
  "arguments": {
    "meeting_id": "xxx",
    "user": ["openid1"],
    "operate_type": "expel",
    "allow_rejoin": true
  }
}'
```

---

### `get_user_meetings` — 查询用户会议列表

#### 调用示例

```bash
# 分页接续：首次不传 page_token；has_more=true 时，回填上次的 next_page_token 继续翻页
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_user_meetings",
  "arguments": {
    "is_show_all_sub_meetings": 0,
    "page_token": "上一次响应中的next_page_token"
  }
}'
```

---

### `get_user_ended_meetings` — 查询已结束会议

#### 调用示例

```bash
# 时间窗口 + 分页接续：首次不传 page_token；has_more=true 时回填继续翻页
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_user_ended_meetings",
  "arguments": {
    "start_time": "2026-03-25T00:00:00+08:00",
    "end_time": "2026-03-25T23:59:59+08:00",
    "page_size": 10,
    "page_token": "上一次响应中的next_page_token"
  }
}'
```

---

### `search_meetings` — 搜索会议列表

> `q_fields` 取值：`subject`（主题，分词匹配）/ `creator`（创建人昵称，模糊匹配）/ `note`（备注，模糊匹配）/ `all`（所有字段）。`page_size` 上限 30。

#### 调用示例

```bash
# 关键词搜索（q_fields 按需切换：subject / creator / note / all）+ 分页接续
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_meetings",
  "arguments": {
    "q": "产品周会",
    "q_fields": "subject",
    "page_size": 30,
    "page_token": "上一次响应中的next_page_token"
  }
}'

# 按会议号精准查询
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_meetings",
  "arguments": {
    "meeting_code": "904854736"
  }
}'

# 按时间窗口筛选
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_meetings",
  "arguments": {
    "from": "2026-03-20T00:00:00+08:00",
    "to": "2026-03-25T23:59:59+08:00",
    "page_size": 30
  }
}'
```

---

### `export_participants` — 导出参会成员统计

> 异步导出参会成员统计数据（含累计参会时长、会议互动行为等统计），适用于考勤分析等场景。返回 `job_id`，需通过 `get_job_result` 查询导出结果。

#### 调用示例

```bash
# 基础导出（默认 xlsx 格式）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "export_participants",
  "arguments": {
    "meeting_id": "xxx"
  }
}'

# 指定导出为 json 格式
python3 scripts/tencent_meeting.py tools/call '{
  "name": "export_participants",
  "arguments": {
    "meeting_id": "xxx",
    "file_type": "json"
  }
}'

# 周期性会议导出某场子会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "export_participants",
  "arguments": {
    "meeting_id": "xxx",
    "sub_meeting_id": "yyy"
  }
}'

# 按入会时间范围筛选导出
python3 scripts/tencent_meeting.py tools/call '{
  "name": "export_participants",
  "arguments": {
    "meeting_id": "xxx",
    "start_time": "2026-03-01T00:00:00+08:00",
    "end_time": "2026-03-25T23:59:59+08:00",
    "file_type": "xlsx"
  }
}'
```

---

### `get_job_result` — 获取异步任务结果

> 通过 `export_participants` 等异步导出接口返回的 `job_id` 查询任务状态及下载链接。任务状态：1-成功（可获取下载链接，有效期2小时）、2-失败（查看 error_msg）、3-处理中（稍后重试）。

#### 调用示例

```bash
# 查询导出任务结果
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_job_result",
  "arguments": {
    "job_id": "zzlWxxxxxxxxxxxxxxxxiTs"
  }
}'
```

---

## 录制与转写

### `get_records_list` — 查询录制列表

> **三种查询模式互斥**：`start_time + end_time`（时间跨度 ≤ 31 天，可翻页）/ `meeting_id`（无需时间）/ `meeting_code`（无需时间）。同一次调用只使用其中一种。

#### 调用示例

```bash
# 按时间范围查询 + 分页接续（跨度 ≤ 31 天）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_records_list",
  "arguments": {
    "start_time": "2026-03-25T00:00:00+08:00",
    "end_time": "2026-03-25T23:59:59+08:00",
    "page_size": 10,
    "page_token": "上一次响应中的next_page_token"
  }
}'

# 按会议 ID 查询（无需传时间）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_records_list",
  "arguments": {
    "meeting_id": "xxx"
  }
}'

# 按会议号查询（无需传时间）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_records_list",
  "arguments": {
    "meeting_code": "904854736"
  }
}'
```

---

### `search_records` — 搜索录制文件

> `q_fields` 取值分为两类，**snippet 返回行为不同**：
> - **返回 snippet**：`transcript_content`（转写原文）/ `smart_minutes`（智能纪要）/ `timeline`（时间轴），均为分词匹配
> - **不返回 snippet**：`subject`（主题，分词匹配）/ `creator`（创建人昵称，模糊匹配）/ `all`（所有字段，按各字段自身方式匹配）
>
> `page_size` 上限 30。可用 `meeting_id` / `meeting_code` 精准定位；`file_type` 可选 `video` 等。

#### 调用示例

```bash
# 关键词搜索（q_fields 按需切换）+ 分页接续
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_records",
  "arguments": {
    "q": "技术架构",
    "q_fields": "transcript_content",
    "page_size": 20,
    "page_token": "上一次响应中的next_page_token"
  }
}'

# 按会议 ID / 会议号定位（二选一）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_records",
  "arguments": {
    "meeting_id": "xxx",
    "page_size": 20
  }
}'

# 按时间窗口 + 文件类型筛选
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_records",
  "arguments": {
    "from": "2026-03-20T00:00:00+08:00",
    "to": "2026-03-25T23:59:59+08:00",
    "file_type": "video",
    "page_size": 20
  }
}'
```

---

### `get_record_addresses` — 获取录制下载地址

参数以 MCP schema 为准（`tools/list`）。

---

### `get_transcripts_details` — 查询转写详情

参数以 MCP schema 为准（`tools/list`）；`pid` 为字符串型起始段落号（首页传 `"0"`），`limit` 为数字型。

---

### `get_transcripts_paragraphs` — 查询转写段落

参数以 MCP schema 为准（`tools/list`）。

---

### `search_transcripts` — 搜索转写内容

参数以 MCP schema 为准（`tools/list`）。

---

### `get_smart_minutes` — 获取智能纪要

参数以 MCP schema 为准（`tools/list`）；可选 `lang`（默认 `default` 原文，可传 `en` 等指定翻译语言）与 `pwd`（录制文件访问密码，仅当录制设置了密码时需要）。

### `search_minutes` — 搜索元宝纪要

> **纪要搜索路由策略**：用户说「搜索纪要」「找纪要」时，需先判断录制权限再决定路由：
> - **有录制权限**：优先调用 `search_records`（`q_fields=smart_minutes`）搜录制纪要，无结果再降级调用 `search_minutes` 搜元宝纪要
> - **无录制权限**：直接调用 `search_minutes` 搜元宝纪要
> - **细节/原话类问题**（如「谁说要延期」）：直接走录制逐字稿 `search_records`（`q_fields=transcript_content`）或 `search_transcripts`
> - **无录制权限 + 需要逐字稿**：引导用户申请录制权限（场景9）
>
> 详细路由规则见 SKILL.md 场景7A「纪要搜索路由策略」。

#### 调用示例

```bash
# 【路由示例1】有录制权限时，用户说"搜索纪要"——优先搜录制纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_records",
  "arguments": {
    "q": "产品评审",
    "q_fields": "smart_minutes",
    "page_size": 20
  }
}'
# 若录制纪要无结果，降级搜元宝纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_minutes",
  "arguments": {
    "q": "产品评审",
    "page_size": 20
  }
}'

# 【路由示例2】无录制权限时，直接搜元宝纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_minutes",
  "arguments": {
    "q": "产品评审",
    "page_size": 20
  }
}'

# 【路由示例3】细节/原话类问题（如"谁说要延期"）——直接走录制逐字稿
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_records",
  "arguments": {
    "q": "延期",
    "q_fields": "transcript_content",
    "page_size": 20
  }
}'

# 按关键词搜索元宝纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_minutes",
  "arguments": {
    "q": "产品评审",
    "page_size": 20
  }
}'

# 按时间窗口搜索
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_minutes",
  "arguments": {
    "from": "2026-03-01T00:00:00+08:00",
    "to": "2026-03-31T23:59:59+08:00",
    "page_size": 20
  }
}'

# 关键词 + 时间窗口组合搜索
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_minutes",
  "arguments": {
    "q": "技术方案",
    "from": "2026-03-20T00:00:00+08:00",
    "to": "2026-03-25T23:59:59+08:00",
    "page_size": 20
  }
}'

# 翻页查询
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_minutes",
  "arguments": {
    "q": "周会",
    "page_token": "上一次响应中的next_page_token",
    "page_size": 20
  }
}'
```

---

### `get_minutes` — 查询元宝纪要详情

#### 调用示例

```bash
# 按会议号查询稳态纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_minutes",
  "arguments": {
    "meeting_code": "904854736",
    "overview": true,
    "summary_points": true,
    "todos": true
  }
}'

# 按会议ID查询稳态纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_minutes",
  "arguments": {
    "meeting_id": "xxx",
    "overview": true,
    "summary_points": true,
    "todos": true
  }
}'

# 按会议ID + 子会议ID查询（周期会议）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_minutes",
  "arguments": {
    "meeting_id": "xxx",
    "sub_meeting_id": "xxx",
    "overview": true,
    "summary_points": true,
    "todos": true
  }
}'

# 获取滚动（瞬态）纪要（必须传 minute_id + short_summary=true）
# ⚠️ 滚动纪要必须自动全量拉取：循环调用直到 has_more=false
# 首次调用（不传 page_token）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_minutes",
  "arguments": {
    "minute_id": "xxx",
    "short_summary": true
  }
}'
# 响应结构示例：
# {
#   "minute_id": "xxx",
#   "items": [
#     {"timestamp": "05:30", "speakers": ["张三"], "content": "总结内容"},
#     ...
#   ],
#   "total_count": 50,
#   "has_more": true,
#   "next_page_token": "abc123"
# }
# 若 has_more=true，继续用 next_page_token 拉取下一页：

# 获取滚动纪要 - 续页拉取（循环直到 has_more=false）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_minutes",
  "arguments": {
    "minute_id": "xxx",
    "short_summary": true,
    "page_token": "上一次响应中的next_page_token",
    "page_size": 100
  }
}'
# 将所有分页的 items 聚合后一次性返回给用户

# 稳态纪要翻页查询（page_size 默认10，最大30）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_minutes",
  "arguments": {
    "meeting_id": "xxx",
    "overview": true,
    "summary_points": true,
    "todos": true,
    "page_token": "上一次响应中的next_page_token",
    "page_size": 10
  }
}'
```

---

## 录制权限申请

> 录制权限申请采用 **两步流程**：先调用 `apply_record_permission_prepare` 获取预览信息向用户展示并确认，
> 用户明确同意后再调用 `apply_record_permission_commit` 正式提交。**严禁跳过预览阶段直接提交申请。**
> 触发场景：用户访问录制相关内容（下载地址/转写/智能纪要）出现无权限错误，或用户主动请求申请录制权限。

### `apply_record_permission_prepare` — 申请录制权限-预览

参数以 MCP schema 为准（`tools/list`）；推荐同时携带 `meeting_id`（若已知），便于服务端校验。

#### 返回字段说明

| 字段 | 说明 |
|------|------|
| `preview.meeting_record_id` | 会议录制 ID |
| `preview.approval_name` | 申请类型文案 |
| `preview.subject` | 会议标题 |
| `preview.file_owner` | 录制所有者名称 |
| `preview.apply_note` | 权限申请备注信息 |
| `preview.applicant` | 申请人名称 |
| `expires_in` | 预览有效期（秒），接近过期时建议重新调用 |

> **调用后必须**：向用户完整展示预览信息（标题/录制所有者/申请人/申请说明），等待用户明确同意后再调用 commit 工具。

---

### `apply_record_permission_commit` — 申请录制权限-提交

> **前置条件**：已调用 prepare 工具向用户展示预览信息，并获得用户明确同意。

参数与 `apply_record_permission_prepare` 保持一致（若 prepare 传了 `meeting_id`，commit 也应传）。

#### 返回字段说明

| 字段 | 说明 |
|------|------|
| `unique_id` | 申请 ID |
| `status` | 审批状态 |
| `message` | 审批状态描述 |
| `approval_url` | 审批链接（**必须展示给用户**，便于跟进审批进度） |
| `share_text` | 申请说明描述 |

## 通讯录

> **调用场景白名单（强约束）**：通讯录工具（`contact_search` / `contact_lookup_by_phone` / `contact_lookup_by_email`）**仅可用于以下两类场景**，用于将姓名/手机号/邮箱**正向解析**为 `open_id`：
> 1. **会议邀请**：将姓名/手机号/邮箱解析为 `open_id`，用于 `schedule_meeting` / `update_meeting` / `meeting_invitees_add` / `meeting_invitees_replace` 的受邀人入参
> 2. **呼叫成员入会**：将姓名/手机号/邮箱解析为 `open_id`，用于 `meeting_control_call` 的 `users` 入参
>
> **严禁在其他场景下调用通讯录工具**，包括但不限于：仅为查看某人部门/职位/联系方式、好奇某人信息、为通用人员搜索目的、为踢人提供 open_id（应改用 `get_meeting_participants`，详见 `SKILL.md` 场景12）、**将 `open_id` 反查为姓名**（通讯录工具仅支持姓名/手机号/邮箱 → `open_id` 的正向查询，**不支持** `open_id` 反查；姓名回填应使用 `get_meeting_invitees` / `get_meeting_participants` 响应中自带的 `user_name`）。**不得将通讯录作为通用人员信息查询接口使用**。
>
> **隐私要点**：手机号、邮箱属于强敏感字段，调用前禁止在对话/日志中复述明文；返回的成员姓名向用户展示时优先使用昵称，必要时按 `privacy_policy.md` 脱敏。

### `contact_search` — 搜索企业通讯录

参数以 MCP schema 为准（`tools/list`）；`username` 必填，同名较多时叠加 `department_name` / `job_title` 过滤后再次调用。

#### 返回字段说明

| 字段 | 说明 |
|------|------|
| `members[].open_id` | 成员 open_id（用于后续工具） |
| `members[].user_name` | 用户名（向用户展示时按隐私规则处理） |
| `members[].job_title` | 职位 |
| `members[].department` | 部门 |

> **唯一命中的返回特性**：当搜索结果**只有一条**时，工具仅返回该成员的 `open_id` 字段，**不会返回 `user_name` / `job_title` / `department` 等其他成员信息**。模型可直接将该 `open_id` 用于后续工具（如会议邀请、呼叫入会），**无需也无法**基于该响应向用户展示部门/职位等字段；**严禁基于该响应伪造部门/职位信息**。

#### 使用约束（强约束）

- **`username` 为必填**：缺失会直接报错，**严禁模型自行猜测/编造姓名**，必须先与用户确认要查找的用户名后再调用。
- **结果较多时建议追加过滤**：仅按 `username` 命中较多（如同名）时，应建议用户补充 `job_title` 或 `department_name` 进一步过滤后**再次调用** `contact_search`，提升匹配精准度。
- **多结果必须由用户确认（强约束）**：命中多人时**必须向用户展示候选列表（昵称 + 职位 / 部门）由用户选择**，**严禁**模型基于职位、部门、入职时间、匹配度等任何维度**自行选择**某一条继续后续操作。**即便其中某条结果看起来"明显更匹配"，也必须等待用户明确指定，不得跳过该步骤**。
- **隐私展示白名单**：向用户展示时**仅允许**「姓名（昵称）/ 部门 / 职位」三类字段，**严禁擅自展示工号、手机号、邮箱、`open_id`、`userid`、`ms_open_id` 等任何其他敏感字段**。

---

### `contact_lookup_by_phone` — 按手机号批量查找用户

参数以 MCP schema 为准（`tools/list`）。

#### 注意事项

- `phones` 单次最多 50 个，超限时分批查询，**禁止自行截断**
- 手机号格式非法 / 用户不存在 / 跨企业 时按 `error_dictionary.md` 指引处理

---

### `contact_lookup_by_email` — 按邮箱批量查找用户

参数以 MCP schema 为准（`tools/list`）。

#### 注意事项

- `emails` 单次最多 50 个，超限时分批查询，**禁止自行截断**

---

## 受邀人管理

> 仅**会议主持人**可操作；`remove` / `replace` 属于不可逆操作，调用前**必须**先调 `get_meeting_invitees` 向用户展示当前列表并获得明确同意（详见 SKILL.md 场景11）。

### `meeting_invitees_add` — 添加受邀人

参数以 MCP schema 为准（`tools/list`）。

#### 注意事项

- `invitees` 为 open_id 数组，单次最多 100 个
- 仅增量添加，已在受邀列表中的成员会被去重忽略

---

### `meeting_invitees_remove` — 移除受邀人

参数以 MCP schema 为准（`tools/list`）。

#### 注意事项

- 移除操作不可逆，调用前必须二次确认
- 不在受邀列表中的 open_id 会被忽略

---

### `meeting_invitees_replace` — 替换受邀人

#### 调用示例

```bash
# 整体替换为新列表
python3 scripts/tencent_meeting.py tools/call '{
  "name": "meeting_invitees_replace",
  "arguments": {
    "meeting_id": "xxx",
    "invitees": ["open_id_xxx", "open_id_yyy"]
  }
}'

# 清空所有受邀人（传空数组）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "meeting_invitees_replace",
  "arguments": {
    "meeting_id": "xxx",
    "invitees": []
  }
}'
```

#### 注意事项

- **`invitees` 传空数组将清空所有受邀人**，必须向用户强调影响并获得明确同意
- 整体覆盖语义，不在新列表中的原受邀人会被全部移除
- 局部替换场景（"用 X 换掉 Y"）**推荐使用 `remove` + `add` 组合**，避免误清空其他成员

---

## 会中控制

> 会中控制工具用于**进行中**的会议；`meeting_control_kick` 是破坏性操作，必须严格二次确认（详见 SKILL.md 场景12）。

### `meeting_control_call` — 呼叫成员入会

参数以 MCP schema 为准（`tools/list`）。

#### 注意事项

- `users` 为 open_id 数组，单次最多 20 个
- 被叫方占线 / 用户已在会中 / 无呼叫权限等错误按 `error_dictionary.md` 指引处理

---

### `meeting_control_kick` — 踢出会议成员

#### 调用示例

```bash
# 三类成员可同时传（users / sip_users / pstn_users 至少一个非空，三者总数 ≤ 20）
# 具体分桶规则见下方"字段路由表"，按 get_meeting_participants 返回的 instanceid 判定
python3 scripts/tencent_meeting.py tools/call '{
  "name": "meeting_control_kick",
  "arguments": {
    "meeting_id": "xxx",
    "users": ["open_id_aaa"],
    "sip_users": ["ms_open_id_sip_yyy"],
    "pstn_users": ["ms_open_id_pstn_xxx"],
    "allow_rejoin": true
  }
}'
```

#### 注意事项

- `users` / `sip_users` / `pstn_users` **至少一个非空**，三者总数 **≤ 20**
- `allow_rejoin` 传`false`（不允许重新加入）；传 `true` 表示**允许**被踢者重新加入。必须向用户明确确认取值
- 不允许踢自己；非主持人 / 联席主持人无权限
- 调用前必须向用户**完整展示**被踢名单 + `allow_rejoin` 值并获得明确同意

#### 字段路由表（强制，按 `get_meeting_participants` 返回的 `instanceid` 判定）

| `instanceid` | 入参字段 | 使用的 id 字段 |
|---|---|---|
| `PSTN`（电话入会） | `pstn_users` | **`ms_open_id`** |
| `SIP`（SIP 设备） | `sip_users` | **`ms_open_id`** |
| 其他（`Mac` / `Windows` / `iOS` / `Android` / `Web` 等） | `users` | **`open_id`** |

**硬规则（跨多轮会话同样适用，禁止凭印象/惯性分类）**：

- ⛔ 凡 `instanceid ∈ {PSTN, SIP}` 的成员，**严禁放入 `users`**，必须走 `pstn_users` / `sip_users` 并使用 `ms_open_id`。
- ⛔ 凡某成员 **`open_id` 为空字符串**（PSTN/SIP 入会成员的典型特征），**必属** PSTN/SIP，**严禁放入 `users`**。
- ⛔ **禁止默认把所有人塞进 `users`**：每个待踢成员都必须逐一查 `instanceid` 后再分桶。

**分桶前数据清洗**：

- **过滤历史记录**：剔除 `get_meeting_participants` 中 `left_time != null` 的已离会条目，仅对在会成员操作。
- **跳过空 id**：建立 id 索引时，`open_id` / `ms_open_id` 为空串的键一律跳过。
- **去重**：同一成员可能出现多条记录，按 id 去重。

> ⚠️ **常见失败根因**：把 PSTN/SIP 成员误塞进 `users`、或对其使用了空的 `open_id`，会导致服务端**静默踢出失败**（接口返回成功但成员仍在会）。务必严格按上表核对 `instanceid` 与 id 字段。

---

## 反馈

### `submit_feedback` — 提交反馈（Agent 意见箱）

> 由 Agent 调用，用于上报工具缺失、工具报错、能力不足、结果异常或改进建议。
> 参数定义、枚举、长度限制、关联字段条件必填等约束以工具自身的 MCP schema 为准（通过 `tools/list` 获取）；调用时机与输出规范详见 SKILL.md 场景8。

#### 调用示例

```bash
# 1. 找不到对应工具
python3 scripts/tencent_meeting.py tools/call '{
  "name": "submit_feedback",
  "arguments": {
    "category": "tool_not_found",
    "intent": "订阅会议变更事件，实时感知会议被修改/取消"
  }
}'

# 2. 调用工具报错
python3 scripts/tencent_meeting.py tools/call '{
  "name": "submit_feedback",
  "arguments": {
    "category": "tool_error",
    "intent": "查询某场会议的参会成员明细",
    "actions_tried": "调用 get_meeting_participants(meeting_id=xxx)",
    "result": "返回 9042 无权限",
    "tool_name": "get_meeting_participants",
    "error_code": "9042"
  }
}'

# 3. 工具能力/参数不足
python3 scripts/tencent_meeting.py tools/call '{
  "name": "submit_feedback",
  "arguments": {
    "category": "tool_inadequate",
    "intent": "按时间范围筛选用户的未来会议列表",
    "actions_tried": "调用 get_user_meetings",
    "result": "工具不支持 start_time / end_time 参数，无法按时间过滤",
    "tool_name": "get_user_meetings"
  }
}'

# 4. 结果不符预期
python3 scripts/tencent_meeting.py tools/call '{
  "name": "submit_feedback",
  "arguments": {
    "category": "unexpected_result",
    "intent": "获取某场会议的智能纪要",
    "actions_tried": "调用 get_smart_minutes(record_file_id=xxx)",
    "result": "返回空内容，但用户确认会议已生成纪要",
    "tool_name": "get_smart_minutes"
  }
}'

# 5. 一般性建议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "submit_feedback",
  "arguments": {
    "category": "suggestion",
    "intent": "希望 get_records_list 支持按会议主题模糊搜索"
  }
}'
```

---

## 相关文档

- **SKILL.md** — 完整的业务规范与触发场景（时间处理、敏感操作、错误处理等通用规则以 SKILL.md 为准）
- **error_dictionary.md** — 错误处理指引
- **version_management.md** — 版本管理指引