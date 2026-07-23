# Concurrent Fan-out Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace serial Router orchestration with concurrent upstream fan-out, deterministic result fan-in, Session-driven tool rounds, and ID-based tool-call deduplication.

**Architecture:** Router becomes stateless per request/round: copy the Session request to every upstream concurrently, collect complete SSE responses, aggregate text/reasoning/tool calls, and stream one response back. Session remains the sole tool executor and sends the updated history back for the next round. Existing socket transports and default fallback remain in use.

**Tech Stack:** Python 3.14, anyio task groups, aiohttp SSE, existing `RouterClient`, `SessionAgent`, pytest/ruff/ty.

---

### Task 1: Replace serial orchestration with concurrent fan-out

**Files:**
- Modify: `src/psi_agent/router/orchestrator.py`
- Test: `tests/psi_agent/router/test_orchestrator.py`

- [ ] Add failing tests proving all upstreams start before any result is aggregated, output order follows configured upstream order, and one failed upstream does not discard successful results.
- [ ] Add an `anyio.create_task_group()` dispatch path that sends identical request bodies to each upstream through `RouterClient`, stores results by upstream index, and awaits all tasks before aggregation.
- [ ] Remove planner/branch private-history assumptions from the request path while retaining cancellation-safe cleanup and `default_socket` fallback signaling.
- [ ] Run `uv run pytest -o addopts='' tests/psi_agent/router/test_orchestrator.py -q` and commit.

### Task 2: Implement deterministic aggregation and tool-call deduplication

**Files:**
- Modify: `src/psi_agent/router/orchestrator.py` or create `src/psi_agent/router/aggregator.py`
- Test: `tests/psi_agent/router/test_aggregator.py`

- [ ] Add failing tests for newline-joined content/reasoning, mixed text plus tool calls, first-definition-wins behavior, and retaining distinct IDs with identical name/arguments.
- [ ] Implement typed aggregation that scans results in upstream configuration order, ignores empty heartbeats, deduplicates by original `tool_call.id`, and emits `finish_reason="tool_calls"` whenever calls remain; otherwise emit `stop`.
- [ ] Ensure incomplete/malformed tool calls are excluded from executable output and all generated SSE chunks contain exactly one choice.
- [ ] Run focused aggregation tests and commit.

### Task 3: Adapt Router server response loop

**Files:**
- Modify: `src/psi_agent/router/server.py`
- Test: `tests/psi_agent/router/test_server.py`

- [ ] Add failing tests for one aggregated response per Session round, mixed content/tool-call SSE, partial upstream failure, all-upstream fallback, and single fallback invocation.
- [ ] Wire server requests to the concurrent orchestrator, stream the aggregate response, preserve request passthrough fields, strip only internal `routing`/`model` fields where required, and keep shielded startup/shutdown/`aclosing()` cleanup.
- [ ] Run server-focused tests and commit.

### Task 4: Make Session continue rounds through Router

**Files:**
- Modify: `src/psi_agent/session/agent.py`
- Test: `tests/psi_agent/session/test_agent.py`

- [ ] Add a failing integration-style test where Router round 1 returns deduplicated tool calls, Session executes each unique tool once, then round 2 returns the final aggregate answer.
- [ ] Verify the existing Session loop treats the aggregate `tool_calls` as one AI response, appends tool results to history, and issues the next request with the same `routing.session_id` and full tools.
- [ ] Ensure duplicate IDs do not cause duplicate tool execution and errors roll back conversation state according to existing Conversation semantics.
- [ ] Run Session-focused tests and commit.

### Task 5: Update CLI/config compatibility and documentation

**Files:**
- Modify: `src/psi_agent/router/protocol.py`, `src/psi_agent/router/entry.py`, `src/psi_agent/router/__init__.py`
- Modify: `src/psi_agent/router/AGENTS.md` or project docs if present
- Test: `tests/psi_agent/router/test_protocol.py`, `tests/psi_agent/router/test_entry.py`

- [ ] Add tests confirming the public config remains `session_socket`, `router_socket`, `default_socket`, and `upstream: list[tuple[str, str]]`; no model fields are introduced.
- [ ] Preserve `router_socket` as a compatibility field while documenting that concurrent fan-out uses `upstream` sockets for each round.
- [ ] Run CLI help and focused protocol tests, then commit.

### Task 6: Full verification and integration coverage

**Files:**
- Test: `tests/integration/test_serial_multi_ai_router.py` (rename or replace with `tests/integration/test_concurrent_multi_ai_router.py`)

- [ ] Replace serial assertions with concurrency assertions, including overlapping upstream execution, tool-call ID deduplication, Session round 2, and final aggregation.
- [ ] Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest -o addopts=''`, `uv build`, and `uv run psi-agent router --help`.
- [ ] Record any environment-only failures separately from code failures and commit final tests/documentation.
