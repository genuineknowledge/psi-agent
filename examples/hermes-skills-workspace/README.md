# hermes-skills workspace

A lightweight psi-agent workspace that exposes a curated set of domain skills
on top of basic file/shell tools.

- `skills/<name>/SKILL.md` — domain capability guides (cryptanalysis,
  image-segmentation, ml-inference, …) plus `_universal` working discipline.
- `tools/` — basic tools: bash, read, write, edit.
- `systems/system.py` — builds the system prompt (`system_prompt_builder`),
  scanning `skills/` for the available-skills index.

No flow, memory, curator, or scheduling components — skills only.

## Run

```bash
psi-agent ai --provider <name> --model <model> --api-key <key> --base-url <url> --session-socket /tmp/ai.sock
psi-agent session --workspace examples/hermes-skills-workspace --ai-socket /tmp/ai.sock --channel-socket /tmp/ch.sock
psi-agent channel repl --session-socket /tmp/ch.sock
```
