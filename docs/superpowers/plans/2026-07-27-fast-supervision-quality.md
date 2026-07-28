# Fast Supervision and Long-Term Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound live supervision to 10 seconds, add safe same-user cache fallback, and preserve complete heatmap history while allowing explicit active-branch rollback.

**Architecture:** `SystemPrompt` uses a 10-second Workspace hook budget. `SupervisorManager` first checks exact cache eligibility, then tries live compact Advice, and finally returns a global-profile fallback. Full map and history enrichment runs after Advice selection and never blocks the main answer. `SupervisorStore` stores append-only heatmap events plus mutable active branch state.

**Tech Stack:** Python 3.14, anyio, aiohttp, YAML/JSON, pytest, Ruff, ty.

---

### Task 1: Fast timeout and fallback observability

**Files:** `src/psi_agent/session/system_prompt.py`, `tests/psi_agent/session/test_session.py`

- [ ] Add a failing test asserting the workspace default before-turn budget is 10 seconds and explicit constructor values still override it.
- [ ] Run the focused test and verify it fails against the current 60-second default.
- [ ] Change the default to 10 seconds and retain cancellation propagation and recoverable timeout behavior.
- [ ] Run the full SystemPrompt test file and Ruff.

### Task 2: Exact Advice cache eligibility

**Files:** `examples/haitun-workspace/systems/supervisor_protocol.py`, `examples/haitun-workspace/systems/supervisor.py`, `tests/integration/test_haitun_supervisor.py`

- [ ] Add failing tests for same-user/same-profile/same-domain cache hit, stale cache rejection, cross-user rejection, and explicit depth change rejection.
- [ ] Implement normalization helpers for domain/topic/intent and `is_cache_eligible(raw, message, now)` with a 10-minute TTL.
- [ ] Return cache Advice with `diagnostics.source="cache"` and a reason code; never reuse unavailable Advice.
- [ ] Run focused protocol and manager tests.

### Task 3: Active branch rollback with immutable history

**Files:** `examples/haitun-workspace/systems/supervisor_store.py`, `examples/haitun-workspace/systems/supervisor.py`, `tests/integration/test_haitun_supervisor.py`

- [ ] Add failing tests proving historical events remain after a deep-to-simple transition and only the affected branch's active strategy changes.
- [ ] Extend heatmap state with append-only `history` and `active_branches` fields; preserve existing counters for compatibility.
- [ ] Detect explicit simplification/deepening signals and update active branch state with a rollback/advance event without deleting history.
- [ ] Run store and manager tests, then inspect YAML round-trip output.

### Task 4: Background enrichment boundary

**Files:** `examples/haitun-workspace/systems/supervisor.py`, `examples/haitun-workspace/systems/system.py`, `tests/integration/test_haitun_supervisor.py`

- [ ] Add failing tests proving Advice is returned when enrichment raises and that enrichment failure does not change the returned Advice source.
- [ ] Split supervisor work into fast Advice selection and best-effort enrichment; use an anyio task started after current Advice selection where a task group is available.
- [ ] Keep atomic writes and cancellation safety; log duration, cache source, and enrichment failure without raw identity/question data.
- [ ] Run focused integration tests.

### Task 5: Regression and latency evidence

**Files:** `tests/integration/test_haitun_supervisor_e2e.py` (create), `examples/haitun-workspace/AGENTS.md`

- [ ] Add a mock Session/AI E2E test asserting main prompt generation starts after at most the fast budget, cache fallback works, and map enrichment happens independently.
- [ ] Add a deterministic latency report with cold, warm, cache-hit, timeout, and non-learning cases.
- [ ] Document the B behavior and reproduction command.
- [ ] Run all supervisor/profile/session tests, Ruff, ty, and the latency evidence test.

### Task 6: Real multi-turn experiment

- [ ] Start fresh AI and Session processes with a non-`supervisor-` main Session ID.
- [ ] Run at least 10 turns across two users and two domains, recording P50/P95 before-turn and first-token latency, cache hit rate, live Advice success, and enrichment failures.
- [ ] Compare against the previous 60-second baseline and record upstream errors without fabricating success.

