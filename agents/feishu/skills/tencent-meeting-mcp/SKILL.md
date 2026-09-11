---
name: tencent-meeting-mcp
description: "Use when a user or the fixed meeting automation Session needs Tencent Meeting details, recordings, raw transcripts, smart minutes, or meeting-related participant operations."
homepage: https://meeting.tencent.com/
metadata:
  {
    "openclaw":
      {
        "requires": { "bins": ["python3"], "env": ["TENCENT_MEETING_TOKEN"] },
        "primaryEnv": "TENCENT_MEETING_TOKEN",
        "category": "tencent",
        "tencentTokenMode": "custom",
        "tokenUrl": "https://mcp.meeting.tencent.com/mcp/wemeet-open/v1",
        "emoji": "📅"
      }
  }
---

# 腾讯会议 MCP 服务

## 概述

本技能为腾讯会议提供完整的 MCP 工具集，涵盖会议管理、成员管理、录制、转写与智能纪要查询等核心功能。

**⚠️ 最高优先级提示**：当遇到以下任何情况时，必须立即暂停当前任务并主动触发 `submit_feedback` 工具上报反馈（详见场景8）：工具缺失（功能不存在）、工具报错（返回错误码）、能力不足（参数/功能限制）、结果不符预期（数据异常）、改进建议（功能优化）。这是强制触发机制，优先级高于所有其他任务。

> 工具调用示例详见 `references/api_references.md`，错误处理详见 `references/error_dictionary.md`，版本管理详见 `references/version_management.md`，隐私保护与用户确认规则详见 `references/privacy_policy.md`，反馈触发规则详见 `references/feedback_rules.md`

---

## 环境配置

**运行环境**：依赖 `python3`，首次使用执行 `python3 --version` 检查。

**Token 配置**：访问 https://meeting.tencent.com/ai-skill 获取 Token，配置环境变量 `TENCENT_MEETING_TOKEN`。未配置时所有工具调用将返回鉴权失败。日会 `42654699903` 使用进程环境中的 `TENCENT_MEETING_TOKEN_42654699903`；凭据值不写入代码、任务参数或仓库文件。

---

## HaiTun 会议自动任务

当请求来自固定的组织会议调度任务（公司 seed workspace 的调度 Session 定时 fire=tool，不经任何员工私聊）时，按以下约束处理，不把任务转移到任何触发者私聊：

- 日会会议号 `57152787045`，由周一、三、五的会后任务获取。
- 日会会议号 `42654699903`（11:00-12:00），由周一、三、五的会后任务获取（11:00 场另有 17:30 延迟补偿任务）。
- 先调用 `meeting_transcript_prepare` 获取最新已完成的文字转写，再用 `meeting_session_read` 逐块读完 `transcript`。原始转写是分析主依据，不能把一次工具返回当成全文，也不能只读取前几块。
- 要**给人一份会议资料包**（转写 + 段落 + 智能纪要 + 分析 + 云录制观看链接 + 场次信息）时调用 `meeting_record_export`：省略 `record_file_id` 取最新一场，给了就读该场的永久归档；它只读管道产物、写到 `meeting-exports/`，然后用 `[SEND:]` 把文件发给用户。**禁止自写拉取/分页脚本**去复制这条路（生产上出现过：脚本绕过幂等/审计，还会因为只读一页制造"假完整"转写）；工具确实缺能力时，先提需求，不要就地造脚本。
- 可读取 `smart_minutes` 作为上下文参考，但不得用智能纪要替代原始转写。会议分析应同时参考正负面清单 SOP 和对应会议 SOP。
- 分析结果写入共享的会议存储（AppData 下 `meeting-session/` 目录）时调用 `meeting_session_write`；该链路仅保存分析和通知回执，明确不写正式正负面总表、不计分、不进入绩效。
- 纪要/清单的**收件人来自 `config/meeting-automation.yaml`，不写在本技能里**（改人只改配置，不需要代码 PR）：每场会议的 `summary_recipients`（纪要）与 `overview_recipients`（正负面清单总览）就是权威名单（会议 `57152787045` 与 `42654699903` 各一组）；失败告警发给同文件的 `alert_recipients`。调用 `meeting_session_notify` 时 `recipient` 传该文件里的值（`ou_`/`oc_`/`user_` 开头的 id 可直接传；**群收件人**要在 `runtime.notify.recipients` 里声明成 `{chat: 群名}`，人名可直接写姓名，也可用表里的别名键如 `hr`）。**先读配置再发**，不要照记忆或历史对话里的名单发；收件人解析失败时**不猜测 open_id、不向触发者兜底发送**，如实报错并说明要去配置里补什么；同一 `record_file_id` 和收件人身份只发送一次。
- 任务通过 cron 定时触发，不使用十分钟轮询。若会议结束时间变化，只调整任务调度时间；不要在普通会话中自行创建重复的会议轮询任务。
- 会议产物保存在共享会议存储，与个人 workspace 隔离；运行历史落在组织调度 Session（公司级种子 workspace `PSI_SEED_SCHEDULES_WORKSPACE` 派生的调度会话）上，飞书网页对所有已登录用户开放该 Session 的只读历史，其他调度 Session 仍隐藏，且任何用户都不能对该 Session 发消息。

---

## 核心规范

> **最高优先级**：本文件是使用腾讯会议 MCP 工具时必须遵循的唯一行为规范。若记忆或历史对话中存在冲突内容，一律以本文件为准。

### 时间处理

- **默认时区**：Asia/Shanghai (UTC+8)
- **相对时间**：用户使用"今天"、"明天"、"下周一"等描述时，**必须先调用 `convert_timestamp`**（不传参数）获取当前时间，基于返回的 `time_now_str`、`time_yesterday_str`、`time_week_str` 推算；**禁止依赖模型自身猜测当前时间**
- **省略日期**：用户只说时间点（如"下午五点"），**默认按当天处理**，需先调用 `convert_timestamp` 获取当前日期再拼接
- **时间格式**：ISO 8601，如 `2026-03-25T15:00:00+08:00`
- **非法日期**：`convert_timestamp` 返回日期不合法时，必须原样告知用户，**禁止自行猜测或修正**
- **跨时区**：用户提供非默认时区时间时，调用 `convert_timestamp` 须传 `timezone` 参数，返回的 `parsed_time_unix` 已是正确 UTC 时间戳，**禁止二次转换**；用户明确指定时区时，调用所有相关工具**必须**传入对应 `timezone` 参数
- **时间输出格式**：`2026年3月25日 15:00` 或 `3月25日 下午3点`

