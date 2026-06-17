---
name: writing-plans
description: Produce structured technical plan documents with context, goals, steps, and risks.
---

Use this skill when asked to write a plan, design doc, or technical proposal.

**Document structure:**

```markdown
## Context
One paragraph: current state, why this matters, key constraints.

## Goals
- What this plan achieves (be specific and measurable)

## Non-Goals
- What is explicitly out of scope

## Steps
1. Step with enough detail to act on
2. ...

## Risks
- [Risk] → Mitigation

## Open Questions
- Questions that must be answered before or during execution
```

**Writing guidelines:**
- Goals must be testable: "API returns < 200 ms" not "API is fast".
- Steps must be actionable: start with a verb, include the tool or method.
- Risks must have mitigations, not just descriptions.
- Keep it to one page for straightforward work; longer only when genuinely complex.
- Save the document with `write` tool or `update_plan` tool.
