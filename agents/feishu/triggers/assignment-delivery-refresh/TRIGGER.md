---
name: assignment-delivery-refresh
description: 定期刷新当前安排者名下任务的已读和接收进度卡
event: haitun.assignment.delivery_check
source: haitun
filter: {}
visibility: silent
run_once: false
fire: tool
tool: assignment_delivery_refresh
tool_args: {}
---

由合成事件按飞书用户路由，不经过 LLM，工具只处理 Memory 中尚未结束且未超过七天的投递记录。
