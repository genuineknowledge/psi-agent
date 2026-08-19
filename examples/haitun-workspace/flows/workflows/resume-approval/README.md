# Recruitment Talent Workflow V3

招聘流程分为三个独立阶段：Workflow A（AI 简历初筛）、A 与 A2 之间的外部 Human 初审、Workflow A2（审核后归档与面试表写入），以及后续 Workflow B（面试结论）。简历上传后不会留下等待人工操作的运行中 Workflow。

## Workflow A：AI 简历初筛与人才库建档

```text
/workflow:resume-approval
```

唯一外部输入是 List Artifact `resume_files`。流程完成：

1. 加载岗位、评分标准、飞书配置并生成批次号；
2. 安全归档附件，按 SHA-256 去重；
3. 并行提取标准和简历，foreach 并行分析；
4. 对完整批次做静态校验，只将错误候选人送入最多两轮的局部 AI 返修；每轮后重新执行完整约束校验。一般残留业务约束错误作为告警继续，但问题库的结构、证据关联、类别覆盖、隐私或未知负面断言错误始终 fail-closed；
5. 通过源文件 SHA-256 精确关联并上传原生简历附件，写入 15 字段人才库，其中问题库只显示类别和问题正文；
6. 验证附件和完整人才行已读回，再写出不可变 `initial_review_handoff` 和明确的初审操作说明；
7. 交接门禁通过后才清理 SHA 地址化简历和提取文本副本；
8. Workflow A ends（在人工审核之前结束），不读取初审决定、不生成面试草稿，也不写面试记录。

Workflow A 完成后 no workflow remains active。Human 可以在飞书中逐行完成初审，无论间隔多久都不需要保留运行时 checkpoint，也不使用 `run_flow_resume`。

评分维度、总分、评级阈值和岗位要求均来自运行时配置的在线参考文档。岗位配置可以是单岗位，也可以是只在配置清单内自动匹配的启用岗位组合；不会凭简历创造岗位。

### 简历附件输入

首选方式是在 Workflow 输入框上传简历附件。当前运行器已验证的 `resume_files` Artifact 格式是路径字符串列表；上传文件会落到用户 `Downloads/.psi/<date>/` 下，例如：

```json
{
  "resume_files": [
    "C:/Users/<user>/Downloads/.psi/2026-08-09/candidate-01.pdf"
  ]
}
```

聊天中可以先发送 `开始简历筛查`，再分多条消息发送附件。该口令只创建空收集批次，不扫描 `.psi/resume-approval/inbox`、Downloads、旧运行或人才表；其中 `.psi/resume-approval/inbox` 仅是 Workflow A 启动后的 SHA 地址化内部暂存目录，不是待处理收件箱。每条附件只登记本地路径，Agent 会回复当前文件名清单；用户发送 `开始筛查` 后才固化完整 `resume_files` 并调用一次 Workflow A。期间可以发送 `查看已收集简历` 或 `取消收集`。Web 端若在启动工作流的同一条消息中一次上传全部附件，则直接一次收集并运行，无需再发 `开始筛查`。收集状态位于当前用户工作区的 `.psi/workflow-input-collections/`，不依赖聊天上下文完整保留。

为本地测试和可重复运行，仍兼容 workspace 内的路径字符串或 `{"path":"...","name":"..."}` 描述符。不要传入未经运行器证明的附件 ID、URL 或 Base64 字段。允许格式为 PDF、DOCX、Markdown 和纯文本；由于原生附件使用飞书单次上传接口，每个源文件最大 20 MiB，staging 会在任何网络调用前拒绝超限文件并记录内部 `size_bytes`。所有文件进入 SHA-256 地址化目录并按内容去重；同一内容同时来自上传目录和 workspace 时，上传附件优先保留。原始上传文件名只保存在本地 `staged_resume_files` 运行 Artifact 中用于溯源；进入提取、评估和远端上传合同的名称统一为中性的 `resume.<ext>`，防止文件名中的手机号或其他个人信息进入 `source.name` 或飞书附件名。

人才库显示 15 个业务字段：姓名、简历附件、评级、学历、毕业院校/背景、总分、备注、匹配岗位、匹配点、不匹配点、面试建议、面试建议理由、问题库、初审状态、简历摘要。`简历附件` 是飞书 type 17 原生附件，不是文本 URL；评估只通过 `assessment.source.sha256 == staged_resume_file.sha256` 关联源文件，不按姓名或文件名猜测。问题库按 `1. [类别] 问题` 的固定格式显示 3–6 条；证据锚点、目的和正负信号只保存在 workspace 私有交接中。面试记录只显示姓名、目标岗位、面试前摘要、详细面试重点、风险提示、建议问题、纪要/补充、评分、定级、疑问、风险验证、结论和状态。技术 ID、评估版本、结构化 JSON、附件 token、本地路径和临时 URL 不进入用户可见输出；跨阶段必要的技术关联只保存在 workspace 私有交接目录。

