"""claude_code tool - delegate coding tasks to the Claude Code CLI.

Part of the ``autonomous-ai-agents`` toolset. Delegates real coding work
(implement a feature, fix a bug, open a PR) to Anthropic's `Claude Code
<https://code.claude.com/docs/en/overview>`_ CLI running in headless
"print" mode (``claude -p``), so the Haitun agent can hand off a bounded,
self-contained engineering task and get back the result plus a resumable
session id.

``claude`` is an external Node/npm CLI (``npm i -g @anthropic-ai/claude-code``),
not a Python package, so this tool shells out to it with
:func:`anyio.run_process` rather than importing a library — no extra
dependency is added. On Windows ``shutil.which`` resolves the ``claude.CMD``
shim, which ``anyio.run_process`` runs directly.

Authentication is the CLI's own concern (its stored login or
``ANTHROPIC_API_KEY`` in the environment), exactly like ``gh`` for the
GitHub tool — this wrapper does not manage credentials.

Headless delegation is non-interactive: Claude Code can never stop to ask
for a permission, so ``permission_mode`` defaults to ``acceptEdits`` (it may
edit files without prompting). Grant more (``bypassPermissions``) only for
tasks that must run shell/git commands unattended, e.g. opening a PR.
"""

from __future__ import annotations

import json
import shlex
import shutil

import anyio

# Permission modes the Claude Code CLI accepts for ``--permission-mode``.
# ``bypassPermissions`` is the unattended mode needed for git/gh/PR flows.
_PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
    "manual",
}


def _resolve_cli() -> str | None:
    """Return the resolved ``claude`` executable path, or None if absent.

    Uses ``shutil.which`` so Windows resolves the ``claude.CMD`` shim to a full
    path — ``anyio.run_process`` doesn't go through a shell, so a bare "claude"
    would not launch there.
    """
    return shutil.which("claude")


_CLI_NOT_FOUND = (
    "[Error] `claude` CLI not found on PATH. Install it with: "
    "npm install -g @anthropic-ai/claude-code (Node.js required), "
    "then authenticate once by running `claude` interactively."
)


def _build_command(
    exe: str,
    prompt: str,
    *,
    model: str,
    permission_mode: str,
    allowed_tools: str,
    disallowed_tools: str,
    add_dirs: str,
    append_system_prompt: str,
    max_turns: int,
    resume_session_id: str,
    continue_recent: bool,
    output_format: str,
) -> list[str]:
    """Assemble the ``claude`` argv for a headless delegation run.

    ``exe`` is the resolved CLI path from ``shutil.which`` — on Windows that's
    the ``claude.CMD`` shim, which ``anyio.run_process`` (no shell) needs the
    full name of; a bare "claude" would fail to launch.
    """
    cmd = [exe, "-p", prompt, "--output-format", output_format]

    if model.strip():
        cmd += ["--model", model.strip()]
    if permission_mode.strip():
        cmd += ["--permission-mode", permission_mode.strip()]
    # shlex so callers can pass quoted rules/paths that contain spaces,
    # e.g. allowed_tools='"Bash(git log *)" Read Edit'.
    if allowed_tools.strip():
        cmd += ["--allowed-tools", *shlex.split(allowed_tools)]
    if disallowed_tools.strip():
        cmd += ["--disallowed-tools", *shlex.split(disallowed_tools)]
    for extra_dir in shlex.split(add_dirs):
        cmd += ["--add-dir", extra_dir]
    if append_system_prompt.strip():
        cmd += ["--append-system-prompt", append_system_prompt]
    if max_turns > 0:
        cmd += ["--max-turns", str(max_turns)]
    if resume_session_id.strip():
        cmd += ["--resume", resume_session_id.strip()]
    if continue_recent:
        cmd.append("--continue")

    return cmd


def _format_json_result(raw: str) -> str:
    """Turn a ``--output-format json`` payload into a compact summary."""
    try:
        data = json.loads(raw)
    except ValueError:  # JSONDecodeError is a subclass of ValueError
        return raw.strip() or "(no output)"
    if not isinstance(data, dict):
        return raw.strip() or "(no output)"

    result = str(data.get("result", "")).strip()
    footer_parts: list[str] = []
    if data.get("session_id"):
        footer_parts.append(f"session_id={data['session_id']}")
    if data.get("num_turns") is not None:
        footer_parts.append(f"turns={data['num_turns']}")
    if data.get("total_cost_usd") is not None:
        footer_parts.append(f"cost_usd={data['total_cost_usd']}")
    if data.get("is_error"):
        footer_parts.append("is_error=true")

    footer = f"\n\n[{', '.join(footer_parts)}]" if footer_parts else ""
    body = result or "(Claude Code returned no result text)"
    return body + footer


