---
name: weekday-alignment
description: 会后自动获取周中对齐会原始全文转写并分析
cron: "0 12 * * 1,3,5"
visibility: silent
fire: tool
tool: meeting_pipeline_run
tool_args: {"meeting_name":"weekday-alignment","meeting_code":"57152787045"}
---
