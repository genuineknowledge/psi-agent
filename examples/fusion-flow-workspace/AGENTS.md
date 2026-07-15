# Fusion Flow Workspace Instructions

- Use this workspace when the user wants Fusion Flow from natural language.
- Read `skills/fusion-flow/SKILL.md` before authoring or running `.flow.ts` files.
- Keep the Fusion Flow skill immutable under `skills/fusion-flow/`.
- Generated task files should go under `flows/<task-slug>/`.
- Put the generated `.flow.ts` and its runtime artifacts in the same task directory:
  - `flows/<task-slug>/<task-slug>.flow.ts`
  - `flows/<task-slug>/runs/<run-id>/`
- Prefer `FLOW_ENGINE=psi` with `FLOW_PSI_WORKSPACE` pointing at a separate executor workspace.
- With `FLOW_ENGINE=psi` you MUST route through the session shim. The bundle emits an
  old-style `psi-agent run --workspace --message ...`, but the current CLI's `run` is a
  YAML batch launcher (one positional config path) and rejects those flags with
  `exit=2 Missing value for argument 'config'`. Wire `FLOW_PSI_COMMAND` to
  `bin/session_shim.py` (see `bin/env.stateful.template` and `bin/README.stateful.md`)
  so it translates that call into the new three-layer architecture
  (`ai --provider` + `session` + `channel cli`). Without this wiring the psi engine
  will not run. On non-repo installs also set `PSI_CMD=psi-agent`.
- Do not store API keys or provider credentials in this workspace.
