---
name: heartbeat
description: Periodic heartbeat task to check HEARTBEAT.md and execute its contents
cron: "*/30 * * * *"
---

# Heartbeat Task

Read the HEARTBEAT.md file from the workspace root and follow its instructions.

## Instructions

1. Read `HEARTBEAT.md` from the workspace root using the `read` tool
2. If HEARTBEAT.md contains tasks or instructions, execute them
3. If HEARTBEAT.md is empty or contains only comments, reply with `HEARTBEAT_OK`
4. If something needs attention, do NOT include "HEARTBEAT_OK"; reply with the alert text instead

## Notes

- This task runs every 30 minutes
- Keep your responses brief and focused
- Do not repeat old tasks from prior chats
- Only act on what is currently in HEARTBEAT.md
