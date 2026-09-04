---
name: work-assignment-delegation
description: "Use when the user wants to assign work to another person, help the recipient understand the task, or record a reviewable work assignment in Fusion Memory. Covers generic project sync, developer tasks, handoff, and follow-up without limiting the scenario to engineering work."
category: productivity
---

# 工作安排委派

当用户要把一项工作交给另一个人、希望对方理解后推进，并且要把这次安排记成可追溯记录时，使用这个 skill。

核心原则：

- 先自主检索和整理；只有确实需要安排者决策，或存在多个等价接收者/历史任务候选时才追问。
- 不能把推测写成确定事实。
- 不把场景限制在开发任务；不只限于开发任务，项目同步、交接、客户沟通、跨部门协作都适用。
- 只在事实确认后写入 Memory。
- 需要记录时，调用 `assignment_upsert` 创建或更新安排；接收者确认时调用 `assignment_accept`，方案提交和结束状态调用 `assignment_transition`。
- 需要查回时，调用 `assignment_get` 或 `assignment_list`。

触发判定：

- 用户说“让/叫/安排/请/交给/丢给/催一下/跟进/负责 + 某人 + 写/做/处理/整理/实现/准备/提交/跟进/看一看/看下/检查/验证/反馈/排查/测一下/确认一下 + 某事/交付物/预期结果”时，按工作安排处理，不能简化成普通转达；即使任务很短、不是开发工作、没有截止时间，也应进入本 skill。
- “布置任务、安排工作、交给某人、让某人负责、让某人出方案/文档/结果、让某人处理问题”都属于工作安排，即使不是开发任务。
- 只要当前消息同时包含明确接收人和待推进事项，就必须先调用 `assignment_upsert` 记录安排，再按需调用 `assignment_send_card`；缺少背景、截止时间或验收标准时写入 `gaps`，不能降级为 `feishu_message_send`。
- 只有用户明确只是转达/带话/发一条普通消息，且不要求对方交付、确认、推进或形成记录时，才用普通飞书消息。
- 如果同一句话既像带话又像任务安排，先按任务安排理解；只有接收者、交付物或是否需要记录会影响后续动作且无法从上下文判断时，才向安排者简短确认。

推荐流程：

1. 从当前消息识别安排者、接收者、任务目标和明确约束。`original_request` 必须逐字保存安排者实际输入或语音转写；去掉系统注入的 `<feishu_context>` 包装，但不得润色、概括或加入 Agent 推导。语音把姓名转写错时，可以在身份字段中匹配正确成员，不能修改原始转写。
2. 先调用 `assignment_list`，按接收者稳定 `user_id` 查看未结束安排和相近任务；再调用 `memory_search` 并传 `visibility="organization"`，检索任务名称、项目名、客户名或仓库名相关共享背景。已有安排里的上下文、证据、风险和行动项必须实际用于本次整理，不能只用来猜工具 schema。
3. 判断是否是同一逻辑任务：安排者和接收者的稳定 `user_id` 相同、核心目标相同且历史安排未结束时，复用已有记录及其 `idempotency_key`。不要为同一逻辑任务生成新的 `idempotency_key`。如果仍存在多个等价候选，再向安排者确认。
4. 将检索结果分成已确认事实、Agent 分析和待确认缺口。写入前至少准备 `context`、`expected_outcome`、`evidence_refs`、`gaps`、`risks`、`action_items`；每项证据保留来源 URI，每个行动项写负责人，截止时间已知时写入行动项。没有截止时间时，必须在 `gaps` 中加入结构化缺口，不能只写进 `original_request`。
5. 只有缺失内容会改变任务目标、接收者、交付边界或验收决策时才打扰安排者；可通过仓库、内部资料和历史任务查明的信息自行补齐并保留证据，无法查明但不阻塞理解的信息标为缺口。
6. 在用户确认后，调用 `assignment_upsert` 记录安排并显式写入 `state: "assigned"`；如果已有记录仍是 `draft`，先调用 `assignment_transition` 执行 `transition_type: "assign"`。
7. 需要通过飞书交给接收者时，调用 `assignment_send_card`。该工具从 Memory 拉取权威详情，卡片把“安排者原始内容”“Agent 分析整理 (非安排者原话)”和“参考资料”分区展示，只保留“确认接收并创建飞书任务”动作；同时给安排者发送一张独立进度卡，后续在原卡片上更新“已发送、已读（若可获取）、已确认接收、飞书任务已创建”，不能额外发送重复进度消息。
8. 如果接收者确认收到，调用 `assignment_accept`。它校验当前飞书操作者是接收者，确认 Memory 状态，并通过 Memory 的原子发布 claim 创建、记录至多一个对应的飞书任务。
9. 如果接收者需要形成可评审方案，先帮助整理方案，再记录 transition。

