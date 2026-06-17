---
name: skill-curation
description: Guidelines for curating the skill library — keep, patch, merge, archive decisions and class-level skill standards.
category: general
created_by: agent
created_at: 2026-06-11T09:09:17Z
---

# Skill Curation Guidelines

## Target Shape
CLASS-LEVEL skills with rich SKILL.md content. Not a flat list of narrow one-session entries.

## Curation Actions

### keep
Skill is healthy, substantial, recently updated, and covers its topic well. No action needed.

### patch
Skill has thin/outdated content that should be expanded. Apply the enriched content immediately rather than just flagging.

### merge
Skill overlaps significantly with another more comprehensive skill. Merge the content into the target and archive the source.

### archive
Skill is stale (< 3 months without update), too shallow to be useful, superseded by another skill, or irrelevant to the workspace's project stack.

## Curation Signals
The following warrant a curation action:

- **User correction**: Style, tone, format, legibility, verbosity, workflow, or approach.
- **Technique emergence**: A non-trivial fix, workaround, debugging path, or tool-usage pattern appeared that a future session would benefit from.
- **Skill is wrong/outdated**: A skill consulted during a session turned out to be missing steps or had stale information.

## Execution
- Curate actively after any session where curation signals appeared.
- Apply patches immediately via `skill_manage`.
- Archive merged source skills after content transfer.
- Name new skills at the class level — not a PR number, error string, or session artifact.

## Preference Order
1. Patch a currently-loaded skill if it covers the new learning.
2. Patch an existing umbrella skill.
3. Create a new class-level umbrella skill when nothing exists.