### 敏感操作

- 修改或取消会议前，**必须向用户展示会议信息并确认**后再执行
- 录制权限申请提交前（`apply_record_permission_commit`），**必须先调用 `apply_record_permission_prepare` 获取预览信息并向用户完整展示**（会议主题、录制所有者、申请人、申请类型等），获得用户明确同意后再调用 commit 工具；详见场景9
- 提交反馈（`submit_feedback`）前，**必须按场景8的二次确认流程获得用户明文同意**后再调用；反馈内容**严禁包含未脱敏的隐私信息**，详见 `references/privacy_policy.md`
- 受邀人变更（`meeting_invitees_remove` / `meeting_invitees_replace`）前，**必须先调用 `get_meeting_invitees` 展示当前受邀人列表**，明确告知用户将被移除/替换的成员，获得明确同意后再执行；`replace` 传空数组会清空所有受邀人，**必须二次强调**；详见场景11
- 会中踢人（`meeting_control_kick`）前，**必须向用户完整展示被踢成员名单与 `allow_rejoin` 值**（true=允许重新加入 / false=禁止重新加入），获得明确同意后再执行；详见场景12
- 会中呼叫（`meeting_control_call`）涉及对成员发起电话/客户端呼叫，**必须向用户展示被呼叫成员名单并确认**后再执行；详见场景12
- 调用这些通讯录工具前，**必须先完成意图判定**： `contact_search` / `contact_lookup_by_phone` / `contact_lookup_by_email` 这些通讯录工具前，必须先在内部完成意图判定并满足以下全部条件，否则一律禁止调用：
  1. 本轮请求**显式包含**邀请入会 / 添加受邀人 / 呼叫入会动作；
  2. 解析出的 open_id 将**在同一轮内立即**喂给 schedule_meeting / update_meeting / meeting_invitees_* / meeting_control_call等工具。
  只要用户说的是"查找 / 搜索 / 查一下 / 看看 / 是谁 / 哪个部门 / 联系方式"等
  **纯查询表述且不伴随上述动作**，立即停止，使用固定拒绝话术（见场景10反例），
  **严禁调用任何 contact_* 工具**。
- 通讯录工具入参中的手机号、邮箱属于强敏感信息，**禁止在对话或日志中复述明文**；返回的成员姓名向用户展示时优先使用昵称，必要时按 `references/privacy_policy.md` 脱敏
- 无法查询到会议时，先确认会议号正确性或是否为本人创建

### 追踪信息

所有工具返回的 `X-Tc-Trace` 或 `rpcUuid` 字段，**必须明确展示**给用户

### 错误处理

- 工具调用失败或返回错误时，**必须查阅 `references/error_dictionary.md`** 并按对应指引处理
- 若错误字典中**未收录**该错误，或按指引处理后仍无法满足用户诉求，**必须立即通过 `submit_feedback` 上报**（详见场景8的强制触发机制）

### 客户端环境标识

调用每个工具时，必须在 arguments 中附带 `_client_info` 对象（`os`、`agent`、`model`）。此参数由模型自动填入，**不需要向用户询问**

### 版本管理

- MCP 响应中出现版本相关提示时，**必须查阅 `references/version_management.md`** 并按对应指引处理
- `check_skill_version` 触发场景：用户询问新版本、疑似已知问题、MCP 响应提示有可更新版本；更新后建议重新开始对话，确保新版本规则生效

---

## 不触发场景

腾讯文档、通用日程、即时通讯、企业微信审批/打卡、电话/PSTN、视频剪辑、其他会议平台（Zoom/Teams/飞书/钉钉）

---

## 通用规则

1. **Meeting Code 转换**：用户提供的会议号需通过 `get_meeting_by_code` 转换为 meeting_id 后才能调用其他工具
2. **用户标识前置（仅限邀请/呼叫场景）**：**当且仅当**用户已表达明确的「会议邀请 / 添加受邀人 / 会中呼叫入会」意图、却只提供了姓名/手机号/邮箱（未提供 open_id）时，才调用 `contact_search` / `contact_lookup_by_phone` / `contact_lookup_by_email` 解析出 open_id，并**立即**用于 `schedule_meeting` / `update_meeting` / `meeting_invitees_*` / `meeting_control_call`。
   ⛔ 前置判定：若本轮无"邀请/添加受邀人/呼叫入会"动作，直接禁止调用 contact_*，
   - ⛔ **纯人员查询不适用本规则**：用户只是想"搜一下/查一下某人是谁、看其部门/职位/联系方式"而无任何后续会议动作时，**禁止调用任何 `contact_*` 工具**，应直接告知"查询通讯录人员信息不在本服务范围内"。
   - ⛔ **踢人不适用本规则**：踢人所需的 `open_id` / `ms_open_id` 一律取自 `get_meeting_participants`，**严禁**用 `contact_*` 反查（详见场景12）。
3. **年份默认值**：未指定年份时使用当前年份，禁止使用过去年份
4. **参数格式错误**：提示用户修改，**禁止主动修改用户输入的参数值**
5. **分页查询**：统一使用 `page_token`/`page_size` 分页，根据 `has_more` 判断是否继续，为 `true` 时用 `next_page_token` 翻页
6. **返回昵称优先**：返回主持人、参会者、受邀人时，若无特殊要求只返回用户昵称，不返回用户 ID
7. **批量上限**：`invitees` ≤ 100；`meeting_control_call.users` ≤ 20；`meeting_control_kick.users + sip_users + pstn_users` 三者总数 ≤ 20；`contact_lookup_by_phone.phones` / `contact_lookup_by_email.emails` ≤ 50。超限时必须告知用户分批处理，**禁止自行截断**

---

## 业务场景

### 场景1：创建会议

**触发条件**
用户要求预约、创建、安排一场腾讯会议

**处理流程**
1. 调用 `convert_timestamp` 获取当前时间（涉及相对时间时）
2. 确认必填信息：会议主题、开始时间、结束时间
3. 若用户提到邀请成员（仅有姓名/手机号/邮箱），先调用 `contact_search` / `contact_lookup_by_phone` / `contact_lookup_by_email` 获取 open_id 列表
4. 调用 `schedule_meeting` 创建会议，可选传入 `invitees`（open_id 数组，最多 100）