反馈机制：

1. 接收者理解、形成方案或推进任务时遇到缺口，先搜索历史安排、组织资料、相关文档和可访问资源。仍无法补齐时，统一调用 `assignment_feedback`，以 `arrangement_id` 绑定反馈；正式飞书任务创建后仍沿用同一条反馈链。
2. 只有以下四类情况可标记为阻塞：缺少只有安排者才能提供的必要信息；需要安排者做不可逆或明显影响范围的决定；权限、资源或需求冲突导致无法继续；继续推断可能造成显著返工或越权。
3. 如果能采用可逆、明确记录的假设继续，就不能阻塞。用 `notification_strategy: "non_blocking"` 写入同一反馈对象，等待里程碑、固定时间或安排者会话空闲时提醒；纯信息记录使用 `notification_strategy: "record_only"`，只写日志不提醒。
4. 阻塞反馈使用 `notification_strategy: "blocking"`，必须说明所属任务和阶段、缺少的信息、无法自行推断的原因、已核查或尝试、进度影响，以及 2-3 个可选项和推荐项。
5. 原始反馈、安排者原始答复与 Agent 分析必须分开。原始内容只能追加，不能覆盖；`private_note` 默认不能出现在共享卡片或接收者视图中。私密内容若会改变执行条件，必须形成可共享的脱敏结论，否则保持阻塞。
6. 安排者答复使用 `assigner_reply`。Agent 读取答复、更新任务理解后，反馈状态必须保持为 `updated_waiting_recipient_confirmation`，不能自动恢复执行；只有接收者确认更新后的理解后，才使用 `recipient_confirm` 进入可执行状态。
7. 反馈是独立对象，不能塞进 `assignment_transition`。`assignment_transition` 仍只管理任务安排主状态与方案提交，不承担反馈版本、答复或确认。
8. 同一反馈对象只维护一条线程。首次阻塞反馈向安排者发送一张问题卡并回绑卡片 ID；安排者答复后原位更新该卡，不能重复发送安排者卡。工具同时向原反馈发起人发送一张接收者结果卡，展示同一线程的共享内容和“确认更新后的理解”动作；它只是同一反馈对象的接收者投影，不能创建新的反馈线程，也不能向同一接收者重复发送结果卡。非阻塞反馈和纯信息记录在没有现有卡片时不立即发卡。
9. 阻塞反馈卡片首屏同时展示 2-3 个有效快捷选项和必填的“其他答复”输入框，不要再生成 label 或 value 为 `other`、`custom`、`custom_time` 或仅表示“其他”的占位选项。安排者需要补充自由文本时，直接在原卡片输入并提交，不再通过一条新的聊天消息追问。
10. 当前最新消息是 `<feishu_card_action>` 且 `dispatch.handler` 为 `assignment_feedback` 时，只调用一次 `assignment_feedback`，把标签内的完整 `card_action_json` 原样传入。工具会校验操作者并把快捷选项或 `form_value.custom_reply` 写成 `assigner_reply`；禁止调用 `tool_describe`、`tool_search_code`、`read` 或 `bash` 查找 action、schema 或工具源码。
11. `assignment_feedback` 返回 `ok=true` 且 `assistant_reply_required=false` 时成功后静默结束；不能再调用 `feishu_message_send`、重复解释卡片内容或向接收者另发普通确认消息。工具已经更新安排者卡、写入 Memory，并在需要时投递接收者结果卡。
12. 安排者答复后，原反馈卡片只读展示“已更新、待接收者确认”，不能在这张发给安排者且已经消费的卡片上生成接收者确认按钮。接收者从自己的 HaiTun 会话收到结果卡，核对更新后的理解并确认；确认后工具把同一反馈线程推进到可执行状态并原位更新双方卡片。
13. 反馈卡片可见正文使用任务标题，不显示 `arrangement_id`、`task_id` 等内部原始 ID；这些 ID 只保留在隐藏的回调关联字段中。任务标题和反馈者姓名由工具按 `arrangement_id` 从权威安排记录中解析，不需要也不应该由对话提供或推测；读不到真实标题时显示“当前工作安排”。卡片上的每条反馈会标注可确认的姓名与角色；身份无法唯一确认时只显示角色，不要在 `raw_content` 里再手写“接收者：”这类前缀。

