---
name: meeting-sop-weekday-alignment
description: "周中对齐会会议 SOP 判定引擎（口径在 config/meeting-sop.yaml，可编辑）。由 meeting_pipeline_run 在固定日会定时分析中显式注入；不用于人工会话。"
version: v0.1-draft
---

# 周中对齐会会议 SOP（判定引擎）

> 判定口径读 `config/meeting-sop.yaml`，用户可编辑，换公司/换会议只改该文件；本文保留
> 引擎与通用纪律，参数值与业务条目以该文件为准（与 todo-sop 同一模式）。业务条目
> （`rules.*`，稳定 ID `msop.*`）定稿前 `active: false`，不得当作已成立口径。
>
> 由 `meeting_pipeline_run` 按 `MeetingJob.analysis_sop_skills` 显式注入每次分块与
> 合成分析请求，替代把规则写死在系统提示词里的旧做法，让"按哪一版规则、哪一条判断"
> 可审计。

## 使用边界

- 仅由固定会议的调度分析链路（`meeting_pipeline_run`）注入，人工会话不得引用本文件
  冒充已生效的公司 SOP。
- 只判断**原始转写中明确出现**的事实；智能纪要仅辅助定位话题，不能作为 SOP 判断证据。
- 产出的是**候选与观察**：不写正式记录、不触发处罚或绩效，结论供固定收件人人工查看。

## 判定引擎（本文件生效部分）

1. **证据层级**：原文 > 纪要辅助；区分事实、发言人陈述、Agent 推断。
2. **SOP 符合性**：按 `meeting-sop.yaml` 的 `observation_axes`（会前/会中/会后）逐条给出
   `judgment_states` 四态之一（符合/部分符合/不符合/证据不足），并附原文依据
   （时间+发言人）；`active: false` 的条目与证据不足一律写"证据不足/待补充证据"，
   不允许"默认通过"，也不得臆测。
3. **负面观察**：描述可观察行为本身，不贴标签；给出 正确做法 / 立即补救 / 预防措施。
4. **输出归属**：每条判断可回到 会议号 + 日期 + 原文片段，不得出现无出处结论。
5. **规则引用**：涉及业务条目时引用 `meeting-sop.yaml` 中 `rules[].id`（如 `msop.prep.01`），
   不编造规则 ID。
