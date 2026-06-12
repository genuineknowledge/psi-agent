# Fusion Flow Workspace

This workspace lets psi-agent discover Fusion Flow as a skill from natural-language requests.

Expected flow:

```text
user natural language
-> psi-agent loads this workspace
-> system prompt points at skills/fusion-flow/SKILL.md
-> agent authors a .flow.ts under flows/<task-slug>/
-> Fusion Flow runtime executes the flow and writes runs under flows/<task-slug>/runs/
-> FLOW_ENGINE=psi calls psi-agent run for flow.session / evaluate / choice
```

Run example:

```bash
uv run psi-agent run \
  --workspace examples/fusion-flow-workspace \
  --profile fusion \
  --message "Build a parallel code-review workflow with security, performance, and readability reviewers."
```

The bundled Fusion Flow skill lives at:

```text
skills/fusion-flow/
```

Generated user task artifacts live outside the skill:

```text
flows/<task-slug>/<task-slug>.flow.ts
flows/<task-slug>/runs/<run-id>/
```

Do not put provider keys in this workspace. Keep model credentials in psi-agent profiles
such as `~/.psi-agent/config.toml`, or pass them through environment variables.