接收者缺口询问：如果当前会话的接收者在已有安排上下文中询问截止时间、任务范围、交付格式、验收标准、资源或权限，且这些信息无法从安排、项目资料或组织资料确定，应把问题写入同一 `arrangement_id` 的 `assignment_feedback`，不能代接收者给安排者发普通消息或手工发卡。反馈工具负责通知安排者；当前回合立即告知接收者“已提交反馈，等待安排者处理”，不得等待真人回复或阻塞会话。工具返回校验错误时只修正同一工具的参数并重试一次，不能改用 `feishu_message_send`、`feishu_message_send_card` 或 `feishu_topic_start` 绕过反馈线程。

接收者流程：

1. 工作安排卡片已经分区包含安排者原始内容、Agent 整理的背景/目标/缺口/风险/行动项和参考资料；只有需要刷新状态或继续讨论时才调用 `assignment_get`。
2. 不要重复输出卡片已经完整展示的详情，也不要让接收者等待模型重新组织同一份内容。
3. 明确区分事实、假设和待确认事项；缺失信息只标成缺口，不补写成事实。
4. 接收者确认收到时，调用 `assignment_accept`。成功后不再经 `feishu_api` 另建 task v2 任务（`POST /open-apis/task/v2/tasks`），也不再调用 `assignment_transition`，避免重复任务和重复状态迁移。
5. 需要方案时：先按 [`proposal-need-remind`](../proposal-need-remind/SKILL.md) 做中事/大事提醒(可跳过)；用户同意写方案后再用 [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md) 协助形成可评审草案(多方案+分析；结构与硬闸门按该 skill)。不要在未提醒分级的情况下直接当成普通转达。
6. 接收者确认方案后，调用 `assignment_transition`，其中 `transition_type: "submit_plan"`，并把方案写入 `plan`。
7. 如果接收者明确不形成方案或任务不需要方案，调用 `assignment_transition`，其中 `transition_type: "close"`，并写入 `closure_reason`。不要调用 `closed_without_plan`，Memory 没有这个 transition。

确认与发布语义：

- Memory 的“已接收”和飞书原生任务的“已发布”是两个结果。
- `assignment_accept` 先确认接收，再向 Memory 原子申请一次发布权；只有获得 claim 的 Gateway 可以创建飞书任务。不要直接调用底层发布协议。
- 飞书任务发布成功或失败后，Memory 在同一事务中终结 claim、追加 `delivery_records` 和审计事件，但不借此改变工作安排的业务状态。
- 飞书任务发布失败不撤销接收。若发布已被 claim、已经失败，或任务已创建但 Memory 未能完成记录，只能人工核对后补记，不要再次创建任务。
- 不要自动重试飞书任务创建。飞书任务接口没有客户端幂等键，未经确认的重试可能创建重复任务。
- 如果 `assignment_accept` 返回 `already_published=true`，直接视为成功，不再创建新任务。

可评审方案要求：

- 说明接收者对任务的理解，而不是替安排者新增事实。
- 列出准备采用的步骤、交付物和验收方式。
- 标出仍需安排者或评审人决策的问题。
- 不开始实施，除非用户明确要求进入实施。
- 如果方案基于假设，必须把假设放在单独小节。

场景模板：

1. 通用工作安排：优先简洁，围绕背景、结论、行动项、负责人、截止时间和来源组织。
2. 开发任务：补充影响范围、代码模块、技术约束、验证方式和评审关注点。
3. 交接或同步：强调上下文、未决事项、依赖关系和下一步接力人。

模板规则：

- 模板只改变表达和重点，不改变已确认事实。
- 模板不得改变已确认事实。
- 模板不能凭空补齐事实缺口。
- 若模板与当前事实不一致，以事实为准。

常用工具：

- `memory_search`
- `assignment_upsert`
- `assignment_get`
- `assignment_list`
- `assignment_transition`
- `assignment_feedback`
- `assignment_send_card`
- `assignment_accept`
- `assignment_delivery_refresh` 仅供后台触发器刷新投递进度；对话和本 skill 不得主动调用 `assignment_delivery_refresh`。
- `feishu_message_send`
- `feishu_message_send_card` / 现有卡片发送工具（只有 `assignment_send_card` 不满足当前卡片需求时再直接使用）

输出要求：

- 简洁、可执行。
- 不暴露内部推理过程。
- 不写多余的过程性说明。
- 只在必要时追问。
