---
name: source-liquid-sop-designer
description: Deprecated compatibility entry for historical source-liquid SOP requests in OWS Stage08. Use only to redirect Codex to round-parallel-synthesis-advisor, where source-liquid inventory, immediate preparation-method registry updates, and final SOURCE_LIQUID_BOTTLE_PREPARATION.md generation now live.
---
# Source Liquid SOP Designer

This skill has been migrated into Stage08.

For OWS round-by-round synthesis work, use:

```text
skills/round-parallel-synthesis-advisor/SKILL.md
skills/round-parallel-synthesis-advisor/references/source_liquid_preparation_methods.md
```

The `round-parallel-synthesis-advisor` skill now owns source-liquid inventory,
immediate per-source-liquid updates to `SOURCE_LIQUID_PREPARATION_METHODS.json`,
and the final `SOURCE_LIQUID_BOTTLE_PREPARATION.md` gate. Keep this deprecated
entry only so historical prompts that name `source-liquid-sop-designer`
redirect to the Stage08 skill instead of silently using stale instructions.
