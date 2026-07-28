# Haitun Supervisor Workspace

This workspace defines a minimal, isolated sidecar supervisor for Haitun.

- `SOUL.md` is the complete supervisor prompt.
- `systems/system.py` exposes only the stable prompt builder and a rebuild checker that always returns `False`.
- The supervisor has no tools, user-facing persona, profile mutation, or before/after-turn hooks.
- Its input is an isolated JSON payload prepared by the supervisor runtime. It must never request or consume the main Agent's answer, reasoning, drafts, tool calls, or tool results.
- Its output is one strict `SupervisorAdvice` JSON object. It never answers the user's question itself.

Keep this workspace deliberately small. Policy changes belong in `SOUL.md` and require integration-test coverage.
