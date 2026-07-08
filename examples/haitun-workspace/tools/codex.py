"""codex tool - delegate coding tasks to the OpenAI Codex CLI.

Part of the ``autonomous-ai-agents`` toolset. Delegates real coding work
(implement a feature, fix a bug, open a PR) to OpenAI's `Codex
<https://developers.openai.com/codex>`_ CLI running non-interactively via
``codex exec``, so the Haitun agent can hand off a bounded, self-contained
engineering task and get the final result back.

``codex`` is an external Rust/npm CLI (``npm i -g @openai/codex``), not a
Python package, so this tool shells out to it with
:func:`anyio.run_process` rather than importing a library — no extra
dependency is added. On Windows ``shutil.which`` resolves the ``codex.CMD``
shim, which ``anyio.run_process`` runs directly.

Authentication is the CLI's own concern (its stored ChatGPT/Codex login or
``OPENAI_API_KEY`` in the environment), exactly like ``gh`` for the GitHub
tool — this wrapper does not manage credentials.

``codex exec`` is non-interactive: it runs to completion without pausing for
approval. What it may touch on disk/network is governed by the *sandbox*, so
that defaults to ``workspace-write`` (edit files in the working dir). Raise to
``danger-full-access`` — or set ``dangerously_bypass=True`` — only for tasks
that must reach the network or run git/gh unattended, e.g. opening a PR.
"""

from __future__ import annotations

import os
import shlex
import shutil
import tempfile

import anyio

# Sandbox policies the Codex CLI accepts for ``--sandbox``. ``danger-full-access``
# is the unrestricted mode needed for network/git/gh/PR flows.
_SANDBOX_MODES = {
    "read-only",
    "workspace-write",
    "workspace-read-network-write",
    "danger-full-access",
}


def _resolve_cli() -> str | None:
    """Return the resolved ``codex`` executable path, or None if absent.

    Uses ``shutil.which`` so Windows resolves the ``codex.CMD`` shim to a full
    path — ``anyio.run_process`` doesn't go through a shell, so a bare "codex"
    would not launch there.
    """
    return shutil.which("codex")


_CLI_NOT_FOUND = (
    "[Error] `codex` CLI not found on PATH. Install it with: "
    "npm install -g @openai/codex (Node.js required), "
    "then authenticate once by running `codex` interactively."
)


def _build_command(
    exe: str,
    prompt: str,
    *,
    directory: str,
    model: str,
    sandbox: str,
    dangerously_bypass: bool,
    skip_git_repo_check: bool,
    add_dirs: str,
    images: str,
    output_schema: str,
    json_events: bool,
    last_message_path: str,
    resume_session_id: str,
    continue_recent: bool,
) -> list[str]:
    """Assemble the ``codex exec`` argv for a headless delegation run.

    ``exe`` is the resolved CLI path from ``shutil.which`` — on Windows that's
    the ``codex.CMD`` shim, which ``anyio.run_process`` (no shell) needs the
    full name of; a bare "codex" would fail to launch. The prompt is placed
    last as a positional argument, after every option.
    """
    cmd = [exe, "exec"]

    # Resuming is a subcommand, not a flag: `codex exec resume --last|<id>`.
    if continue_recent:
        cmd += ["resume", "--last"]
    elif resume_session_id.strip():
        cmd += ["resume", resume_session_id.strip()]

    if model.strip():
        cmd += ["--model", model.strip()]

    # --dangerously-bypass-approvals-and-sandbox removes the sandbox entirely,
    # so passing --sandbox alongside it is redundant/conflicting.
    if dangerously_bypass:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    elif sandbox.strip():
        cmd += ["--sandbox", sandbox.strip()]

    if skip_git_repo_check:
        cmd.append("--skip-git-repo-check")

    cmd += ["--cwd", directory]

    # shlex so callers can pass quoted paths that contain spaces.
    for extra_dir in shlex.split(add_dirs):
        cmd += ["--add-dir", extra_dir]
    if images.strip():
        cmd += ["--images", images.strip()]
    if output_schema.strip():
        cmd += ["--output-schema", output_schema.strip()]
    if json_events:
        cmd.append("--json")

    # Capture the final agent message to a file — the cleanest way to get the
    # result without parsing the whole JSONL event stream.
    cmd += ["--output-last-message", last_message_path]

    # Prompt is positional and must come after all options.
    cmd.append(prompt)
    return cmd