async def claude_code(
    prompt: str,
    directory: str = ".",
    model: str = "",
    permission_mode: str = "acceptEdits",
    allowed_tools: str = "",
    disallowed_tools: str = "",
    add_dirs: str = "",
    append_system_prompt: str = "",
    max_turns: int = 0,
    resume_session_id: str = "",
    continue_recent: bool = False,
    output_format: str = "json",
    timeout_seconds: int = 1800,
) -> str:
    """Delegate a coding task to the Claude Code CLI (implement features, open PRs).

    Runs ``claude -p`` headlessly in *directory* so a self-contained engineering
    task (add a feature, fix a bug, refactor, write tests, open a pull request)
    can be handed off end-to-end. Give a clear, bounded spec in *prompt* — what
    to build, in which files, and how to verify it. Because the run is
    non-interactive, Claude Code cannot pause to ask for permission: keep the
    default ``acceptEdits`` for edit-only work, and raise to ``bypassPermissions``
    only when it must run shell/git/gh commands unattended (e.g. to push a branch
    and open a PR). Reviewing the diffs it produces afterwards is recommended.

    With the default ``output_format="json"`` the return value ends with a
    ``[session_id=..., turns=..., cost_usd=...]`` footer; pass that id back as
    *resume_session_id* to iterate on the same delegated task.

    Args:
        prompt: The coding task specification. Be concrete: goal, target files,
            constraints, and how to verify (tests/build). This is the only required arg.
        directory: Working directory the CLI runs in (usually the repo root). Defaults to ".".
        model: Optional model to use — an alias ("opus", "sonnet", "haiku", "fable")
            or a full model name. Empty uses the CLI's configured default.
        permission_mode: How the delegated session handles permissions. One of
            "default", "acceptEdits" (default; edit files without prompting), "plan",
            "auto", "dontAsk", "bypassPermissions" (run everything unattended — needed
            for git/gh/PR flows), or "manual".
        allowed_tools: Space-separated permission rules that run without prompting,
            quoted if they contain spaces, e.g. '"Bash(git log *)" Read Edit'.
        disallowed_tools: Space-separated deny rules, same syntax as allowed_tools.
        add_dirs: Space-separated extra directories the CLI may read/edit, quoted if
            they contain spaces. Each must exist.
        append_system_prompt: Extra text appended to Claude Code's system prompt.
        max_turns: Cap on agentic turns (0 = no limit). Delegation exits with an
            error if the cap is hit before finishing.
        resume_session_id: Resume a previous delegation by its session id (from an
            earlier json footer) to continue iterating in the same context.
        continue_recent: Continue the most recent Claude Code conversation in
            *directory* instead of starting fresh.
        output_format: "json" (default; structured, adds the session/cost footer),
            "text" (raw assistant text), or "stream-json".
        timeout_seconds: Max seconds to wait; coding tasks are long, so this
            defaults to 1800 (30 min).

    Returns:
        Claude Code's result text (plus a metadata footer for json output), or an
        error message prefixed with "[Error]".
    """
    exe = _resolve_cli()
    if exe is None:
        return _CLI_NOT_FOUND

    fmt = output_format.strip() or "json"
    if fmt not in ("json", "text", "stream-json"):
        return "[Error] output_format must be one of: json, text, stream-json."

    mode = permission_mode.strip()
    if mode and mode not in _PERMISSION_MODES:
        return f"[Error] permission_mode must be one of: {', '.join(sorted(_PERMISSION_MODES))}."

    if not prompt.strip():
        return "[Error] prompt is required — describe the coding task to delegate."

    work_dir = anyio.Path(directory)
    if not await work_dir.is_dir():
        return f"[Error] directory does not exist: {directory}"

    try:
        cmd = _build_command(
            exe,
            prompt,
            model=model,
            permission_mode=mode,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            add_dirs=add_dirs,
            append_system_prompt=append_system_prompt,
            max_turns=max_turns,
            resume_session_id=resume_session_id,
            continue_recent=continue_recent,
            output_format=fmt,
        )
    except ValueError as e:  # unbalanced quotes in a shlex-parsed field
        return f"[Error] Could not parse tool/dir arguments: {e}"

    try:
        with anyio.fail_after(timeout_seconds):
            result = await anyio.run_process(cmd, cwd=str(directory), check=False)
    except TimeoutError:
        return f"[Error] Claude Code delegation timed out after {timeout_seconds}s."
    except FileNotFoundError:
        return "[Error] `claude` CLI could not be launched. Ensure Node.js and Claude Code are installed."

    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")

    if result.returncode != 0:
        detail = (err or out).strip() or "(no output)"
        return f"[Error] Claude Code exited with code {result.returncode}:\n{detail}"

    if fmt == "json":
        return _format_json_result(out)
    return out.strip() or "(no output)"
