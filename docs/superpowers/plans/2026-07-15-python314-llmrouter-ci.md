# Python 3.14 LLMRouter CI Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all GitHub workflows reproducibly build the Python 3.14 LLMRouter dependency chain from the committed lock file.

**Architecture:** Add a shared PyO3 compatibility variable to four workflows, pin/cache uv setup, install Rust before frozen synchronization, and add one CI import/resource smoke test. A static pytest contract parses the YAML and prevents future workflows from silently dropping these requirements.

**Tech Stack:** GitHub Actions YAML, setup-uv v7, uv 0.11.23, Rust stable, PyYAML, pytest.

---

### Task 1: Add a failing workflow contract test

**Files:**
- Create: `tests/test_ci_workflows.py`

- [ ] Parse the four workflow files with `yaml.safe_load`; assert workflow-level `PYO3_USE_ABI3_FORWARD_COMPATIBILITY == "1"`, every setup-uv step pins `version: "0.11.23"`, enables cache, and watches `uv.lock` plus `pyproject.toml`.
- [ ] For every job containing `uv sync`, assert the command is exactly `uv sync --frozen` and an earlier step uses `dtolnay/rust-toolchain@stable`.
- [ ] Assert `ci.yml` contains a step named `Verify LLMRouter environment` whose command references LiteLLM, `LLMMultiRoundRouter`, `_decompose_and_route`, and all three prompt filenames.
- [ ] Run the new test and verify RED against the existing workflows.

### Task 2: Update CI and packaging workflows

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/nuitka.yml`
- Modify: `.github/workflows/pyinstaller.yml`
- Modify: `.github/workflows/auto-alpha-tag.yml`

- [ ] Add the workflow-level compatibility variable to all four files.
- [ ] Add `version: "0.11.23"`, cache enablement, and dependency cache globs to every setup-uv step.
- [ ] Add Rust stable before every synchronization and change all `uv sync` commands to `uv sync --frozen`.
- [ ] Add the two-command LLMRouter API/prompt smoke step after the lint job sync.
- [ ] Preserve triggers, job dependencies, matrices, builds, artifacts, tags, and publishing.

### Task 3: Verify static and repository behavior

**Files:**
- Verify: all files from Tasks 1-2.

- [ ] Run `tests/test_ci_workflows.py` and verify GREEN.
- [ ] Run PyYAML parsing over all `.github/workflows/*.yml` files.
- [ ] Run ruff and ty on the new test and the existing AI source/tests.
- [ ] Run the AI suite to ensure workflow-only changes did not mask environment regressions.
- [ ] Run `git diff --check`, `git status --short`, and `git diff --stat`; confirm README files are untouched.
