---
name: example
description: Demonstrates how to define a skill. Replace this with your own skill instructions.
category: example
---

# Example Skill

This is a placeholder skill that demonstrates the skill format used by openclaw-style-workspace.

## When to use

Use this skill when the user asks for a demonstration of how skills work in this workspace.

## Instructions

1. Explain that skills are stored in `skills/<name>/SKILL.md`
2. Show the YAML frontmatter format: `name`, `description`, `category`
3. The `description` field is indexed into the system prompt's `<available_skills>` block
4. The full SKILL.md content can be read by the agent using the `read` tool when needed

## Example

If the user asks "what skills do you have?", the agent will list skills from the
`<available_skills>` block in the system prompt. To see this skill's full content,
the agent reads `skills/example/SKILL.md`.
