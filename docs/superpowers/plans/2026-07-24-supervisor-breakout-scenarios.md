# Supervisor Breakout Scenarios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run and document two auditable multi-turn demonstrations showing how the background supervisor changes a CEO's CI/CD decision process and a legal counsel's Agent-governance learning path.

**Architecture:** A scenario runner sends sequential OpenAI-compatible requests to a real Haitun Session, snapshots persisted supervisor/profile state after every turn, and writes raw evidence per persona. If real upstream execution fails, a separate deterministic runner exercises the existing advice validation, prompt rendering, map, and heatmap paths and labels all fallback evidence as mocked. A report builder assembles complete transcripts, supervisor JSON, state deltas, breakout timelines, and limitations without blending evidence modes.

**Tech Stack:** Python 3.14, anyio, aiohttp, JSON/YAML, psi-agent HTTP/SSE protocol, pytest, Ruff, Markdown.

---

## File Structure

- Create `examples/haitun-workspace/demo_supervisor_scenarios.py`: scenario definitions, real HTTP execution, SSE decoding, state snapshots, deterministic fallback, and raw evidence serialization.
- Create `tests/integration/test_haitun_supervisor_scenarios.py`: scenario shape, isolation, evidence labeling, and deterministic smoke coverage.
- Create `artifacts/supervisor-scenarios/raw/`: generated per-turn JSON evidence; excluded from commits unless explicitly selected.
- Create `artifacts/supervisor-scenarios/supervisor-breakout-report.md`: generated complete report.
- Modify `examples/haitun-workspace/AGENTS.md`: document how to reproduce the evaluation only if the new runner becomes a maintained example.

## Task 1: Define Scenarios and Evidence Schema

**Files:**
- Create: `examples/haitun-workspace/demo_supervisor_scenarios.py`
- Test: `tests/integration/test_haitun_supervisor_scenarios.py`

- [ ] **Step 1: Write failing scenario-schema tests**

Load the runner with `compile` and `exec`. Assert `SCENARIOS` has exactly `ceo-cicd` and `legal-agent-governance`, the required user/profile IDs, respectively 5 and 7 user turns, and no main Session ID starts with `supervisor-`. Assert the evidence record fields include `mode`, `user_message`, `assistant_message`, `supervisor_input`, `raw_advice`, `validated_advice`, `prompt_advice_injected`, `profile`, `heatmap_before`, `heatmap_after`, `map_before`, `map_after`, and `errors`.

- [ ] **Step 2: Run the test and verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py -k schema
```

Expected: failure because the runner does not exist.

- [ ] **Step 3: Implement immutable scenario definitions**

Use two frozen dataclasses, `Scenario` and `Turn`, and the exact identities from the approved specification. CEO turns must progress from the yes/no decision through cost, company facts, staged recommendation, and pilot metrics. Legal turns must progress from the Agent concept through comparison, legal domains, governance controls, policy drafting, and a high-autonomy incident stress test.

- [ ] **Step 4: Implement evidence-record defaults**

Provide `new_turn_evidence(...) -> dict[str, Any]` that initializes every required field and uses `mode="REAL"` or `mode="DETERMINISTIC MOCK"`. Never infer REAL from missing mode.

- [ ] **Step 5: Run tests, Ruff, and commit**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py -k schema
uv run ruff check examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
git add -- examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
git commit -m "test(haitun): define supervisor evaluation scenarios"
```

## Task 2: Capture Real Multi-Turn Evidence

**Files:**
- Modify: `examples/haitun-workspace/demo_supervisor_scenarios.py`
- Modify: `tests/integration/test_haitun_supervisor_scenarios.py`

- [ ] **Step 1: Write failing SSE and snapshot tests**

Feed a fixture containing multiple `data:` chunks plus `[DONE]`; assert the decoder returns the complete assistant message and preserves an upstream error. Create temporary profile, latest-advice, map, and heatmap files; assert `snapshot_state()` returns parsed copies and does not mutate the source files.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py -k "sse or snapshot"
```

- [ ] **Step 3: Implement the real request loop**

Use `aiohttp.ClientSession.post(..., json=body)` against a supplied Session URL. Send one turn at a time with `stream=True`, stable `user_id`, stable `profile_id`, and the current user message. Do not send prior assistant text in the supervisor payload; normal main conversation continuity remains the Session's responsibility. Decode SSE, store the complete visible response, and append transport/upstream errors verbatim.

- [ ] **Step 4: Snapshot prompt and stores after every turn**

Read the main Session JSONL to capture whether its system message contains `## 旁路监督建议`. Hash the user ID with SHA-256, then copy the user's `latest-advice.json`, domain heatmaps, applicable shared maps, and `_profile.md` into the turn evidence. Record before/after snapshots separately.

- [ ] **Step 5: Enforce isolation in recorded supervisor input**

