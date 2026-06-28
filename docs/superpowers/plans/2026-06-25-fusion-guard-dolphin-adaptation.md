# Fusion-Guard Dolphin Host Adaptation Plan

## Goal

Keep Dolphin as a host-only integration point for an out-of-tree Fusion-Guard plugin.

## Tasks

- [x] Add `psi_agent.session._protocol.SessionToolContext` with class-level context storage.
- [x] Store `workspace_path`, `history_path`, resolved `session_id`, and `ai_socket` on `SessionAgent`.
- [x] Write history immediately after appending the latest `user` message.
- [x] Wrap workspace tool execution with `SessionToolContext.push()`.
- [x] Add focused session tests for context visibility and early user-message persistence.
- [x] Keep Fusion-Guard plugin implementation, workspace tools, policy parsing, denial formatting, and secure bash runner out of the Dolphin repository.
- [x] Add a thin `examples/fusion-guard-security-workspace` wiring sample that contains only `tools/` and delegates to the out-of-tree Fusion-Guard adapter.

## Verification

Run:

```bash
uv run pytest tests/psi_agent/session/test_runtime_context.py tests/psi_agent/session/test_agent.py -q
```

The Fusion-Guard repository owns separate checks for its real Dolphin workspace and adapter package. Dolphin checks only the thin example workspace shape, external delegation, and missing-adapter message.
