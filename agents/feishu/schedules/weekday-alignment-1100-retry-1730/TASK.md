---
name: weekday-alignment-1100-retry-1730
description: 会后自动获取日会原始全文转写并分析, 产出本场评价与后续建议（17:30 补偿重跑，主任务已投递则自动跳过）
cron: "30 17 * * 1,3,5"
visibility: silent
fire: tool
tool: meeting_pipeline_run
tool_args: {"meeting_name":"weekday-alignment-1100","meeting_code":"42654699903"}
---

本任务由调度器直接调用 meeting_pipeline_run（fire: tool，不经过模型）；上面的 tool_args 即全部入参。

- 做什么：取「日会」最新已完成场次的原始转写，按 config/meeting-sop.yaml 的生效条目逐条判定；产出「本场 SOP 判定 + 本场会议评价 + 后续建议」，投递给 config/meeting-automation.yaml 的收件人。
- 口径在哪：config/meeting-sop.yaml（可编辑的判定条目，改口径只改这里）+ skills/meeting-sop/weekday-alignment/SKILL.md（判定纪律与输出结构）。
- 失败怎么办：按 record_file_id 去重；失败告警发给 alert_recipients；补跑/补发用 meeting_pipeline_replay(meeting_name, record_file_id)。
- 本条是 17:30 的补偿重跑：主任务（0 13 * * 1,3,5）已成功投递时，本次按 record 去重自动跳过，不重复投递。
