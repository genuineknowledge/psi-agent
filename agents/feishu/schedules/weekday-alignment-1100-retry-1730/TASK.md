---
name: weekday-alignment-1100-retry-1730
description: 会后自动获取日会原始全文转写并分析, 产出本场评价与后续建议
cron: "30 17 * * 1,3,5"
visibility: silent
fire: tool
tool: meeting_pipeline_run
tool_args: {"meeting_name":"weekday-alignment-1100","meeting_code":"42654699903"}
---
