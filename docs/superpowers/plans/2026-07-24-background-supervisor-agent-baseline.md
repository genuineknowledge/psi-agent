# Background Supervisor Agent Baseline

This baseline was observed before implementation of the background supervisor agent.

## Commands tried

```powershell
uv run pytest -q tests/psi_agent/session tests/integration/test_session_workspace.py
```

The run completed with 62 passing tests and these two pre-existing failures:

- `tests/integration/test_session_workspace.py::test_system_prompt_builder_raises_exception_caught`
- `tests/integration/test_session_workspace.py::test_session_with_empty_workspace_uses_cwd`

In both failures, nested `uv run psi-agent ai` / `uv run psi-agent session` processes exited with return code 1 before their Windows Unix socket files were created. Test cleanup then raised `ProcessLookupError` while attempting to terminate an already-exited process.

## Impact

- These two integration tests cannot be used as regression evidence in this worktree.
- The failure could mask CLI startup regressions on Windows.
- Unit tests and mock-based coverage remain usable for implementation verification.
- Final verification must recheck these tests in the parent checkout or a local terminal environment.

This failure predates the background supervisor implementation.
