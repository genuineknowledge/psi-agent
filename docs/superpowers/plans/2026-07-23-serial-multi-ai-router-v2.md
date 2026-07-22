# Serial Multi-AI Router v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved socket-only Router with exactly three serial subtasks, Session-owned tool execution, shared planning/aggregation socket, and default-socket fallback.

**Architecture:** Add focused protocol, planner, client, orchestrator, server, prompts, and entry modules under `src/psi_agent/router/`. Keep branch histories in memory keyed by Session ID; Session remains the sole ToolRegistry owner and adds internal routing metadata.

**Tech Stack:** Python 3.14, anyio, aiohttp, tyro, loguru, pytest/pytest-asyncio, existing socket and SSE helpers.

---

### Task 1: Protocol and configuration

**Files:** Create `src/psi_agent/router/protocol.py`; create `tests/psi_agent/router/test_protocol.py`.

- [ ] Write failing tests for `list[tuple[str, str]]` upstream validation, duplicate sockets, exactly-three branch invariants, valid statuses, and rejecting `default_socket == session_socket`.
- [ ] Run `uv run pytest tests/psi_agent/router/test_protocol.py -v`; confirm missing-module failures.
- [ ] Implement precise dataclasses for `PlannedTask`, `BranchState`, `RoutingRun`, `ToolCallOrigin`, and immutable route configuration with validation.
- [ ] Re-run the focused tests; commit `feat(router): add protocol types`.

### Task 2: Socket SSE client and prompts

**Files:** Create `src/psi_agent/router/client.py`, `src/psi_agent/router/prompts.py`; create `tests/psi_agent/router/test_client.py`, `tests/psi_agent/router/test_prompts.py`.

- [ ] Write failing tests for Unix/TCP connector selection, one-choice SSE parsing, `[DONE]`, non-200 errors, malformed JSON, cancellation cleanup, planner/repair/branch/aggregation prompt content, and removal of `routing`/`model`.
- [ ] Run the focused tests and verify they fail before implementation.
- [ ] Implement `RouterClient` with `aiohttp`, `resolve_connector_and_endpoint`, explicit stream closing under shield, defensive JSON guards, and unknown-parameter passthrough. Implement bounded prompt builders with socket descriptions only.
- [ ] Re-run tests; commit `feat(router): add socket client and prompts`.

### Task 3: Strict planner

**Files:** Create `src/psi_agent/router/planner.py`; create `tests/psi_agent/router/test_planner.py`.

- [ ] Write failing tests for exactly three JSON tasks, repeated sockets, unconfigured socket rejection, malformed JSON, and one repair attempt.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Implement `Planner.plan()` against `router_socket`, strict dict/list/string validation, socket whitelist validation, and exactly one repair request.
- [ ] Re-run tests; commit `feat(router): add strict planner`.

### Task 4: Serial orchestration and tool mapping

**Files:** Create `src/psi_agent/router/orchestrator.py`; create `tests/psi_agent/router/test_orchestrator.py`.

- [ ] Write failing tests proving branch 1 completes before branch 2, branch 3 sees only earlier final answers, every branch sees the complete tools list, repeated tool rounds work, tool-call IDs are rewritten/restored, and mismatched results fail.
- [ ] Run the focused tests and confirm they fail for missing state-machine behavior.
- [ ] Implement the Session-ID run store, one-active-branch state machine, private histories, serial subanswer transfer, global tool-call mapping, max rounds, aggregation through `router_socket` without tools, and run deletion on completion.
- [ ] Re-run tests; commit `feat(router): add serial orchestration`.

### Task 5: Server and default fallback

**Files:** Create `src/psi_agent/router/server.py`; create `tests/psi_agent/router/test_server.py`.

- [ ] Write failing tests for request validation, normal SSE, planner/branch/aggregation failure, one fallback to `default_socket`, HTTP-before-prepare errors, SSE-after-prepare errors, and parameter preservation.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement the aiohttp handler, fallback proxy, one-choice SSE encoding, per-chunk DEBUG logging, shielded startup/shutdown cleanup, and timeout/TTL handling.
- [ ] Re-run tests; commit `feat(router): add server and fallback`.

### Task 6: Entry point and CLI

**Files:** Create `src/psi_agent/router/entry.py`, `src/psi_agent/router/__init__.py`; modify `src/psi_agent/cli.py`; create `tests/psi_agent/router/test_entry.py`.

- [ ] Write failing tests for `Router.run()` validation, first-line `setup_logging`, cancellation cleanup, and `psi-agent router --help` exposing socket-only options and `list[tuple[str, str]]` upstream.
- [ ] Run focused tests and confirm failure.
- [ ] Implement lifecycle orchestration and add `Router` to the top-level Tyro union; keep models/provider/API keys out of Router.
- [ ] Run tests and `uv run psi-agent router --help`; commit `feat(router): expose router cli`.

### Task 7: Session metadata and integration

**Files:** Modify `src/psi_agent/session/agent.py` and `src/psi_agent/ai/server.py`; add mirrored unit tests and `tests/integration/test_serial_multi_ai_router.py`; update Router/AI/Session docs and README files.

- [ ] Write failing tests for stable `routing.session_id`, stripping internal routing at ordinary AI, and the complete three-socket scenario: branch 1 tool/answer, branch 2 tool/answer, branch 3 answer, shared aggregation, and exact socket mapping.
- [ ] Run focused and integration tests; confirm failure before wiring.
- [ ] Add only the metadata needed for continuation; preserve existing history, ToolRegistry, rollback, and single-choice SSE semantics; document serial tools and default fallback.
- [ ] Re-run integration tests; commit `feat(router): connect router to session`.

### Task 8: Verification

**Files:** Modify only files exposed by verification failures.

- [ ] Run `uv run ruff check .` and fix Router-related lint errors.
- [ ] Run `uv run ruff format --check .`.
- [ ] Run `uv run ty check` without adding new ignores.
- [ ] Run `uv run pytest -v`, `uv build`, and `uv run psi-agent router --help`.
- [ ] Run `git diff --check` and inspect status to ensure pre-existing `.gitignore` and `package-lock.json` changes remain untouched.
