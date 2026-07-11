#!/usr/bin/env python3
"""Fusion Flow stateful-session shim.

Hijacks the flow runtime (bundle)'s one-shot "psi-agent run" call to a
subagent into "connect to a long-lived psi-agent session", so the same
subagent (same system prompt) keeps memory across multiple calls.

The bundle sends a command like:
    <this-shim> run --workspace W --message "<system>\\n\\n---\\n\\n<prompt>" \\
                --output-format text [--model M] [--ai-socket S] [--ai ...] ...
We only care about --workspace / --message; the rest is ignored.

Mechanism:
  key = sha256(system)[:16]               # stable per role each round
  no session for key -> start a psi-agent session (reusing the shared AI
    backend of this run), record key->socket
  key exists          -> reuse
  -> psi-agent channel cli --session-socket <socket> --message <prompt>
  stdout is forwarded verbatim to the bundle (which thinks it called run)

Lifecycle: every long-lived session + shared AI backend pid/socket is
registered under $FUSION_SHIM_STATE_DIR (default /tmp/fusion-shim-<ppid>),
killed by the cleanup script after the flow ends.

Environment variables (reuse the flow's existing FLOW_PSI_*):
  PSI_CMD                  psi-agent command prefix, default "uv run --no-sync psi-agent"
  FLOW_PSI_WORKSPACE       subagent execution workspace (required)
  FLOW_PSI_MODEL           model name
  FLOW_PSI_API_KEY         AI backend key
  FLOW_PSI_BASE_URL        AI backend base url
  FLOW_PSI_AI              any-llm provider name (main: ai --provider <name>), default openai
  FLOW_PSI_AI_SOCKET       if set, reuse this existing AI backend instead of starting one
  FUSION_SHIM_STATE_DIR    session/process registry dir (default derived from PPID)
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# Cross-platform advisory file lock. POSIX uses fcntl.flock; Windows uses
# msvcrt.locking. The shim runs on both (Linux servers + the Windows desktop
# build), so importing fcntl unconditionally used to crash on Windows.
if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _psi_cmd() -> list[str]:
    # posix=False on Windows so backslashes in a path-bearing PSI_CMD
    # (e.g. C:\tools\psi-agent) are NOT eaten as shell escapes by shlex.
    raw = os.environ.get("PSI_CMD", "uv run --no-sync psi-agent")
    return shlex.split(raw, posix=(os.name != "nt"))


def _popen_detached(cmd: list[str]) -> subprocess.Popen:
    """Start a detached background process cross-platform.

    POSIX: start_new_session=True (own session/process group). Windows:
    CREATE_NEW_PROCESS_GROUP (start_new_session is a POSIX-only kwarg).
    """
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


@contextmanager
def _resource_lock(state: Path, name: str):
    """Cross-process exclusive lock around a check->spawn critical section.

    Serializes concurrent shim processes (parallel flow branches) so only one
    of them wins the check->spawn race for a shared resource (the shared AI
    backend, or a per-key long-lived session). Each resource gets its own lock
    file under state/locks/, so unrelated resources never block each other.
    """
    lock_dir = state / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    with lock_path.open("w") as lf:
        if os.name == "nt":
            # msvcrt locks a byte range; lock 1 byte as an advisory whole-file lock.
            # LK_LOCK blocks (retries) until the range is available.
            lf.write("x")
            lf.flush()
            lf.seek(0)
            msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lf.seek(0)
                msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _state_dir() -> Path:
    raw = os.environ.get("FUSION_SHIM_STATE_DIR") or f"/tmp/fusion-shim-{os.getppid()}"
    d = Path(raw)
    (d / "sockets").mkdir(parents=True, exist_ok=True)
    return d


def _parse_args(argv: list[str]) -> dict[str, str]:
    """Pick the few flags we care about from the bundle's `run --flag val ...`."""
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--workspace", "--message", "--model", "--ai-socket", "--ai") and i + 1 < len(argv):
            out[a.lstrip("-")] = argv[i + 1]
            i += 2
            continue
        i += 1
    return out


def _split_message(message: str) -> tuple[str, str]:
    """message = system + '\\n\\n---\\n\\n' + prompt (whole thing is prompt if no system)."""
    sep = "\n\n---\n\n"
    if sep in message:
        system, prompt = message.split(sep, 1)
        return system, prompt
    return "", message


def _session_key(system: str) -> str:
    return hashlib.sha256(system.encode("utf-8")).hexdigest()[:16]


def _endpoint(state: Path, name: str) -> str:
    """Endpoint for a named resource (AI backend / session channel).

    POSIX: a unix-socket file under state/sockets/. Windows: a named pipe path —
    psi-agent's _sockets layer only recognizes endpoints starting with \\\\.\\pipe\\.
    The tag is a deterministic hash of the state dir so concurrent shim processes
    in the SAME run compute the SAME pipe name (session reuse depends on this).
    """
    if os.name == "nt":
        tag = hashlib.sha256(str(state).encode("utf-8")).hexdigest()[:8]
        return rf"\\.\pipe\psi-shim-{tag}-{name}"
    return str(state / "sockets" / f"{name}.sock")


