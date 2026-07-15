# LLMRouter Packaged Custom Tasks Design

## Goal

Make psi-agent's LLMRouter adapter load the prompt templates packaged in
`src/psi_agent/ai/custom_tasks` so `LLMMultiRoundRouter` can resolve
`agent_decomp_route`, `agent_decomp_cot`, and `agent_prompt` after installation.

## Root Cause

`llmrouter.prompts` derives `_CUSTOM_TASKS_DIR` from the installed LLMRouter
package location. In a dependency installation this points outside psi-agent,
so the loader searches only `site-packages/llmrouter/prompts` and raises
`FileNotFoundError` for `agent_decomp_route.yaml`. The reference prototype
works because it overrides the prompt module's `_PROJECT_ROOT` and
`_CUSTOM_TASKS_DIR` globals before constructing `LLMMultiRoundRouter`.

## Resource Ownership

psi-agent owns these required templates:

- `src/psi_agent/ai/custom_tasks/agent_decomp_route.yaml`;
- `src/psi_agent/ai/custom_tasks/agent_decomp_cot.yaml`;
- `src/psi_agent/ai/custom_tasks/agent_prompt.yaml`.

The adapter locates the directory through package resources rather than the
current working directory. The templates must be present in both editable
installs and built wheels. Startup validates all three filenames and reports
the missing resource and resolved directory before constructing LLMRouter.

## Adapter Integration

`LLMRouterAdapter._build_router_sync` imports `llmrouter.prompts`, resolves
psi-agent's packaged `custom_tasks` directory, assigns the directory to
`llmrouter.prompts._CUSTOM_TASKS_DIR`, assigns its parent AI package directory
to `_PROJECT_ROOT`, and then constructs `LLMMultiRoundRouter` with the runtime
YAML.

LLMRouter exposes no public custom-prompt-directory parameter, so these private
module globals are the narrowest integration point and match the reference
prototype. The adapter does not copy files into `.venv`, edit third-party
package files, or fork the prompt loader.

The assignments and Router construction run under the existing process-wide
LLMRouter lock. This prevents another build or routing call from observing
partially configured third-party global state. The configured path remains
pointed at psi-agent's immutable package resources for the process lifetime;
it is not restored after construction because LLMRouter resolves templates
during later routing calls too.

## Packaging

Hatchling must include `src/psi_agent/ai/custom_tasks/*.yaml` as package data.
If its default package inclusion does not retain the files, the wheel target
will add an explicit artifacts/include rule scoped to those YAML files. No
runtime-generated prompt copies are used.

## Error Handling

Missing packaged templates are a startup configuration error, not a recoverable
per-request routing failure. Adapter startup raises `FileNotFoundError` naming
the missing template and resolved custom-task directory. `AiRouter.run()` keeps
its existing shielded adapter cleanup behavior.

## Testing

Tests will verify:

- all three package resources exist and contain a non-empty `template` value;
- `_build_router_sync` configures LLMRouter's prompt globals before invoking
  the Router constructor;
- a missing required resource fails before Router construction with an
  actionable error;
- the built wheel contains all three YAML paths;
- existing adapter, Router, CLI, type, and AI-layer tests remain green.

## Documentation

The AI-layer design document records that the adapter relies on LLMRouter
0.3.1's private prompt globals, that packaged `custom_tasks` resources are
required, and that changing the pinned library version requires revalidating
this integration point.
