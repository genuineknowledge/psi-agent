# Fallback Strong Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare `Qwen Primary → DeepSeek high Backup` Fallback against an independent `DeepSeek-V4-Pro` baseline using `reasoning_effort=high`.

**Architecture:** Add a dedicated baseline recording proxy that forwards to the existing DeepSeek AI service. Keep the Fallback primary and backup proxies unchanged, apply the deterministic fault identity to the baseline proxy or Fallback primary according to the active condition, and validate each condition against its own expected attempt topology.

**Tech Stack:** Python 3.14, AnyIO, aiohttp, YAML, pytest.

## Global Constraints

- Preserve the four-condition paired experiment and deterministic `(seed, case_id, trial)` fault plan.
- Keep Fallback ordered as Qwen primary followed by DeepSeek high backup.
- Configure the one-target baseline as DeepSeek high without changing unrelated repository files.
- Use `anyio`; do not introduce native `asyncio` APIs.

---

### Task 1: Register the independent strong baseline topology

**Files:**
- Modify: `tests/evals/fallback_reliability/test_experiment.py`
- Modify: `tests/evals/fallback_reliability/experiment.py`
- Modify: `tests/evals/fallback_reliability/experiment.yml`

**Interfaces:**
- Consumes: `parse_experiment(raw) -> ExperimentDocument`
- Produces: three recording proxies with roles `baseline`, `primary`, and `backup`

- [x] **Step 1: Write the failing configuration assertions**
- [x] **Step 2: Run `uv run pytest -q tests/evals/fallback_reliability/test_experiment.py` and confirm the old shared-primary topology fails**
- [x] **Step 3: Update topology validation and YAML with a DeepSeek-high baseline proxy**
- [x] **Step 4: Re-run the configuration tests**

### Task 2: Record and analyze condition-specific first attempts

**Files:**
- Modify: `tests/evals/fallback_reliability/test_fault_proxy.py`
- Modify: `tests/evals/fallback_reliability/test_analyze.py`
- Modify: `tests/evals/fallback_reliability/fault_proxy.py`
- Modify: `tests/evals/fallback_reliability/analyze.py`
- Modify: `tests/integration/test_fallback_reliability_eval.py`

**Interfaces:**
- Consumes: `RecordingConfig.proxies`
- Produces: paired synthetic faults on either the baseline proxy or Fallback primary, with backup activation only for `fallback-faulted`

- [x] **Step 1: Add failing tests for baseline-role fault injection and condition-specific attempt order**
- [x] **Step 2: Run the focused tests and confirm the two-role implementation fails**
- [x] **Step 3: Extend proxy roles, recorder expectations, analyzer validation, and integration fixtures**
- [x] **Step 4: Run focused tests until green**

### Task 3: Update experiment documentation and verify

**Files:**
- Modify: `tests/evals/fallback_reliability/README.md`
- Modify: `tests/evals/fallback_reliability/MODEL_CONFIG.md`

**Interfaces:**
- Consumes: the implemented YAML topology
- Produces: reproducible instructions that describe the strong baseline and invalidate reuse of the historical run

- [x] **Step 1: Update model/topology documentation**
- [x] **Step 2: Run `uv run pytest -q tests/evals/fallback_reliability tests/integration/test_fallback_reliability_eval.py`**
- [x] **Step 3: Run Ruff on all modified Python files**
- [x] **Step 4: Review the final diff for unrelated changes**
