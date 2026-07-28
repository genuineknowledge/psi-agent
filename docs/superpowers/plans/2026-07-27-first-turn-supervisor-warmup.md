# First-Turn Supervisor Warmup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the first learning answer without waiting, warm the supervisor after that answer, require supervision from the second learning turn, and produce auditable CEO/legal experiments and documentation.

**Architecture:** Durable per-user participation state in `SupervisorStore` determines first versus later eligible turns. `system_before_turn` skips only a true first turn and records warmup intent; `system_after_turn` primes the supervisor from the user message alone. Later turns use a 20-second live/cache/unavailable path. Structured metrics and experiment artifacts document latency and quality.

**Tech Stack:** Python 3.14, anyio, aiohttp/SSE, JSON/YAML, pytest, Ruff, ty, Markdown.

---

### Task 1: Twenty-second budget

**Files:** `src/psi_agent/session/system_prompt.py`, `tests/psi_agent/session/test_session.py`

- [ ] Change the failing default-budget assertion from 10 to 20 seconds and verify RED.
- [ ] Change the production default to 20 seconds and verify the full SystemPrompt suite.

### Task 2: Durable participation state

**Files:** `examples/haitun-workspace/systems/supervisor_store.py`, `tests/integration/test_haitun_supervisor.py`

- [ ] Add failing tests for a new user, warmup requested/completed/failed states, eligible turn count, and process-restart reconstruction.
- [ ] Add atomic `load_participation` and `save_participation` under each hashed user directory.
- [ ] Keep raw questions and identities out of participation files.

### Task 3: First-turn skip and after-turn warmup

**Files:** `examples/haitun-workspace/systems/system.py`, `examples/haitun-workspace/systems/supervisor.py`, `tests/integration/test_haitun_supervisor.py`, `tests/integration/test_haitun_profile.py`

- [ ] Add failing tests proving first before-turn returns `{}` without chat, after-turn warmup receives only the user message, and second before-turn invokes live/cache supervision.
- [ ] Add `SupervisorManager.should_warmup`, `prime`, and durable state transitions.
- [ ] Compose warmup with the existing profile after-turn hook without passing the assistant message into supervisor code.
- [ ] Preserve schedule and recursive-session bypass.

### Task 4: Structured metrics

**Files:** `examples/haitun-workspace/systems/supervisor.py`, `examples/haitun-workspace/systems/supervisor_store.py`, `tests/integration/test_haitun_supervisor.py`

- [ ] Add failing tests for metric fields and absence of raw ID/question/answer values.
- [ ] Record Advice source, elapsed milliseconds, cache reason, warmup status, revisions, and event counts.
- [ ] Persist per-turn metrics as append-only JSONL under the hashed user directory.

### Task 5: CEO/legal experiment runner and reports

**Files:** `examples/haitun-workspace/demo_supervisor_scenarios.py`, `tests/integration/test_haitun_supervisor_scenarios.py`, generated `artifacts/supervisor-scenarios/`

- [ ] Extend scenarios to 8–12 turns per identity and record first-turn/warmup/second-turn participation.
- [ ] Attempt real mode first and preserve exact upstream failures.
- [ ] Generate a complete experiment evaluation report and a separate stability/engineering report with P50/P95 and source distributions.
- [ ] Verify user isolation, Advice isolation, map revisions, heatmap histories, and explicit real/mock labels.

### Task 6: Feature README and verification

**Files:** `examples/haitun-supervisor-workspace/README.md`, `examples/haitun-workspace/AGENTS.md`

- [ ] Document purpose, architecture, data flow, influence on the main Agent, identities, isolation, five breakout modes, cache, warmup, maps, heatmaps, errors, local testing, metrics, experiments, limitations, and roadmap.
- [ ] Run Session, supervisor, profile, scenario, Ruff, format, ty, secret scan, and artifact completeness checks.
- [ ] Commit maintained code and documentation; keep runtime state and generated reports uncommitted unless requested.
