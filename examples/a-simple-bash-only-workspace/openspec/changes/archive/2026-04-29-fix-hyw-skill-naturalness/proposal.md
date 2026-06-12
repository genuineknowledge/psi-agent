## Why

The current `hyw` skill implementation causes the AI to behave unnaturally when asking questions. When the skill is loaded, the AI explicitly announces it has "discovered" the skill and explains the "何意味？" prefix rule before using it, making the interaction feel forced and performative rather than natural.

The root cause is that the skill's description ("Use this skill when asking the user questions") frames it as an action to be "used" rather than a behavioral guideline to be internalized. This causes the AI to treat it as a tool invocation rather than a personality trait.

## What Changes

- Modify the `hyw` skill's SKILL.md to reframe it as a behavioral style guideline rather than a "skill to use"
- Update the skill description in YAML frontmatter to be more subtle and internal-facing
- Restructure the instructions to emphasize natural integration over explicit application
- Add guidance to avoid announcing or explaining the prefix behavior

## Capabilities

### New Capabilities

- `hyw-skill-behavior`: Defines how the "何意味？" prefix should be naturally integrated into AI responses without explicit announcement or explanation

### Modified Capabilities

(None - this is a new capability for the example workspace)

## Impact

- `examples/a-simple-bash-only-workspace/skills/hyw/SKILL.md` - skill definition file
- User experience when interacting with agents using this workspace
