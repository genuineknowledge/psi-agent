# Fusion-Guard Dolphin Host Adaptation Plan

## Goal

Keep Dolphin as a host-only integration point for an out-of-tree Fusion-Guard plugin.

## Tasks

- [x] Add `psi_agent.session.runtime_context` with a read-only `SessionToolContext`.
- [x] Store `workspace_path`, `history_path`, resolved `session_id`, and `ai_socket` on `SessionAgent`.
- [x] Write history immediately after appending the latest `user` message.
- [x] Wrap workspace tool execution with `push_session_tool_context(...)`.
- [x] Add focused session tests for context visibility and early user-message persistence.
- [x] Keep Fusion-Guard plugin implementation, workspace tools, policy parsing, denial formatting, and secure bash runner out of the Dolphin repository.

## Verification

Run:

```bash
uv run pytest tests/psi_agent/session/test_runtime_context.py tests/psi_agent/session/test_agent.py -q
```

The Fusion-Guard repository owns separate checks for its Dolphin workspace and adapter package.
