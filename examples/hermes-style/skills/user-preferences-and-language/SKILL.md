---
name: user-preferences-and-language
description: Capture stable user preferences for language, timezone, tools, and communication style in future sessions.
category: agent
created_by: agent
created_at: 2026-06-09T09:47:28Z
updated_at: 2026-06-09T09:49:30Z
---

---
name: user-preferences-and-language
description: Capture stable user preferences for language, timezone, tools, and communication style in future sessions.
category: agent
created_by: agent
created_at: 2026-06-09T00:00:00Z
---

# User Preferences and Language

When a user states stable working preferences, treat them as durable session-to-session guidance and follow them unless they later override them.

## Preference capture rules

Persist preferences when the user clearly states a standing rule such as:
- preferred language by input language
- timezone
- preferred tools/frameworks
- explicit exclusions (for example, "I never use X")
- response style requests

Do not ignore these as one-off remarks if they are phrased as general habits or defaults.

## Preferences learned for this user

- Timezone: UTC+8 / China timezone
- Language preference: respond in Chinese when the user writes in Chinese
- Python type checking preference: use `ty check`; do not suggest `mypy` as the default type checker for this user

## Application guidance

### Language
- Mirror the user's language
- If the user writes in Chinese, respond in Chinese
- If the user writes in English, English is fine unless they request otherwise

### Time references
- When discussing schedules, dates, cron interpretation, reminders, or "today/tomorrow", reason in UTC+8 unless the user specifies another timezone for the task

### Python tooling
- When proposing type-checking steps, commands, CI examples, or developer workflow, prefer `ty check`
- Avoid suggesting `mypy` unless the user explicitly asks to compare tools or use it for a specific external constraint
- When mentioning common Python quality gates for this user, default to `ruff` + `ty check`, not `ruff` + `mypy`

## Communication behavior

If the user gives a workflow/tooling preference, incorporate it into future recommendations automatically rather than restating generic defaults.

## Anti-pattern to avoid

Do not claim you will run or update tools/files across a codebase unless you have first inspected the repository and actually have the ability to do so in the current environment. If the user asks for a refactor, first state that you need to review the existing code and ask them to share the module or repository context before promising concrete file counts or tool runs.