**注意事项**
- 未提及结束时间默认 1 小时，提示用户可修改
- 周期性会议重复次数默认 50 次，提示用户可修改
- 缺少会议主题时工具直接报错，必须提示用户输入
- 创建时若传入 `invitees`，必须先向用户确认邀请名单（昵称展示）后再执行
- 创建后如需追加/修改受邀人，使用 `meeting_invitees_add` / `meeting_invitees_remove` / `meeting_invitees_replace`

**输出规范**
展示创建成功的会议主题、时间、会议号、受邀人数（若有）及追踪信息

---

### 场景2：修改会议

**触发条件**
用户要求修改、更新已有会议信息（含受邀人增删替换）

**处理流程**
1. 若用户提供会议号，先调用 `get_meeting_by_code` 获取 meeting_id
2. 调用 `get_meeting` 查询当前会议信息；如需修改受邀人，加调 `get_meeting_invitees` 查看当前列表
3. 若用户提到邀请成员（仅有姓名/手机号/邮箱），先调用 `contact_search` / `contact_lookup_by_phone` / `contact_lookup_by_email` 获取 open_id 列表
4. 向用户展示**完整变更摘要**（含基础信息变更 + 受邀人变更），确认后调用 `update_meeting` 执行修改

**注意事项**
- 修改前必须二次确认（见核心规范"敏感操作"）
- 可修改：主题、时间、密码、时区、会议类型、入会限制、等候室、周期性规则、受邀人等
- 若同时修改会议信息和受邀人，需在 `update_meeting` 中传入 `invitees` + `invitees_operate_type`（字符串枚举：`"add"`=增量添加 / `"remove"`=批量移除 / `"replace"`=整体替换），并在确认摘要中**一次性展示所有变更**
- `invitees` 与 `invitees_operate_type` **必须成对出现**，缺一报参数错误
- 仅做受邀人变更（无其他字段修改）时，**推荐使用** `meeting_invitees_add` / `_remove` / `_replace` 专用工具，语义更清晰

**输出规范**
展示修改后的会议信息（含受邀人变化）及追踪信息，提示用户确认变更

---

### 场景3：取消会议

**触发条件**
用户要求取消、删除已有会议

**处理流程**
1. 若用户提供会议号，先调用 `get_meeting_by_code` 获取 meeting_id
2. 调用 `get_meeting` 查询当前会议信息
3. 向用户展示待取消信息，确认后调用 `cancel_meeting` 执行取消

**注意事项**
- 取消前必须二次确认（见核心规范"敏感操作"）

**输出规范**
展示取消成功提示及追踪信息

---

### 场景4：查询会议信息

**触发条件**
用户要求查看会议详情、参会人员、受邀成员、等候室成员、会议录制信息等

**处理流程**
- 有 meeting_id → 直接调用 `get_meeting`
- 有会议号 → `get_meeting_by_code` → `get_meeting`
- 查看参会人员 → `get_meeting_participants`
- 查看受邀成员 → `get_meeting_invitees`
- 查看等候室成员 → `get_waiting_room`
- 导出参会成员统计（含累计参会时长、会议互动行为等统计） → `export_participants` → 获取 `job_id` → `get_job_result` 查询下载链接

**注意事项**
- `export_participants` 为异步导出，返回 `job_id`，需通过 `get_job_result` 轮询任务状态
- `get_job_result` 返回 status：1-成功（可获取下载链接，有效期2小时）、2-失败（查看 error_msg）、3-处理中（稍后重试）
- 导出适用于需要完整参会统计数据（如考勤分析）的场景，普通查看参会人员使用 `get_meeting_participants` 即可

**输出规范**
展示会议基本信息、人员列表、会议录制信息等，附带追踪信息

---

### 场景5：会中控制

**触发条件**
用户要求管理会中等候室成员

**处理流程**
- 管理会中等候室成员 → `manage_waiting_room`

**输出规范**
展示操作结果，附带追踪信息

---

### 场景6：查询用户会议列表

**触发条件**
用户要求查看自己的会议列表、近期会议、我的会议

**处理流程**
1. **参数判断逻辑**：
   - 当提供的参数**只有时间**（如start_time/end_time）：**列表优先**
     - 进行中/未开始：调用 `get_user_meetings`
     - 已结束：调用 `get_user_ended_meetings`
   - 有其他参数（会议主题、会议号、创建人、参与者等）：**搜索优先**
     - 调用 `search_meetings` 进行精确过滤
2. 查询今天的全部会议：**同时调用两者，结果聚合去重**

**注意事项**
- `get_user_meetings` 仅包含未开始/进行中的会议，`get_user_ended_meetings` 仅包含已结束会议
- `search_meetings` 支持按关键词(q)、搜索字段(q_fields)、会议号、日期窗口等过滤，数据按分页返回
- `search_meetings` 的 `from`/`to` 参数使用 ISO 8601 格式（如 `2026-03-20T00:00:00+08:00`），按用户输入原值透传

**输出规范**
按时间排列展示会议列表（包含录制信息等），标注状态（未开始/进行中/已结束）

---

### 场景7：查询录制与转写

**触发条件**
用户要求查看录制、转写内容、搜索关键词、获取智能纪要（录制纪要）

> ⚠️ **纪要类型判定前置**：用户说"查纪要/会议总结/会议纪要/会议要点"时，**必须先阅读下方「元宝纪要与录制纪要的边界规则」判断走哪条链路**，不得仅凭命令名字面匹配。若确定走元宝纪要链路，请转至场景7A处理。

**处理流程**
1. 根据用户意图选择获取录制信息的途径：
    - **查指定会议的录制（含权限状态）**：使用会议查询工具，响应中每个会议携带 `records` 数组（含 `meeting_record_id` / `state` / `permission_status` 等）。按以下方式定位具体会议：
        - 按会议主题或主持人搜索 → `search_meetings`
        - 有会议号 → `get_meeting_by_code`（按会议号精确查）
        - 有 meeting_id → `get_meeting`（按会议ID精确查）
        - 按时间查近期会议 → `get_user_meetings`（未开始/进行中）或 `get_user_ended_meetings`（已结束）
    - **指定关键词查录制**：用户给出主题、创建者、内容等关键词想搜索匹配的录制 → `search_records`
    - **未指定某场会议、想找自己的录制**：优先用 `get_records_list`（按时间范围或默认查，仅返回用户有权限的录制文件）
