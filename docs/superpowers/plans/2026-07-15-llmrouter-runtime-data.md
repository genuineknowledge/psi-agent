# LLMRouter Runtime Model Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CLI-configured remote routing model callable while keeping independent routing models out of LLMRouter's upstream candidate prompt.

**Architecture:** Keep the generated disk `llm_data.json` candidate-only and the runtime YAML CLI-derived. Pass the router model and endpoint into the synchronous builder, construct LLMRouter first, then inject the base-model endpoint into the instance's mutable `llm_data` under the existing process lock.

**Tech Stack:** Python 3.14, anyio, JSON/YAML, llmrouter-lib 0.3.1 private instance state, pytest, ruff, ty.

---

### Task 1: Define two-phase builder behavior

**Files:**
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`

- [ ] Add a failing fake-constructor test that receives candidate-only `llm_data`, snapshots its keys during construction, and asserts `_build_router_sync("runtime.yaml", "router-small", "https://router.example/v1")` returns an instance whose post-construction `llm_data["router-small"]` contains the CLI endpoint while the construction snapshot does not contain `router-small`.
- [ ] Run only that test and verify RED because `_build_router_sync` currently accepts one argument and performs no injection.
- [ ] Change `_build_router_sync` to accept `runtime_yaml`, `router_model`, and `router_base_url`. Under `_LLMROUTER_ENV_LOCK`, configure prompt globals, construct the Router, validate `base_model` and `llm_data`, then add or update the in-memory base entry.
- [ ] Run the focused test and verify GREEN.

### Task 2: Preserve same-name candidates and startup file semantics

**Files:**
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`

- [ ] Add tests for a same-name candidate retaining `feature` while gaining `api_endpoint`, mismatched `base_model` raising at build time, and non-dictionary `llm_data` raising at build time.
- [ ] Update `start()` to pass `self.router_model` and `self.router_base_url` to `run_sync`.
- [ ] Update existing fake builder signatures to accept all three positional arguments.
- [ ] Assert generated disk JSON remains candidate-only, runtime YAML contains the CLI base model and endpoint, and neither file contains the API key.

### Task 3: Prove route-model invocation contract

**Files:**
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`

- [ ] Add an endpoint-aware fake Router whose `_decompose_and_route` asserts its base entry has the CLI endpoint and `API_KEYS` contains the router key, then returns valid candidate routes.
- [ ] Run Adapter and Router tests; expect a majority `RouteDecision` rather than an endpoint exception or fallback.

### Task 4: Documentation and verification

**Files:**
- Modify: `src/psi_agent/ai/AGENTS.md`

- [ ] Document candidate-only disk JSON, CLI-derived YAML, post-construction in-memory endpoint injection, same-name behavior, and the LLMRouter 0.3.1 fallback defect.
- [ ] Run focused Adapter/Router tests and the complete AI-layer suite.
- [ ] Run `ruff check`, targeted `ruff format --check`, and `ty check` for modified AI source/tests.
- [ ] Inspect a started fake adapter's runtime files and in-memory Router data to confirm secrets remain absent and endpoint injection is present.
- [ ] Run `git diff --check`, `git status --short`, and `git diff --stat`; separate unrelated dirty files in the final report.
