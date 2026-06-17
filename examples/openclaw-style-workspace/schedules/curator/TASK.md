---
name: curator
cron: "0 2 */7 * *"
description: 定时触发 curator 后台任务
---

# Curator Tick

This task runs every 7 days.

## Instructions

1. Respond with exactly `__PSI_CURATOR_TICK__` and nothing else — no explanation, no extra text, no tool calls.
2. Do not initiate any other action.

## Purpose

This tick signal is detected by the system's `after_turn` hook, which runs the skill and flow curator in the background.

## Expected response

```
__PSI_CURATOR_TICK__
```