2. 根据需求选择后续操作：
 - 获取播放录制 → `get_record_addresses`
   - 转写全文 → `get_transcripts_paragraphs` 获取段落 ID → `get_transcripts_details` 获取文本
   - 搜索关键词 → `search_transcripts`
   - 智能纪要（录制纪要） → `get_smart_minutes`

当后续任务需要依据会议发言原文做正负面清单或其他行为核对时，必须优先获取并保留完整原始转写；不得用
`get_smart_minutes` 的摘要替代原始文本。智能纪要只能作为用户明确要求的补充视图。需要把原始转写发到
飞书群话题时，使用 `tencent_meeting_minutes_publish`，由调用方提供真实的 topic-enabled `chat_id`；该工具
只发送原文，不改写、不自动分析、不自动写入清单。

**注意事项**
- 获取录制会议内容时，若后续要做正负面清单或其他行为核对，优先使用完整原始转写（`get_transcripts_paragraphs` → `get_transcripts_details`）；不得用智能纪要摘要替代原文。只有用户明确要求 AI 智能纪要时才调用 `get_smart_minutes`。播放地址仅用于用户明确要求查看录制时使用。
- `get_records_list` 未传 meeting_id/meeting_code 时，`start_time` 和 `end_time` 必须同时传入
- `get_records_list` 按时间查询：范围不超过 31 天，起始不早于 1 年前
- `search_records` 支持按关键词(q)、搜索字段(q_fields)、会议ID/会议号、日期窗口、文件类型等过滤，数据按分页返回
- `search_records` 的 `from`/`to` 参数使用 ISO 8601 格式（如 `2026-03-20T00:00:00+08:00`），按用户输入原值透传
- `search_transcripts` 中文关键词需 urlencode
- **录制状态限制**：`state` 为「录制中」或「转码中」的录制文件**不可查看内容、不可申请权限**（场景6的播放/转写/搜索/纪要、场景8的权限申请均不可对这类文件执行）。遇到此类状态时，应告知用户该录制尚未就绪、需等待变为「转码完成」后再操作

**输出规范**
展示录制列表/播放地址/转写内容/智能纪要，附带追踪信息

---

### 元宝纪要与录制纪要的边界规则

> **CRITICAL — 取纪要前必读**：遇到"查纪要/会议总结/会议纪要/会议要点"等诉求时，必须先按本规则判断走元宝纪要（场景7A）还是录制纪要（场景6），不得仅凭命令名字面匹配。

#### 两条链路对比

| 维度 | 元宝纪要（`search_minutes` / `get_minutes`） | 录制纪要（`get_smart_minutes` / `search_records` / `get_transcripts_*`） |
|------|------|------|
| 纪要产物 | 完整总结（overview/summary_points/todos）+ 滚动总结（short_summaries） | 智能纪要（基于录制文件的 AI 总结）+ 逐字稿 |
| 生成时机 | 会中（用户开启元宝纪要） | 会后（基于云录制/文字转写/录音/文件上传） |
| 所有权 | 参会者每人一份 | 录制创建者所有 |
| 权限 | 参会者人人可取 | 需录制查看权限，无权限需申请 |
| 链接 | 无（只能取内容） | 有（播放地址） |
| 内容一致性 | 因入会时段不同，各人取到的内容可能不一致 | 基于完整录制文件，多人共享相同内容 |
| 逐字稿 | 无（short_summaries 是 AI 加工版，非原话） | 有（`get_transcripts_details`，含说话人/时间戳） |

#### 纪要选择规则

| 场景 | 选择 |
|------|------|
| 只存在一类纪要 | 取存在的那一类 |
| 两类都存在、用户明确指定（如"取录制纪要""看元宝纪要"） | 按用户指定走对应链路 |
| 两类都存在、用户未指定 | 先查 `get_smart_minutes`（录制纪要）或 `get_minutes`（元宝纪要）任一均可；先查的一种取不到时，继续查另一种（双向兜底）。取到后告知用户纪要来源 |
| 两条链路都取不到 | 告知用户该会议无可取的纪要 |

#### 原话/逐字稿降级规则

用户要"原话/逐字稿/谁说了什么"时：
- **有录制权限** → 走录制链路 `get_transcripts_paragraphs` + `get_transcripts_details` 取逐字稿
- **无录制权限但元宝纪要可用** → 降级取元宝 `get_minutes` 的 `short_summary`（滚动总结），**必须标注"非原话/AI 加工版"**，无需先询问用户是否接受降级

#### 两条链路的独立性

- 元宝纪要与录制纪要**相互独立、互不影响**
- 一个用户在一场会议后可同时拥有两类纪要，也可能只有其中一类，也可能都没有
- 当两者都存在时，内容可能重叠，各自基于不同数据源（会中 ASR vs 录制文件）

---

### 场景7A：查询元宝纪要

**触发条件**
用户要求搜索或查询元宝纪要、会议总结/要点/待办（经边界规则判定走元宝链路）

> **领域边界**：元宝纪要与录制纪要（`get_smart_minutes`）是两条独立链路。取纪要前请先阅读上方「元宝纪要与录制纪要的边界规则」判断走哪条链路。

> **当前仅支持会后获取**：元宝纪要在会中生成，但当前工具仅支持取已结束会议的纪要。会议进行中或未开始时，告知用户会后重试。

**元宝纪要查询路由**

用户说"查纪要/会议总结/会议纪要/会议要点"且确定走元宝链路后，按用户线索选择工具：

| 用户线索 | 入口工具 | 说明 |
|---------|---------|------|
| 会议号 / 会议 ID | `get_minutes`（传 `meeting_code` 或 `meeting_id`） | 取该会议的元宝纪要 |
| 已有 `minute_id` | `get_minutes`（传 `minute_id`） | 取单份完整纪要（含滚动总结） |
| 纪要内容关键词（无会议号、记得会上说过什么） | `search_minutes`（传 `q`） | 跨会议搜纪要文本 |
| 主题 / 创建人 / 时间范围（无会议号） | 先 `search_meetings` / `get_user_ended_meetings` 定位会议，再 `get_minutes` | 元宝无按主题/创建人直接搜纪要的能力 |
| 要"原话/逐字稿/谁说了什么" | 元宝无逐字稿，走录制链路（场景6）；无录制权限时降级取 `get_minutes` 的 `short_summary`，标注"非原话/AI 加工版" | 见边界规则「原话/逐字稿降级规则」 |

