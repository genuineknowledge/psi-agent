## Context

The `hyw` skill in the example workspace is designed to add a contemplative "何意味？" (He Yi Wei - "What does it mean?") prefix to questions the AI asks users. However, the current implementation causes the AI to behave unnaturally by:

1. Announcing it has "discovered" or "found" the skill
2. Explaining the prefix rule to the user before using it
3. Treating the prefix as a deliberate action rather than an integrated behavior

This happens because the skill is framed as something to "use" rather than a behavioral guideline to internalize.

## Goals / Non-Goals

**Goals:**
- Make the "何意味？" prefix feel natural and integrated into the AI's personality
- Prevent the AI from announcing or explaining the skill behavior
- Maintain the contemplative, philosophical tone intended by the skill

**Non-Goals:**
- Changing how the system prompt builder works
- Modifying other skills or the workspace structure
- Removing the skill entirely

## Decisions

### Decision 1: Reframe skill as behavioral style

**Choice:** Change the skill description from action-oriented to style-oriented.

**Rationale:** The current description "Use this skill when asking the user questions" triggers the AI to think of it as a tool to invoke. A better framing like "A contemplative questioning style that adds philosophical depth" treats it as a personality trait.

**Alternatives considered:**
- Move the behavior into system.py's system prompt directly → Would lose the skill's modularity
- Remove the skill entirely → Would lose the intended contemplative tone

### Decision 2: Add explicit anti-announcement instruction

**Choice:** Add an explicit instruction to never announce or explain the prefix behavior.

**Rationale:** Even with better framing, some models may still feel compelled to explain their behavior. An explicit prohibition makes the expectation clear.

### Decision 3: Simplify the skill content

**Choice:** Reduce the skill content to essential guidance, removing verbose explanations.

**Rationale:** Shorter, more direct instructions are easier for the AI to internalize without overthinking.

## Risks / Trade-offs

- **Risk:** Some models may still occasionally announce the behavior → **Mitigation:** The explicit instruction reduces but cannot eliminate this; it's model-dependent behavior
- **Risk:** The prefix may be applied inconsistently → **Mitigation:** Acceptable trade-off for naturalness; perfect consistency would require more rigid framing that causes the original problem
