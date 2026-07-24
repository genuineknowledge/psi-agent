# Dynamic Router Implementation Plan

> **For agentic workers:** Use TDD and review each task against `docs/superpowers/specs/2026-07-24-dynamic-router-design.md`.

**Goal:** Implement the final dynamic Planner → selected upstream → Aggregator flow without broadcasting every request to every upstream.

**Architecture:** `Planner` calls `router_socket` and validates a dynamic list of `{subtask, socket}` tasks. `Orchestrator` dispatches only selected sockets, concurrently where independent, then calls `router_socket` with specialist results for aggregation. `Session` remains the only tool executor.

## Task 1: Dynamic plan protocol

- Update `parse_plan()` and `Planner.plan()` to accept one or more tasks instead of exactly three.
- Keep strict root/task keys and configured-socket validation.
- Update planning and repair prompts to describe dynamic task count and full socket catalog.
- Add tests for one task, multiple tasks, repeated sockets, malformed output, and one repair attempt.

## Task 2: Directed subtask execution

- Change `Orchestrator.process()` to call Planner first.
- Build one request per planned task with the original messages, complete tools, and task-specific prompt.
- Dispatch only the selected sockets; preserve Planner order in the result array.
- Add logs showing the selected task count, subtask summaries, and exact sockets.
- Test that unselected upstreams receive no request and selected tasks can overlap.

## Task 3: Router-model aggregation

- Send subtask names and final subtask results to `router_socket`.
- Include the complete tools schema when the aggregate may produce tool calls.
- Deduplicate aggregate tool calls by original ID before returning to Session.
- Strengthen the aggregation prompt to return only the end-user answer, never routing JSON or internal thoughts.
- Test text-only, mixed text/tool-call, duplicate-ID, and malformed aggregation responses.

## Task 4: Session round-trip and fallback

- Verify Session executes aggregate tool calls once and sends the next round with the same `routing.session_id`.
- Verify partial upstream failures retain successful results and all-stage failures invoke `default_socket` once.
- Add an integration test covering Planner, two directed upstreams, Aggregator, Session tool execution, and a second round.

## Task 5: Structure and documentation

- Keep `server.py` and `__init__.py` as public boundaries.
- Use `routing.py` as the public routing facade, `aggregation.py` as the aggregation facade, and `prompts.py` for pure prompt builders.
- Keep legacy implementation modules only as compatibility imports until all internal imports are migrated.
- Run Ruff, format, Ty, focused Router/Session tests, integration tests, build, and CLI help.