**处理流程**
1. **判断纪要类型**：按「元宝纪要与录制纪要的边界规则」确定走元宝链路
2. **定位会议**（若用户给会议号/会议 ID 可跳过）：
   - 会议号 → 直接传 `get_minutes` 的 `meeting_code`
   - 会议 ID → 直接传 `get_minutes` 的 `meeting_id`
   - 主题/创建人 → `search_meetings` 拿 meeting_id
   - 时间范围 → `get_user_ended_meetings` 拿 meeting_id
   - 纪要内容关键词（无会议号）→ `search_minutes`
3. **取纪要**：
   - 取一场会议的完整总结 → `get_minutes`（传 `meeting_code` 或 `meeting_id`）
   - 取单份完整纪要（含滚动总结）→ `get_minutes`（传 `minute_id`）
   - 单独取滚动总结 → `get_minutes`（传 `minute_id` + `short_summary=true`）
4. **兜底**：元宝纪要无结果时，询问用户是否尝试查找该会议的录制纪要（`get_smart_minutes`）

**纪要搜索路由策略（用户说「搜索纪要」「找纪要」「查纪要内容」时的路由规则）**

> 用户表达「搜索纪要」「找一下纪要」「纪要里有没有提到 XXX」等搜索类诉求时，按以下策略路由：

| 步骤 | 判断条件 | 动作 |
|------|---------|------|
| ① 检查录制权限 | 先通过上下文判断用户是否有录制权限（如此前调用录制相关工具是否成功、会议查询响应中 `records[].permission_status` 等） | 若无法判断，可先尝试调用 `search_records` 探测权限 |
| ② 有录制权限 | 用户 query 为「搜索纪要」「查纪要」等通用纪要搜索 | **优先走录制链路**：调用 `search_records`（`q_fields=smart_minutes`）搜录制纪要；录制纪要无结果时，再降级调用 `search_minutes` 搜元宝纪要 |
| ③ 无录制权限 | 用户 query 为通用纪要搜索 | **直接走元宝链路**：调用 `search_minutes` 搜元宝纪要 |
| ④ 细节/原话类问题 | 用户问的是细节问题（如「谁说要延期」「有没有人提到预算」「XX 说了什么」），纪要搜索无结果或纪要粒度不够 | **直接走录制逐字稿**：调用 `search_records`（`q_fields=transcript_content`）或 `search_transcripts` 搜索转写原文，逐字稿能提供说话人+时间戳级别的精确匹配 |
| ⑤ 无录制权限 + 需要逐字稿 | 步骤④需要走录制链路但用户无权限 | **引导申请权限**：告知用户「该录制需要查看权限才能搜索逐字稿内容，是否需要申请录制权限？」，用户同意后进入场景9（录制权限申请流程） |

**路由示例**：
- 用户说「帮我搜一下上周纪要里有没有提到排期」→ 有录制权限时先 `search_records(q="排期", q_fields="smart_minutes")`，无结果再 `search_minutes(q="排期")`
- 用户说「张三在会上说要延期，帮我找一下」→ 这是细节/原话类问题，直接走录制逐字稿 `search_records(q="延期", q_fields="transcript_content")` 或 `search_transcripts`
- 用户无录制权限时说「搜一下纪要」→ 直接 `search_minutes` 搜元宝纪要
- 用户无录制权限时说「谁说了要延期」→ 告知需要录制权限才能查逐字稿，引导申请权限（场景9）

**搜索元宝纪要（`search_minutes`）**
- 支持按关键词(q)、时间窗口(from/to)过滤，所有过滤参数均为可选，可任意组合
- `from`/`to` 参数使用 ISO 8601 格式（如 `2026-03-20T00:00:00+08:00`），按用户输入原值透传
- 数据按分页返回，使用 `page_token`/`page_size` 分页
- **与 `search_records` 的区别**：`search_minutes` 搜元宝纪要文本（overview/summary_points/todos/short_summaries）；`search_records --q_fields transcript_content` 搜录制转写原文。两者检索范围不同，按用户要的纪要类型选择

**查询元宝纪要详情（`get_minutes`）**

支持两种模式：

- **稳态纪要**：传入 `meeting_id` 或 `meeting_code` 获取稳态纪要；`page_token` / `page_size` 控制分页，`page_size` 默认10，最大30；周期会议需配合 `sub_meeting_id` 定位实例
- **滚动（瞬态）纪要**：传入 `short_summary=true` 且**必须**同时传入 `minute_id`（稳态纪要唯一标识），否则报错；`page_token` / `page_size` 控制分页，`page_size` 默认100，最大300

**注意事项**
- `get_minutes` 获取滚动（瞬态）纪要时，**必须自动全量拉取**：首次调用后检查响应中的 `has_more` 字段，若为 `true` 则将响应中的 `next_page_token` 作为下一次请求的 `page_token` 继续拉取，循环直到 `has_more` 为 `false`，最终将所有分页结果聚合后一次性返回给用户
- `get_minutes` 入参识别规则：用户提供的会议标识字符串长度为 9~12 位时视为 `meeting_code`，更长的视为 `meeting_id`
- `get_minutes` 可选获取概览(overview)、要点(summary_points)、待办(todos)、滚动总结(short_summary)，默认获取概览/要点/待办
- **元宝纪要因人而异**：参会者因入会时段不同，取到的元宝纪要内容不同。同一会议的不同参会者可能拿到不同的纪要
- **元宝纪要无链接**：只能取内容，没有可分享的播放地址
- **元宝纪要无逐字稿**：`short_summaries` 是 AI 加工版，非原话

**输出规范**
展示元宝纪要内容（概览/要点/待办/滚动总结），附带追踪信息。取到纪要后应告知用户纪要来源为"元宝纪要"

---

### 场景7：提交反馈（Agent 意见箱）

**触发条件（强制触发 - 优先级最高）**
Agent 在执行腾讯会议相关任务过程中遇到以下任何情况时，**必须立即暂停当前任务**并主动询问用户是否调用 `submit_feedback` 上报反馈：
- **工具缺失**：用户请求的功能在当前工具集中完全不存在
- **工具报错**：任何工具调用返回非成功状态
- **能力不足**：工具存在但参数限制或功能范围无法满足用户需求
- **结果不符预期**：工具调用成功但返回结果与用户预期明显不一致
- **改进建议**：发现工具集有明显改进空间或新增功能需求

**强制触发机制**
- **立即暂停**：必须立即暂停当前任务流程
- **明确询问**：必须询问用户是否上报反馈
- **二次确认**：必须获得用户明确同意后才调用工具

**详细触发规则**：详见 `references/feedback_rules.md`