async def codex(
    prompt: str,
    directory: str = ".",
    model: str = "",
    sandbox: str = "workspace-write",
    dangerously_bypass: bool = False,
    skip_git_repo_check: bool = False,
    add_dirs: str = "",
    images: str = "",
    output_schema: str = "",
    json_events: bool = False,
    resume_session_id: str = "",
    continue_recent: bool = False,
    timeout_seconds: int = 1800,
) -> str:
    """Delegate a coding task to the OpenAI Codex CLI (implement features, open PRs).

    Runs ``codex exec`` non-interactively in *directory* so a self-contained
    engineering task (add a feature, fix a bug, refactor, write tests, open a
    pull request) can be handed off end-to-end. Give a clear, bounded spec in
    *prompt* — what to build, in which files, and how to verify it. Because the
    run is non-interactive, Codex never pauses for approval; what it may touch is
    governed by *sandbox*, so keep the default ``workspace-write`` for edit-only
    work and raise it (or set ``dangerously_bypass=True``) only when it must
    reach the network or run git/gh unattended (e.g. to push a branch and open a
    PR). Reviewing the diffs it produces afterwards is recommended.

    To iterate on the same delegated task, call again with
    ``continue_recent=True`` (resumes the most recent Codex session) or pass a
    known session id as *resume_session_id*.

    Args:
        prompt: The coding task specification. Be concrete: goal, target files,
            constraints, and how to verify (tests/build). This is the only required arg.
        directory: Working directory the CLI runs in (usually the repo root). Defaults to ".".
        model: Optional model to use (e.g. "gpt-5.2", "o4-mini"). Empty uses the
            CLI's configured default.
        sandbox: What Codex may touch. One of "read-only", "workspace-write"
            (default; edit files in the working dir), "workspace-read-network-write",
            or "danger-full-access" (unrestricted — needed for network/git/PR flows).
            Ignored when dangerously_bypass is True.
        dangerously_bypass: Run with --dangerously-bypass-approvals-and-sandbox,
            removing the sandbox entirely. Use only for fully trusted, unattended
            tasks that need network/git access (e.g. opening a PR).
        skip_git_repo_check: Pass --skip-git-repo-check so Codex runs even when
            *directory* is not inside a git repository.
        add_dirs: Space-separated extra writable directories, quoted if they
            contain spaces. Each becomes an --add-dir flag.
        images: Comma-separated image paths to attach to the prompt.
        output_schema: Path to a JSON schema file constraining the final response.
        json_events: Also emit the raw JSONL event stream (--json) and return it
            instead of just the final message — useful for programmatic parsing.
        resume_session_id: Resume a previous Codex session by its id to continue
            iterating in the same context.
        continue_recent: Resume the most recent Codex session (exec resume --last)
            instead of starting fresh.
        timeout_seconds: Max seconds to wait; coding tasks are long, so this
            defaults to 1800 (30 min).

    Returns:
        Codex's final message (or the JSONL event stream when json_events is set),
        or an error message prefixed with "[Error]".
    """
    exe = _resolve_cli()
    if exe is None:
        return _CLI_NOT_FOUND

    if not prompt.strip():
        return "[Error] prompt is required — describe the coding task to delegate."

    mode = sandbox.strip()
    if mode and mode not in _SANDBOX_MODES:
        return f"[Error] sandbox must be one of: {', '.join(sorted(_SANDBOX_MODES))}."

    work_dir = anyio.Path(directory)
    if not await work_dir.is_dir():
        return f"[Error] directory does not exist: {directory}"

    # Temp file to capture the final agent message; read + removed after the run.
    fd, last_message_path = tempfile.mkstemp(prefix="codex_last_", suffix=".txt")
    os.close(fd)

    try:
        cmd = _build_command(
            exe,
            prompt,
            directory=directory,
            model=model,
            sandbox=mode,
            dangerously_bypass=dangerously_bypass,
            skip_git_repo_check=skip_git_repo_check,
            add_dirs=add_dirs,
            images=images,
            output_schema=output_schema,
            json_events=json_events,
            last_message_path=last_message_path,
            resume_session_id=resume_session_id,
            continue_recent=continue_recent,
        )
    except ValueError as e:  # unbalanced quotes in a shlex-parsed field
        await anyio.Path(last_message_path).unlink(missing_ok=True)
        return f"[Error] Could not parse directory arguments: {e}"

    try:
        with anyio.fail_after(timeout_seconds):
            result = await anyio.run_process(cmd, cwd=str(directory), check=False)
    except TimeoutError:
        await anyio.Path(last_message_path).unlink(missing_ok=True)
        return f"[Error] Codex delegation timed out after {timeout_seconds}s."
    except FileNotFoundError:
        await anyio.Path(last_message_path).unlink(missing_ok=True)
        return "[Error] `codex` CLI could not be launched. Ensure Node.js and Codex are installed."

    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")

    # Read the captured final message before cleaning up the temp file.
    last_message = ""
    try:
        raw = await anyio.Path(last_message_path).read_text(encoding="utf-8", errors="replace")
        last_message = raw.strip()
    except OSError:
        last_message = ""
    finally:
        await anyio.Path(last_message_path).unlink(missing_ok=True)

    if result.returncode != 0:
        detail = (err or last_message or out).strip() or "(no output)"
        return f"[Error] Codex exited with code {result.returncode}:\n{detail}"

    if json_events:
        return out.strip() or "(no output)"
    return last_message or out.strip() or "(Codex returned no result text)"