人才行先按包含 `问题库` 的完整 12 字段 AI 指纹解析，再决定是否上传；Human 所有的 `备注`、`初审状态` 和 token 不具内容稳定性的 `简历附件` 均不进入指纹。精确复用行已有有效附件时不重复上传；缺附件时只按精确 `record_id` 增量回填 `简历附件`，不触碰任何 AI 字段或 Human 字段；新行在上传后一次写入完整 15 字段。创建或回填必须按姓名重新查询并验证唯一完整指纹及附件 token 已落表，且不可变交接门禁已接受 `attachment_persisted=true`，随后才允许 cleanup 删除 SHA 地址化暂存文件。任何映射、上传、写入或读回歧义均整批 fail-closed。

## 外部 Human 初审

审核人只修改人才库的 `初审状态`，将每一行设为 `通过` 或 `不通过`。全部完成后，回到聊天并只回复 `初审完成`。这句话仅是启动信号，不是决策数据；主 Agent 会使用 Workflow A 返回的完整 `initial_review_handoff` 对象新启动一次 `resume-interview-preparation`。只要仍存在任何 `待审批`，A2 就会在生成和写入前拒绝执行。

## Workflow A2：审核后归档、面试准备与记录写入

使用 Workflow A 返回的完整 `initial_review_handoff` 对象显式启动：

```text
/workflow:resume-interview-preparation
```

A2 验证交接文件哈希、目标飞书表、岗位、评估版本、结构化问题库和人才记录覆盖，然后按精确 Feishu `record_id` 重新读取全部初审结果。任何 `待审批`、缺失、重复、格式异常或 AI 所有字段变化都会在面试写入前阻断。全部决定有效后，A2 固化审核后快照，按通过候选人并行生成只读草稿；其中 `建议问题` 必须逐条、原序复用 A1 的结构化问题，Program 会拒绝任何独立改写。统一组装后由单独写入 Agent 查询或创建面试记录，最后按面试 `record_id` 持久化私有脱敏评估交接。A2 失败时使用同一描述符启动一个新的 A2 run，不重跑 A，也不改审核前交接文件。

## 面试期间

用户在飞书多维表格的「面试记录」表补充：面试纪要、补充信息、四维 1–5 分、面试 S/A/B/C、聪明人 T1–T5、疑问待验证、风险验证和面试结论。加权公式为 `靠谱×0.3 + 专业×0.3 + 学习行动×0.2 + AI Native×0.2`。完成后设置 `面试状态=已完成`。表中不再设置独立证据列；Workflow B 从纪要和补充信息提炼每个评分的依据，叙述不足以支持任一评分时 fail-closed。

此阶段没有运行中的 Workflow，也不依赖旧 checkpoint。

## Workflow B：招聘结论与最终确认

面试完成后启动：

```text
/workflow:interview-conclusion
```

输入是 Workflow A2 返回的一个或多个精确面试 Feishu `record_id`：

```json
{
  "interview_record_ids": [
    "recExample0001"
  ]
}
```

流程会并行读取每条面试记录、对应私有脱敏评估交接和来源人才库行；随后确定性校验四维评分、降级规则和从纪要提炼的依据，应用简历评级×面试表现决策矩阵，在 Human checkpoint 中给出精炼招聘结论。Human 在原始、精确的「面试记录」行将 `面试状态` 从 `已完成` 设置为 `录用`、`不录用` 或 `待定`；流程随后只按该行的 `interview_record_id` 读回验证并追加审计文档。`talent_record_id` 仅用于验证候选人才库中的姓名、匹配岗位和 `初审状态=通过`，人才库不承载最终面试状态。

## 本地配置

部署时在同目录准备不提交文件：

1. 将 `resume-approval.defaults.inputs.example.json` 复制为 `resume-approval.defaults.json` 并填写真实飞书目标；参考资料 URL 使用多维表格文档页的 `?table=ldx...` 链接，同时填写对应的底层 Docx token；
2. 将 `role-requirements.inputs.example.json` 复制为 `role-requirements.json`；
3. 确认两个多维表格文档页的 URL 和底层 Docx token 可由 Bot 读取；不再需要 workspace 根目录下的本地 `standards/` 评分文件。
4. 确认 HaiTun 进程已配置 `PSI_FEISHU_APP_ID` 和 `PSI_FEISHU_APP_SECRET`，并具备目标文档、人才库和面试记录表的读取、写入及 `bitable_file` 附件上传权限；密钥和上传返回的附件 token 不得写入配置、日志或提交。
5. 正式运行前读取两个 Docx token 的标题和正文长度，并核对 `feishu-schema.json` 中的字段名称、类型和单选值；任一资源不可读或 Schema 不一致时停止部署。