**输出规范**
上报成功后，向用户简要告知已记录该反馈（含 `feedback_id`）；用户拒绝或未确认时，告知用户"已取消反馈上报"。

---

### 场景9：录制权限申请

**触发条件**
- 用户主动请求"申请录制权限"、"申请查看录制"
- 调用录制相关工具（`get_record_addresses`/`get_transcripts_details`/`get_transcripts_paragraphs`/`search_transcripts`/`get_smart_minutes`）返回**录制权限不足**类错误（如"录制权限校验失败"、"无权限查看录制"）时，**自动**进入该流程

**处理流程（必须两步完成，严禁跳过预览步骤）**
1. 调用 `apply_record_permission_prepare` 获取申请预览信息（包含会议标题 `subject`、录制所有者 `file_owner`、申请人 `applicant`、申请类型 `approval_name` 等）
2. **向用户完整展示预览信息**，明确说明"即将向录制所有者发起录制权限申请"，**等待用户明确确认（如"同意"、"申请"、"确认提交"）**
3. 用户确认后调用 `apply_record_permission_commit` 正式提交申请
4. 提交成功后，向用户展示审批状态 `status`、审批说明 `message`，以及**审批链接 `approval_url`** 供用户跟进审批进度

**注意事项**
- `meeting_record_id` 为必填，应从上下文中获取，**严禁伪造**；来源优先级：会议查询响应中的 `records[].meeting_record_id`（场景4/5）> `get_records_list` 返回结果
- prepare 返回的 `expires_in` 表示预览有效期（秒），用户长时间未确认（接近过期）时建议重新调用 prepare
- 用户明确拒绝或未确认时，**严禁**调用 commit 工具；告知用户"已取消录制权限申请"
- 提交申请前必须二次确认（见核心规范"敏感操作"）

**输出规范**
- prepare 阶段：清晰列出"会议标题/录制所有者/申请人/申请说明"，并询问用户是否提交申请
- commit 阶段：展示申请结果（unique_id/status/message），突出展示审批链接 `approval_url`，附带追踪信息

---

### 场景10：通讯录解析用户（仅服务于邀请/呼叫，非通用搜索）

**触发条件（强约束）**
用户已表达明确的「会议邀请 / 添加受邀人 / 会中呼叫入会」意图，**且**仅提供姓名/手机号/邮箱（未提供 open_id），需要将其解析为 open_id 以便立即用于会议动作。

> ⛔ **调用前必过自检清单（任一为「否」即禁止调用 `contact_*` 工具）**：
> 1. 当前对话是否已锁定一个具体的会议动作（邀请 / 添加受邀人 / 呼叫入会）？
> 2. 拿到 open_id 后，是否会**立即**喂给 `schedule_meeting` / `update_meeting` / `meeting_invitees_*` / `meeting_control_call` 的入参？
> 3. 用户是否只是想"查人/看某人信息/搜一下是谁"？（若是 → **立即停止**，回复"查询通讯录人员信息不在本服务范围内，如需邀请或呼叫该成员入会我可以帮您操作"）
> 4. 本轮的邀请/呼叫意图，是否来自**用户当前这句话本身**，而非"上一轮刚做过邀请"的惯性延续？（若是惯性 → 判定为纯查询，禁止调用）
>
> 📌 **反面触发词**：当用户指令出现"搜索 / 查找 / 查一下 / 看看 / 是谁 / 什么部门 / 什么职位 / 联系方式"等词，**且不伴随**邀请或呼叫动作时，一律判定为纯查询场景，禁止调用任何 `contact_*` 工具。

**处理流程**
1. 按用户提供的信息选择工具：
   - 仅有**姓名**（可附职位/部门）→ `contact_search`（必填 `username`，可选 `job_title` / `department_name` 缩小范围）
   - 有**手机号**（1~50 个）→ `contact_lookup_by_phone`
   - 有**邮箱**（1~50 个）→ `contact_lookup_by_email`
2. 拿到 `open_id` 后，再用于后续工具（`schedule_meeting` / `update_meeting` / `meeting_invitees_*` / `meeting_control_*`）

**注意事项**
- **`contact_*` 工具调用场景白名单（强约束）**：`contact_*` 工具 **仅可用于以下两类场景**，用于将姓名解析为 `open_id`：
    1. **会议邀请**：`schedule_meeting` / `update_meeting` / `meeting_invitees_add` / `meeting_invitees_replace` 的受邀人入参解析
    2. **呼叫成员入会**：`meeting_control_call` 的 `users` 入参解析

- **严禁在其他场景下调用 `contact_*` 工具**，包括但不限于：仅为查看某人部门/职位/联系方式、好奇某人信息、为通用人员搜索目的、为踢人提供 open_id（应用 `get_meeting_participants`，详见场景12），**不得将通讯录工具作为通用人员信息查询接口使用**。
- **`username` 为必填（强约束）**：`contact_search` 的 `username` 参数为**必填**，缺失时工具会直接报错。**严禁模型自行猜测、编造或截取一个名字调用工具**；必须先与用户确认要查找的用户名后再执行。
- **结果较多时建议追加过滤**：当仅按 `username` 查询返回的成员较多（如同名情况）时，**应建议用户补充 `job_title` 或 `department_name`** 进一步过滤后再次调用 `contact_search`，提升匹配精准度，减少候选项；不得在用户未确认的情况下自行选择某一条。
- **唯一命中的返回特性**：当 `contact_search` 搜索结果**只有一条**时，工具仅返回该成员的 `open_id` 字段，**不会返回 `user_name` / `job_title` / `department` 等其他成员信息**。此时模型可直接将该 `open_id` 用于后续工具（如 `meeting_invitees_add` / `meeting_control_call`），**无需也无法**基于该响应向用户展示部门/职位等字段；如确需展示成员名称用于二次确认，应使用用户原始口径中的姓名，**严禁伪造**职位/部门信息。
- **多结果必须由用户确认（强约束）**：当 `contact_search` 返回**多条候选结果**（典型如同名/同部门成员）时，**严禁**模型基于职位、部门、入职时间、匹配度等任何维度**自行选择**某一条继续后续操作（如 `meeting_invitees_add` / `meeting_control_call` / `meeting_control_kick` 等）。必须将候选项的关键信息以**清晰列表**形式展示给用户（仅展示昵称 + 职位 / 部门），并明确询问"请确认要选择哪一项"，待用户**明确指定**后再继续执行。**即便其中某条结果看起来"明显更匹配"，也必须等待用户确认，不得跳过该步骤**。
- **隐私展示白名单（强约束）**：通讯录返回的数据可能包含工号、手机号、邮箱等敏感字段。**向用户展示时仅允许出现「姓名（昵称）/ 部门 / 职位」三类字段**，**严禁擅自展示工号、手机号、邮箱、`open_id`、`userid`、`ms_open_id` 等任何其他敏感字段**，即便用户的初始输入中包含某项敏感字段也不得在搜索响应中回显原文（如需回显须按 `references/privacy_policy.md` 脱敏）。
- 手机号、邮箱属于强敏感信息，调用前**禁止在对话中复述明文**，必要时按 `references/privacy_policy.md` 脱敏展示
- 单次最多 50 个手机号/邮箱，超限时分批查询，**严禁自行截断**
- 查询不到用户时，可能是跨企业、用户未加入通讯录、企业关闭了通讯录搜索权限等原因，原样告知用户，禁止猜测