Record only the structural allowlist and persisted aggregate values. Add a recursive assertion rejecting keys or values that contain `assistant`, `reasoning`, `draft`, `tool_calls`, `tool result`, or a `messages` array.

- [ ] **Step 6: Run focused tests and commit**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py -k "real or sse or snapshot or isolation"
uv run ruff check examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
git add -- examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
git commit -m "feat(haitun): capture real supervisor scenario evidence"
```

## Task 3: Add Deterministic Fallback

**Files:**
- Modify: `examples/haitun-workspace/demo_supervisor_scenarios.py`
- Modify: `tests/integration/test_haitun_supervisor_scenarios.py`

- [ ] **Step 1: Write failing fallback tests**

Make the real request function raise `ConnectionError`. Assert the runner preserves a REAL failure record, then creates distinct `DETERMINISTIC MOCK` turns. Assert each scenario has at least one validated breakout and distinct user-hash heatmap paths.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py -k fallback
```

- [ ] **Step 3: Implement deterministic supervisor fixtures**

Return schema-valid advice per turn. CEO advice progresses from observation to `reframe` and `operationalize`; legal advice progresses from `deepen` to `broaden`, `cross_domain`, and `operationalize`. Each result must pass the real `validate_advice()` function and update state through the real `SupervisorStore`/`update_heatmap` paths.

- [ ] **Step 4: Implement deterministic main responses**

Generate complete Chinese responses that first answer the immediate question, then integrate only the validated directions. The CEO output must conclude with a staged adoption decision and measurable pilot. The legal output must include a concise company policy with scope, risk tiers, permissions, approvals, data, logs, suppliers, incidents, and accountability. Label these responses as mocked in evidence, not inside the simulated user's dialogue.

- [ ] **Step 5: Verify fallback and commit**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py -k fallback
uv run ruff check examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
git add -- examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
git commit -m "feat(haitun): add deterministic supervisor scenario fallback"
```

## Task 4: Run Both Scenarios and Generate the Report

**Files:**
- Generate: `artifacts/supervisor-scenarios/raw/ceo-cicd.json`
- Generate: `artifacts/supervisor-scenarios/raw/legal-agent-governance.json`
- Generate: `artifacts/supervisor-scenarios/supervisor-breakout-report.md`

- [ ] **Step 1: Start a fresh AI and main Session**

Use normal main Session IDs such as `demo-ceo-main` and `demo-legal-main`. Reuse the configured provider without printing its secret. Confirm both sockets respond before sending scenario turns.

- [ ] **Step 2: Run real mode**

```powershell
uv run --no-cache python examples/haitun-workspace/demo_supervisor_scenarios.py --mode real --session-url http://127.0.0.1:<port>
```

Expected: both scenarios complete, or every failed real turn records an exact error and triggers fallback.

- [ ] **Step 3: Run deterministic fallback where required**

```powershell
uv run --no-cache python examples/haitun-workspace/demo_supervisor_scenarios.py --mode fallback
```

Expected: both scenario files contain complete mocked arcs while retaining separate real failure records.

- [ ] **Step 4: Generate the Markdown report**

The report must contain complete dialogue, every raw advice object, a per-turn breakout timeline, before/after heatmap and map snapshots, isolation evidence, prompt-injection evidence, real-versus-mock labels, error impacts, achieved behavior, limitations, and reproduction commands.

- [ ] **Step 5: Validate report completeness**

Parse both raw JSON files and assert the required turn counts, complete assistant messages, at least one breakout per scenario, distinct heatmap roots, and all report headings. Search the report for ambiguous evidence labels and reject an unlabeled mock section.

## Task 5: Verification and Handoff

**Files:**
- Modify if maintained example: `examples/haitun-workspace/AGENTS.md`

- [ ] **Step 1: Run focused verification**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_scenarios.py tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_profile.py
uv run ruff check examples/haitun-workspace/demo_supervisor_scenarios.py tests/integration/test_haitun_supervisor_scenarios.py
uv run ty check examples/haitun-workspace/demo_supervisor_scenarios.py
```

- [ ] **Step 2: Inspect generated evidence**

Confirm the report and raw JSON exist, parse successfully, contain no API key, and keep real failures separate from mocked completions. Confirm no raw main-Agent reasoning or tool results appear in supervisor input evidence.

- [ ] **Step 3: Document reproducibility if retained**

Add the exact runner commands and artifact locations to `examples/haitun-workspace/AGENTS.md`; do not modify top-level README files for a workspace-specific evaluation.

- [ ] **Step 4: Commit maintained code and documentation**

```powershell
git add -- examples/haitun-workspace/demo_supervisor_scenarios.py examples/haitun-workspace/AGENTS.md tests/integration/test_haitun_supervisor_scenarios.py
git commit -m "docs(haitun): document supervisor breakout evaluation"
```

Generated artifacts remain local evidence unless the user explicitly requests committing them.