def _endpoint_ready(ep: str) -> bool:
    if os.name == "nt":
        # Named pipe shows up in the pipe filesystem once the server is listening.
        return os.path.exists(ep)
    return Path(ep).is_socket()


def _wait_endpoint(ep: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _endpoint_ready(ep):
            return True
        time.sleep(0.2)
    return _endpoint_ready(ep)


def _record_pid(state: Path, kind: str, pid: int, sock: str) -> None:
    """Register a process for the cleanup script. One JSON record per line."""
    with (state / "procs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "pid": pid, "socket": str(sock)}) + "\n")


def _ensure_ai_backend(state: Path) -> str:
    """Return a usable AI backend socket path.

    Prefer reusing FLOW_PSI_AI_SOCKET; otherwise start one shared backend for
    this run (started once, socket recorded to disk).
    """
    explicit = os.environ.get("FLOW_PSI_AI_SOCKET")
    if explicit:
        return explicit

    marker = state / "ai_socket"
    if marker.exists():
        ep = marker.read_text(encoding="utf-8").strip()
        if _endpoint_ready(ep):
            return ep

    # Serialize concurrent first-calls so only one shared AI backend is started.
    with _resource_lock(state, "ai"):
        # Double-check under the lock: another shim may have started it already.
        if marker.exists():
            ep = marker.read_text(encoding="utf-8").strip()
            if _endpoint_ready(ep):
                return ep

        sock = _endpoint(state, "shared-ai")
        # main architecture: the AI backend is `ai --provider <name>` (any-llm-sdk),
        # no longer the old `ai openai-completions` subcommand.
        cmd = [
            *_psi_cmd(),
            "ai",
            "--provider",
            os.environ.get("FLOW_PSI_AI", "openai"),
            "--session-socket",
            str(sock),
        ]
        if os.environ.get("FLOW_PSI_MODEL"):
            cmd += ["--model", os.environ["FLOW_PSI_MODEL"]]
        if os.environ.get("FLOW_PSI_API_KEY"):
            cmd += ["--api-key", os.environ["FLOW_PSI_API_KEY"]]
        if os.environ.get("FLOW_PSI_BASE_URL"):
            cmd += ["--base-url", os.environ["FLOW_PSI_BASE_URL"]]

        proc = _popen_detached(cmd)
        _record_pid(state, "ai", proc.pid, sock)
        if not _wait_endpoint(sock):
            sys.stderr.write(f"[shim] AI backend endpoint not up: {sock}\n")
            sys.exit(1)
        marker.write_text(str(sock), encoding="utf-8")
        return str(sock)


def _ensure_session(state: Path, key: str, workspace: str, ai_socket: str) -> str:
    """Return the channel-socket of the long-lived session for this key; start one if absent.

    The caller MUST hold _resource_lock(state, f"sess-{key}"); the socket check
    below is the double-check of the double-checked-locking pattern.
    """
    sock = _endpoint(state, f"sess-{key}")
    if _endpoint_ready(sock):
        return sock

    # main architecture: Session no longer accepts --model (model is decided by the AI backend layer).
    cmd = [
        *_psi_cmd(),
        "session",
        "--workspace",
        workspace,
        "--channel-socket",
        sock,
        "--ai-socket",
        ai_socket,
    ]

    proc = _popen_detached(cmd)
    _record_pid(state, "session", proc.pid, sock)
    if not _wait_endpoint(sock):
        sys.stderr.write(f"[shim] session endpoint not up for key={key}: {sock}\n")
        sys.exit(1)
    return sock


def _send(channel_socket: str, prompt: str) -> int:
    """Send prompt to the long-lived session via a one-shot channel cli; forward output to the bundle."""
    cmd = [*_psi_cmd(), "channel", "cli", "--session-socket", channel_socket, "--message", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def main(argv: list[str]) -> int:
    # argv looks like: run --workspace W --message M ...  (the leading 'run' token is ignored)
    args = _parse_args(argv)
    message = args.get("message", "")
    workspace = args.get("workspace") or os.environ.get("FLOW_PSI_WORKSPACE", "")
    if not workspace:
        sys.stderr.write("[shim] no workspace (need --workspace or FLOW_PSI_WORKSPACE)\n")
        return 1

    system, prompt = _split_message(message)
    key = _session_key(system)

    state = _state_dir()
    ai_socket = _ensure_ai_backend(state)
    # Hold one per-key lock across ensure-session AND the primed decision so that
    # concurrent branches for the same role can't both spawn a session on the
    # same socket, and can't both decide they are the priming (first) call.
    with _resource_lock(state, f"sess-{key}"):
        channel_socket = _ensure_session(state, key, workspace, ai_socket)
        # The first message must carry system (so the long-lived session gets the role);
        # later calls with the same key reuse it and send only the prompt.
        first_marker = state / "sockets" / f"sess-{key}.primed"
        if not first_marker.exists():
            payload = f"{system}\n\n---\n\n{prompt}" if system else prompt
            first_marker.write_text("1", encoding="utf-8")
        else:
            payload = prompt
    return _send(channel_socket, payload)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
