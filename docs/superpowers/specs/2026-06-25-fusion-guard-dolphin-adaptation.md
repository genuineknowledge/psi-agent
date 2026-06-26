# Fusion-Guard Dolphin Host Adaptation Design

**Date**: 2026-06-25
**Status**: corrected

## Goal

Expose the minimal Dolphin-Agent host surface that an out-of-tree Fusion-Guard plugin needs, without placing Fusion-Guard plugin code in the Dolphin repository.

Dolphin remains responsible for session orchestration, history, tool loading, and the AI socket. Fusion-Guard remains responsible for its workspace tool, intent analysis prompt/rule parsing, safety decisions, and policy integration.

## Scope

Included in Dolphin:

- A read-only `SessionToolContext` available while workspace tools execute.
- Immediate JSONL history write after the current `user` message is appended.
- Documentation of the runtime context contract in the Session layer.
- Tests proving tools can read the context and that the current `user` message is already on disk before tool execution.

Excluded from Dolphin:

- `psi_agent.fusion_guard` package code.
- Fusion-Guard prompt builders, rule parsers, denial message helpers, policy installers, or secure bash runner implementation.
- A dedicated Fusion-Guard example workspace inside Dolphin.

The Fusion-Guard repository owns its Dolphin workspace. That workspace should follow `examples/a-serper-mcp-workspace`: a workspace with only `tools/`.

## Runtime Contract

`psi_agent.session.runtime_context.SessionToolContext` is available only during a tool call and contains:

- `session_id`
- `workspace_path`
- `history_path`
- `history_messages`
- `latest_user_message`
- `ai_socket`

Workspace tools may read this context, but they must not mutate Dolphin session memory directly.

## History Ordering

On each `SessionAgent.run()` turn:

1. Dolphin builds the system prompt if needed.
2. Dolphin yields pending schedule chunks if any.
3. Dolphin appends the latest `user` message to in-memory history.
4. Dolphin immediately writes the current history to `workspace/histories/{session_id}.jsonl`.
5. Dolphin sends the final in-memory history to the AI socket and continues the normal agent loop.
6. On final assistant completion, Dolphin overwrites the JSONL with the final turn state.

This gives external tools a stable on-disk view of the current user message while preserving Dolphin's final-history semantics.

## Testing

- `tests/psi_agent/session/test_runtime_context.py` verifies tool-time context and early user-message persistence.
- `tests/psi_agent/session/test_agent.py` verifies error turns still persist the early user message instead of silently losing the latest request.
