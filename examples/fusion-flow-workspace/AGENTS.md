# Fusion Flow Workspace Instructions

- Use this workspace when the user wants Fusion Flow from natural language.
- Read `skills/fusion-flow/SKILL.md` before authoring or running `.flow.ts` files.
- Keep the Fusion Flow skill immutable under `skills/fusion-flow/`.
- Generated task files should go under `flows/<task-slug>/`.
- Put the generated `.flow.ts` and its runtime artifacts in the same task directory:
  - `flows/<task-slug>/<task-slug>.flow.ts`
  - `flows/<task-slug>/runs/<run-id>/`
- Prefer `FLOW_ENGINE=psi` with `FLOW_PSI_WORKSPACE` pointing at a separate executor workspace.
- Do not store API keys or provider credentials in this workspace.