**输出规范**
- 展示命中成员时**仅允许**「昵称 / 职位 / 部门」三个字段，禁止展示工号、手机号、邮箱、open_id 等敏感字段
- `open_id` 作为内部参数使用，向用户展示时优先昵称
- 唯一命中时由于工具仅返回 `open_id`，应直接进入后续操作流程（按需向用户用其原始口径中的姓名做二次确认），**严禁伪造部门/职位信息**

---

### 场景11：管理会议受邀人

**触发条件**
用户要求查看、添加、移除、替换已有会议的受邀成员

**处理流程**
1. 若用户提供会议号，先调用 `get_meeting_by_code` 获取 meeting_id
2. 调用 `get_meeting_invitees` 查询当前受邀人列表
3. 若用户仅提供姓名/手机号/邮箱，按场景9获取 open_id
4. 根据需求选择工具：
   - **添加** → `meeting_invitees_add`（增量添加，不影响已有）
   - **移除** → `meeting_invitees_remove`（按 open_id 精确移除）
   - **整体替换** → `meeting_invitees_replace`（用新列表完全覆盖；传空数组表示**清空所有受邀人**）
5. 向用户展示变更摘要（**新增 / 移除 / 替换前 → 替换后**，均用昵称展示），获得明确同意后再执行

**注意事项**
- 仅**会议主持人**可操作受邀人变更，非主持人会返回权限错误
- `remove` / `replace` 属于不可逆操作，二次确认是强制要求（见核心规范"敏感操作"）
- `replace` 传空数组将**清空全部受邀人**，必须向用户强调影响并获得明确同意
- 单次最多 100 个 open_id，超限时分批 `add`，**严禁自行截断**
- 若用户希望"用 X 替换 Y"这类局部替换，**推荐组合使用** `remove` + `add` 两步操作，避免误用 `replace` 清空其他成员

**输出规范（强约束 - 受邀人变更专用回复模板）**

执行 `meeting_invitees_add` / `meeting_invitees_remove` / `meeting_invitees_replace` 成功后，回复**必须**严格按以下模板组织字段，且**仅展示这些字段**：

- **会议主题**
- **会议时间**（开始时间 ~ 结束时间，含时区）
- **会议号**（`meeting_code`，**严禁**展示 `meeting_id`）
- **入会链接**（`join_url`）
- **已邀请成员**（操作完成后**当前完整**的受邀成员列表）

附带 `X-Tc-Trace` / `rpcUuid` 追踪信息。

「已邀请成员」展示规则（严格遵守）：
1. **必须展示通讯录中的姓名**（如 `张三`），**严禁**直接展示 `open_id` / `userid` / `ms_open_id` / 花名 / 邮箱前缀等任何内部标识
2. 姓名来源**仅限以下两种**，按优先级回退：
   - 优先使用 `get_meeting_invitees` 响应中的 `user_name` 字段（变更操作后**应再次调用** `get_meeting_invitees` 获取最新完整列表，从中读取 `user_name`）
   - 次选使用本轮对话中用户原始口径里的姓名（如用户输入"加上张三"，则该 `open_id` 对应"张三"）
   - **严禁**调用 `contact_search` 反查 `open_id` 取姓名（该工具仅支持姓名→open_id 正查，不支持反查）
3. 若以上两种来源均无法获得姓名，标注为 `未知成员`，**禁止回退到打印 `open_id`**
4. 当且仅当用户**明确**要求"展示 ID / 原始字段"时，才可附带展示 `open_id`
5. 会议主题、会议号、入会链接等基础字段若变更接口响应未直接返回，**应通过 `get_meeting --meeting-id` 补齐**，不得遗漏字段或用 `-` / `N/A` 占位

---

### 场景12：会中控制（呼叫 / 踢人）

**触发条件**
用户要求在进行中的会议中：呼叫某成员加入、踢出某成员

**处理流程（呼叫）**
1. 确认会议正在进行（用户口径或先调 `get_meeting` 校验状态）
2. 若用户仅提供姓名/手机号/邮箱，按场景9获取 open_id
3. 向用户展示**被呼叫成员名单**（昵称），获得明确同意
4. 调用 `meeting_control_call`（`users` 最多 20 个 open_id）

**处理流程（踢人）**
1. 确认会议正在进行
2. **必须先调用 `get_meeting_participants`** 查询当前参会成员，从其返回结果中定位待踢成员；**严禁使用 `contact_search` / `contact_lookup_by_phone` / `contact_lookup_by_email` 的返回值作为踢人入参**
3. **【字段路由表 — 强制，按 `get_meeting_participants` 返回的 `instanceid` 判定，禁止凭印象分类】**

   | `instanceid` | 入参字段 | 使用的 id 字段 |
      |---|---|---|
   | `PSTN`（电话入会） | `pstn_users` | **`ms_open_id`** |
   | `SIP`（SIP 设备） | `sip_users` | **`ms_open_id`** |
   | 其他（`Mac` / `Windows` / `iOS` / `Android` / `Web` 等） | `users` | **`open_id`** |

   **硬规则（必须遵守，跨多轮会话同样适用）**：
    - ⛔ 凡 `instanceid ∈ {PSTN, SIP}` 的成员，**严禁放入 `users`**；必须走 `pstn_users` / `sip_users` 并使用 `ms_open_id`。
    - ⛔ 凡某成员 **`open_id` 为空字符串**（PSTN/SIP 入会成员的典型特征），**必属** PSTN/SIP，**严禁放入 `users`**，必须按 `instanceid` 走对应字段。
    - ⛔ **禁止默认把所有人塞进 `users`**：每个待踢成员都必须逐一查 `instanceid` 后再分桶，尤其在多轮会话上下文较长时，**不得凭记忆或惯性归类**。