业务事实源是运行时配置的评分标准和岗位需求文档页。Workflow A 每次启动都通过配置的 Docx token 读取实时正文，并对本批次内容计算 SHA-256 固定版本。`ldx` 页 ID 用于识别多维表格中的页面，不直接作为 Docx OpenAPI token。

## Program 结构化输出合同

FusionFlow 为兼容纯文本脚本，会把单输出 Program 的 stdout 保留为字符串，即使内容是合法 JSON。
因此，所有产生数据的 Program Step 必须声明至少两个命名输出，并输出以 Artifact ID 为精确键名的
严格 JSON object。第二个输出是执行或校验 manifest；不得为了“简化”而删除，否则 List Artifact
会退化成字符串并在 foreach 处失败，结构化 object 也会迫使下游 Agent 自行猜测解析。

`assert_*_ready_step` 和 `assert_*_program_ready_step` 是唯二例外：它们是零输出硬门禁，成功时
不得输出 stdout。前者在校验状态不是 `complete`、存在阻断错误或有效记录为空时以非零状态退出；最终简历评估中的 `constraint_warnings` 不属于阻断错误；
后者检查非 foreach Program 输出，发现运行器生成的 `$fusion_flow/program_error` 时立即退出。
这样被阻断的批次会在任何飞书写入和 Human checkpoint 之前终止，也不会把错误对象当成普通
Artifact 传给下一步并产生误导性的类型错误。

简历评估在硬门禁前有两轮显式、可审计的闭环返修。每轮先对完整候选人列表运行完整业务约束
校验器，再仅对候选人局部错误生成 repair request；Repair Agent 必须保留来源 SHA、批次和有效证据，
Program 按原候选人索引合并后再次全量校验。无错误时 foreach 为空，不产生额外 LLM 调用。两轮后
仍存在的一般业务约束错误写入 `constraint_warnings`；JSON 结构、人才库字段类型、飞书单选值，或问题库结构/证据/类别/安全约束不成立时
保持 fail-closed。Repair Agent 自身超时、轮次耗尽或无法返回 JSON 时仍按原执行语义终止。

Agent 和 Human Step 不受这条 Program stdout 兼容语义影响，但仍须遵守各自的结构化提交与
fail-closed 合同。

## 飞书数据

- `岗位需求`：岗位运营视图；
- `人才库`：15 字段用户可见看板，包含原生简历附件和问题库，是每位候选人的初审入口，也是简历评估、问题库与初审结果的事实源；
- `面试记录`：跨天维护面试过程，也是面试证据与最终录用状态的事实源；
- 汇总文档：最终结论的审计摘要，不是事实源。

人才库创建与复用使用包含 `问题库` 的 12 个 AI 所有字段作为确定性指纹，排除 Human 会修改的 `备注`、`初审状态`，也排除 token 不具内容稳定性的 `简历附件`。已有精确指纹行缺附件时只按 `record_id` 增量更新该附件格；后续跨阶段仍一律使用 Feishu `record_id` 精确关联。重复指纹、SHA 映射歧义或写后读回不一致都会 fail-closed。

## 关键文件

- `resume-approval.workflow`：Workflow A，28 步、最多两轮局部评估返修、无 Human checkpoint；
- `../resume-interview-preparation/resume-interview-preparation.workflow`：Workflow A2，13 步，读取外部初审结果后完成审核归档与面试记录写入；
- `../interview-conclusion/interview-conclusion.workflow`：Workflow B 的 `/workflow:interview-conclusion` 规范入口，12 步、一个终审 Human；
- `feishu-schema.json`：岗位、人才库和面试记录字段；
- `dashboard-spec.inputs.example.json`：按人才库、面试记录两个事实源分别配置的看板视图与指标建议；跨表招聘漏斗需在外部 BI 中按可靠关联构建；
- `programs/stage_resume_files.py`：飞书附件安全归档；
- `programs/validate_candidate_assessments.py`：简历评估聚合校验；
- `programs/assessment_repair_pipeline.py`：从完整校验错误构造局部返修请求并按候选人索引安全合并；
- `programs/persist_interview_handoffs.py`：按面试 Feishu record id 持久化私有脱敏评估；
- `programs/validate_interview_evidence.py`：跨 Workflow 面试/人才记录关联校验；
- `programs/validate_hiring_conclusions.py`：招聘结论证据校验；

## 测试

```powershell
pytest --override-ini addopts= -p no:cacheprovider -q resume-approval/tests resume-interview-preparation/tests
```

测试覆盖两个 G4 图、Program 严格 JSON 规范化、稳定需求 ID 规范化、零输出硬门禁、List foreach、分离的 Human 生命周期、附件边界、OCR 隔离、跨运行关联、面试证据完整性和证据不足的录用结论阻断。
