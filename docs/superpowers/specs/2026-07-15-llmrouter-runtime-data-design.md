# LLMRouter Runtime Model Data Design

## Goal

Generate LLMRouter runtime data from the Router CLI while ensuring the remote
routing model can call its API without becoming an unintended upstream
candidate.

## Root Cause

`LLMMultiRoundRouter._decompose_and_route` looks up `base_model` in its loaded
`llm_data` whenever that dictionary is non-empty. In LLMRouter 0.3.1, a
non-empty dictionary that does not contain the base model does not fall back to
the YAML-level `api_endpoint`, so the method raises before calling the routing
API. psi-agent currently writes only upstream candidates to `llm_data.json`,
which triggers this defect.

## Generated Runtime Files

`LLMRouterAdapter.start` continues to generate `llm_data.json` from the ordered
`--upstream` values. Each candidate contributes:

```json
"candidate-model": {
  "feature": "candidate description",
  "model": "candidate-model"
}
```

The file contains candidates only. Candidate order is retained, and neither
the routing model's API endpoint nor any API key is written to it.

The generated runtime YAML contains:

```yaml
data_path:
  llm_data: <generated path>
base_model: <router-model>
use_local_llm: false
api_endpoint: <router-base-url>
```

`base_model` and `api_endpoint` come from `--router-model` and
`--router-base-url`. The routing API key remains in the lock-protected
`API_KEYS` environment variable only.

## Two-Phase Construction

The synchronous build bridge receives `runtime_yaml`, `router_model`, and
`router_base_url`. It first constructs `LLMMultiRoundRouter` from the candidate-
only file. LLMRouter therefore builds `model_descriptions`, `model_list`, and
`DECOMP_ROUTE_PROMPT` without an independent routing model.

After construction, the bridge validates that the instance's `base_model`
matches the CLI value and that `llm_data` is a mutable dictionary. It then
injects an in-memory endpoint record:

```python
router.llm_data[router_model] = {
    "model": router_model,
    "api_endpoint": router_base_url,
}
```

This satisfies LLMRouter 0.3.1's endpoint lookup during
`_decompose_and_route` without rebuilding the already-created candidate prompt.
The generated disk file remains an accurate candidate-only artifact.

## Same-Name Routing and Candidate Model

If `router_model` already exists as an upstream candidate, that candidate is
intentionally selectable. The bridge preserves its `feature` and `model`
fields and adds or replaces only `api_endpoint` in the in-memory entry. It does
not create a duplicate record.

## Concurrency and Secrets

Prompt-global configuration, Router construction, instance validation, and
base-model endpoint injection all run under the existing process-wide
LLMRouter lock. Route calls use the same lock and temporarily set `API_KEYS`,
restoring the prior value in `finally`. Runtime YAML and JSON never contain the
key.

## Failure and Fallback

An incompatible LLMRouter instance shape—mismatched `base_model`, missing or
non-dictionary `llm_data`, or an invalid endpoint—is a startup failure. A
remote API error during a request remains recoverable and uses psi-agent's
existing default/first-upstream fallback. Successful decisions keep source
`llmrouter_majority`; fallback decisions keep their existing sources.

## Testing

Tests will verify:

- candidate-only disk JSON is generated from the CLI upstream values;
- YAML base model and endpoint come from Router CLI fields;
- the fake Router sees candidate-only data during construction;
- the independent routing model is injected only after prompt construction;
- a same-name candidate retains its description and gains the endpoint;
- incompatible Router instance state fails during startup;
- secrets are absent from both generated files;
- an endpoint-aware fake `_decompose_and_route` succeeds and returns a majority
  decision instead of exercising fallback;
- adapter, Router, CLI, AI-layer, lint, and type checks remain green.

## Private API Note

This is a compatibility shim for `llmrouter-lib==0.3.1` internals. Upgrading the
pin requires revalidating constructor timing, `llm_data`, `base_model`, prompt
construction, and endpoint lookup before removing or changing the shim.