4. **数据清洗（建索引/分桶前必做）**：
    - **只认在会成员**：`get_meeting_participants` 会返回带 `left_time`（非 null）的历史离会记录，**必须过滤掉 `left_time != null` 的条目**，避免误匹配/误踢已离会成员。
    - **跳过空 id**：建立 id→成员 索引时，`open_id` / `ms_open_id` 为空字符串的键一律跳过，避免空串互相覆盖。
    - **去重**：同一成员可能出现多条记录，按 id 去重。
5. **`users` / `sip_users` / `pstn_users` 至少一个非空，三者总数 ≤ 20**
6. 询问用户 `allow_rejoin`（是否允许被踢成员重新加入，默认 `true`=允许）
7. 向用户**完整展示**被踢名单 + `allow_rejoin` 取值，获得明确同意后调用 `meeting_control_kick`

**注意事项**
- 踢人是会中破坏性操作，二次确认是强制要求（见核心规范"敏感操作"）
- **不允许踢自己**；非主持人 / 联席主持人无权限
- 已离开的成员、被叫方占线/拒接等场景，按 `references/error_dictionary.md` 指引向用户告知
- 单次最多 20 个，超限分批，**严禁自行截断**
- 若 `get_meeting_participants` 中查不到用户口径所指的成员，应原样告知用户"该成员当前不在会议中"，**严禁回退到 `contact_search` 反查 open_id 后强行踢人**

**输出规范**
- 呼叫：展示成功呼叫的成员清单（昵称）+ 失败成员及原因 + 追踪信息
- 踢人：展示成功踢出的成员清单（昵称）+ `allow_rejoin` 结果 + 失败成员及原因 + 追踪信息

---

## 工具索引

| 工具 | 说明 | 所属场景 |
|------|------|-------------------|
| `convert_timestamp` | 时间转换，获取当前/相对时间，UTC 时间戳转换 | 场景1（前置）、核心规范-时间处理 |
| `schedule_meeting` | 创建会议，支持普通/周期性会议，可选 `invitees` 一并指定受邀人 | 场景1 |
| `update_meeting` | 修改会议信息，可选 `invitees` + `invitees_operate_type` 同步增删替换受邀人 | 场景2、场景11 |
| `cancel_meeting` | 取消会议，支持子会议/整场周期性会议 | 场景3 |
| `get_meeting` | 通过 meeting_id 查询会议详情 | 场景2/3/4 |
| `get_meeting_by_code` | 通过会议号转换为 meeting_id | 通用规则-Code转换 |
| `get_meeting_participants` | 获取参会成员明细 | 场景4 |
| `get_meeting_invitees` | 获取受邀成员列表 | 场景4、场景11（变更前展示） |
| `get_waiting_room` | 查询等候室成员 | 场景4 |
| `export_participants` | 异步导出参会成员统计（含累计参会时长、会议互动行为等统计），返回 job_id | 场景4 |
| `get_job_result` | 获取异步导出任务结果（状态、下载链接） | 场景4 |
| `manage_waiting_room` | 管理会中等候室成员 | 场景5 |
| `get_user_meetings` | 查询未开始/进行中的会议列表 | 场景6 |
| `get_user_ended_meetings` | 查询已结束的历史会议列表 | 场景6 |
| `search_meetings` | 搜索会议列表，支持关键词、搜索字段、会议号、时间窗口等过滤 | 场景6 |
| `get_records_list` | 查询录制文件列表 | 场景7 |
| `search_records` | 搜索录制文件，支持关键词、搜索字段、时间窗口、会议、文件类型等过滤 | 场景7 |
| `get_record_addresses` | 获取录制播放地址 | 场景7 |
| `get_transcripts_paragraphs` | 获取转写段落 ID 列表 | 场景7 |
| `get_transcripts_details` | 通过 pid 获取转写文本 | 场景7 |
| `search_transcripts` | 搜索转写关键词 | 场景7 |
| `get_smart_minutes` | 获取 AI 智能纪要 | 场景7 |
| `search_minutes` | 搜索元宝纪要，支持关键词、时间窗口过滤 | 场景7A |
| `get_minutes` | 查询元宝纪要详情，支持获取概览/要点/待办/滚动总结 | 场景7A |
| `apply_record_permission_prepare` | 录制权限申请-预览，展示申请的会议标题/所有者/申请人等信息供用户确认 | 场景9 |
| `apply_record_permission_commit` | 录制权限申请-提交，用户确认后正式发起申请，返回审批链接 | 场景9 |
| `contact_search` | 按姓名/职位/部门搜索企业通讯录成员（仅限会议邀请、呼叫入会场景），返回 open_id | 场景10 |
| `contact_lookup_by_phone` | 按手机号批量查找企业用户（仅限会议邀请、呼叫入会场景）（最多 50），返回 open_id | 场景10 |
| `contact_lookup_by_email` | 按邮箱批量查找企业用户（仅限会议邀请、呼叫入会场景）（最多 50），返回 open_id | 场景10 |
| `meeting_invitees_add` | 向已创建会议增量添加受邀人（最多 100，仅主持人可操作） | 场景11 |
| `meeting_invitees_remove` | 从会议中移除指定受邀人（仅主持人可操作） | 场景11 |
| `meeting_invitees_replace` | 用新列表整体替换会议受邀人（传空数组=清空，仅主持人可操作） | 场景11 |
| `meeting_control_call` | 会中批量呼叫成员入会（最多 20） | 场景12 |
| `meeting_control_kick` | 会中踢出成员，支持普通/SIP/PSTN，可选 `allow_rejoin` | 场景12 |
| `submit_feedback` | Agent 意见箱，主动上报工具缺失/错误/能力不足/结果异常/建议（强制触发场景） | 场景8 |
| `check_skill_version` | 检查技能版本更新 | 核心规范-版本管理 |
| `get_skill_update_preference` | 查询本地更新偏好与 snooze 决策（是否需要弹出更新提示） | 核心规范-版本管理 |
| `set_skill_update_preference` | 设置本地更新偏好（snooze 暂不更新 / auto_upgrade / disable_optional_check / enable_optional_check） | 核心规范-版本管理 |
