---
name: heartbeat
description: Periodic self-check task. Runs every 30 minutes to verify the agent is alive.
cron: "*/30 * * * *"
---

# Heartbeat Task

This task runs every 30 minutes.

## Instructions

1. Respond with exactly `HEARTBEAT_OK` and nothing else — no explanation, no extra text.
2. Do not initiate any other action unless explicitly scheduled.

## Purpose

The heartbeat serves two functions:
- Proves the agent loop is alive and responding
- Provides a regular opportunity to run lightweight maintenance (stale file cleanup)

## Expected response

```
HEARTBEAT_OK
```
