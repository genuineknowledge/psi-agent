# LLMRouter Packaged Custom Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure `llmrouter-lib==0.3.1` to load psi-agent's packaged `custom_tasks` YAML prompts before constructing `LLMMultiRoundRouter`.

**Architecture:** The adapter resolves `psi_agent.ai/custom_tasks` as a package resource, validates the three required YAML files, then sets LLMRouter's private prompt-directory globals under the existing process lock. Tests prove ordering and resource validity; a built-wheel inspection proves the templates survive installation.

**Tech Stack:** Python 3.14, importlib.resources, pathlib only inside the synchronous third-party bridge, anyio, PyYAML, Hatchling, pytest, ruff, ty.

---

### Task 1: Specify packaged prompt resources

**Files:**
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`
- Verify: `src/psi_agent/ai/custom_tasks/agent_decomp_route.yaml`
- Verify: `src/psi_agent/ai/custom_tasks/agent_decomp_cot.yaml`
- Verify: `src/psi_agent/ai/custom_tasks/agent_prompt.yaml`

- [ ] Add a test using `importlib.resources.files("psi_agent.ai") / "custom_tasks"` that asserts all three filenames are files and `yaml.safe_load(...)["template"]` is a non-empty string.
- [ ] Run the focused test. It may pass immediately because the resources already exist; this is a characterization test for packaging inputs, not the production fix.

### Task 2: Prove prompt globals are configured before construction

**Files:**
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`

- [ ] Add a failing test that monkeypatches the Router constructor and calls `_build_router_sync("runtime.yaml")`; inside the fake constructor assert `llmrouter.prompts._CUSTOM_TASKS_DIR` ends in `psi_agent/ai/custom_tasks`, `_PROJECT_ROOT` is its parent, and `load_prompt_template("agent_decomp_route")` returns non-empty text.
- [ ] Run only this test and verify RED with the current `FileNotFoundError` or incorrect global path.
- [ ] Add `_REQUIRED_PROMPTS = ("agent_decomp_route.yaml", "agent_decomp_cot.yaml", "agent_prompt.yaml")` and a synchronous resource resolver. Convert the filesystem-backed package resource to `pathlib.Path` only inside this synchronous bridge because LLMRouter's private global requires synchronous `Path.exists`, `/`, and `os.walk` behavior.
- [ ] In `_build_router_sync`, acquire `_LLMROUTER_ENV_LOCK`, validate every required file, set `llmrouter.prompts._PROJECT_ROOT` and `_CUSTOM_TASKS_DIR`, then construct `LLMMultiRoundRouter`.
- [ ] Run the focused test and all adapter tests; expect GREEN.

### Task 3: Avoid lock recursion during route execution

**Files:**
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`

- [ ] Confirm `_build_router_sync` and `_route_sync` both use the same non-reentrant lock only in separate worker calls; add a test that starts the adapter with a fake constructor and routes once under `anyio.fail_after(2)`.
- [ ] Run the test and verify construction plus routing completes without deadlock and restores `API_KEYS`.

### Task 4: Verify wheel package data

**Files:**
- Modify only if required: `pyproject.toml`

- [ ] Run `uv build` using the workspace cache.
- [ ] Inspect the wheel with Python `zipfile` and assert these entries exist: `psi_agent/ai/custom_tasks/agent_decomp_route.yaml`, `agent_decomp_cot.yaml`, and `agent_prompt.yaml`.
- [ ] If any are absent, add an explicit Hatch wheel artifact/include rule for `src/psi_agent/ai/custom_tasks/*.yaml`, rebuild, and repeat the inspection.

### Task 5: Documentation and verification

**Files:**
- Modify: `src/psi_agent/ai/AGENTS.md`

- [ ] Document the packaged templates, the private `_CUSTOM_TASKS_DIR` integration, the process-global lock, and the requirement to revalidate when upgrading `llmrouter-lib`.
- [ ] Run `ruff format`, `ruff check`, and `ty check` for the AI source/tests.
- [ ] Run focused adapter/Router tests and the complete `tests/psi_agent/ai` suite with a workspace basetemp and cache provider disabled.
- [ ] Run a real prompt-loader probe that points LLMRouter at the packaged directory and loads all three template names.
- [ ] Run `git diff --check`, `git status --short`, and `git diff --stat`; report unrelated pre-existing dirty files separately.
