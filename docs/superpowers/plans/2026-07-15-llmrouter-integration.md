# LLMRouter Integration Plan

Date: 2026-07-15

## Purpose

This document is the consolidated implementation-plan entry for the July 2026
LLMRouter work in psi-agent. It mirrors
`docs/superpowers/specs/2026-07-15-llmrouter-integration-design.md` and
replaces the previously split plan notes for JSON upstream input, packaged
custom tasks, multiline upstream arguments, runtime model data, and Python 3.14
CI environment support.

## Scope

The combined implementation covers five linked changes:

- direct integration of `llmrouter-lib==0.3.1` into psi-agent;
- Router CLI support for explicit router-model configuration and multiline
  upstream candidate input;
- packaged `custom_tasks` prompt-template loading for LLMRouter;
- runtime `llm_data` generation plus in-memory router endpoint injection;
- GitHub Actions environment hardening for Python 3.14 and LiteLLM/PyO3 builds.

## Plan Summary

### 1. Replace the old routing path with LLMRouter

- add the LLMRouter adapter module and move routing-specific logic out of the
  oversized router entry point;
- remove the previous semantic-router-oriented behavior and keep only the
  transport/proxy responsibilities that still apply;
- pin `llmrouter-lib==0.3.1` as the supported integration target.

### 2. Define the active Router CLI contract

- keep the command shape as `psi-agent ai router`;
- accept one remote router model through `--router-model`,
  `--router-base-url`, and `--router-api-key`;
- accept candidate answer models through one `--upstream` option followed by
  multiple JSON object strings, one per candidate;
- preserve explicit default-model fallback behavior;
- reject the earlier outer JSON array contract and other ambiguous input forms.

### 3. Parse and validate candidate upstreams strictly

- parse each upstream value independently with `json.loads`;
- require exactly `addr`, `model`, and `description` fields;
- reject blank values, duplicate models, unknown keys, and embedded candidate
  credentials;
- preserve upstream order so `upstream[0]` remains the implicit fallback when
  `default_model` is not set;
- surface validation errors with the failing upstream index.

### 4. Build LLMRouter runtime state once at startup

- create a managed runtime directory;
- generate candidate-only `llm_data.json` from CLI upstream values;
- generate runtime YAML with absolute `llm_data` path plus router model and
  endpoint;
- construct `LLMMultiRoundRouter` once in a worker thread and reuse it for all
  requests;
- keep cleanup shielded so startup failure and shutdown both release runtime
  resources safely.

### 5. Package and wire custom prompt templates

- ship `agent_decomp_route.yaml`, `agent_decomp_cot.yaml`, and
  `agent_prompt.yaml` as package data under `src/psi_agent/ai/custom_tasks`;
- resolve those resources from the installed psi-agent package rather than from
  the current working directory;
- set LLMRouter prompt globals before Router construction so template lookup
  succeeds in editable installs, wheels, and CI environments;
- fail fast at startup if any required template is missing.

### 6. Work around the LLMRouter base-model endpoint lookup bug

- construct LLMRouter from candidate-only disk metadata first;
- validate the resulting instance shape;
- inject an in-memory `router.llm_data[router_model]` entry containing only the
  router endpoint needed by `_decompose_and_route`;
- preserve the candidate description if the router model name is also one of
  the selectable upstream models.

### 7. Keep routing behavior deterministic and psi-agent-owned

- serialize bounded conversation context including recent history;
- omit tool-result bodies, tool-call arguments, credentials, and binary
  payloads from routing input;
- invoke `_decompose_and_route()` in a worker thread under a process-wide lock;
- validate returned routes against the configured candidate whitelist;
- choose one winner by majority vote, with first-valid-route tie-breaking;
- bypass LLMRouter when the incoming request explicitly names a configured
  candidate model;
- on recoverable routing failure, choose `default_model` or `upstream[0]`.

### 8. Preserve existing upstream request and SSE flow

- forward the original Chat Completions request to only the selected upstream;
- replace only the forwarded `model` field and remove internal routing
  metadata;
- preserve all other passthrough request fields, SSE content, reasoning,
  tool-call deltas, and done markers;
- keep existing non-stream and stream-time error behavior unchanged.

### 9. Harden CI for Python 3.14

- apply `PYO3_USE_ABI3_FORWARD_COMPATIBILITY: "1"` at workflow level;
- pin `astral-sh/setup-uv@v7` usage to `version: "0.11.23"` and
  `python-version: "3.14"`;
- enable uv dependency caching against `uv.lock` and `pyproject.toml`;
- change project sync commands to `uv sync --frozen`;
- install stable Rust before sync in workflows that build the project
  environment;
- add a smoke step that verifies LiteLLM, `LLMMultiRoundRouter`, and packaged
  prompt resources.

## Deliverables

Implementation is expected to touch:

- Router runtime code in `src/psi_agent/ai/`;
- CLI wiring in `src/psi_agent/cli.py`;
- dependency metadata in `pyproject.toml` and `uv.lock`;
- AI-layer tests and CI contract tests;
- GitHub workflow files that create project environments;
- supporting design/agent documentation as needed.

## Verification

The combined work should be verified with:

```powershell
uv lock
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -v
uv run psi-agent ai router --help
uv build
git diff --check
```

## Notes

- This plan intentionally describes one integrated feature line rather than
  preserving the earlier document split by subproblem.
- Future work that changes LLMRouter from a routing-only component into a full
  answer execution backend should be planned separately.
