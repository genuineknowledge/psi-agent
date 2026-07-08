from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fusion_guard_security.analysis import build_intent_analysis_prompt, parse_intent_analysis_reply
from fusion_guard_security.policy import (
    build_agent_session_domain,
    build_policy_install_request,
    install_policy_with_daemon,
)

DENIAL_PREFIX = "[Fusion-Guard] Security policy denied this request"
MAX_SCRIPT_CONTEXT_BYTES = 64 * 1024
SHELL_COMMANDS = {"bash", "sh", "zsh", "dash", "ksh"}
COMMAND_SEPARATORS = {";", "&&", "||", "|"}


async def secure_bash(
    command: str,
    cwd: str | None = None,
    *,
    context_override: Any | None = None,
) -> str:
    ctx = context_override
    if ctx is None:
        return _deny("missing Dolphin session context")

    static_risk = _static_shell_risk(command)
    if static_risk:
        return _deny(f"blocked risky shell pattern before analysis: {static_risk}")

    working_dir = _working_directory(cwd, ctx)
    script_contexts = _script_contexts_for_command(command, working_dir=working_dir, ctx=ctx)

    history_messages = _history_messages_from_context(ctx)
    latest_user_message = _latest_user_message(history_messages)
    if latest_user_message is None:
        return _deny("missing latest user message in Dolphin history")

    identity = _policy_identity(ctx)
    policy_domain = build_agent_session_domain(identity["agent_id"], identity["session_id"])
    prompt = build_intent_analysis_prompt(
        history_messages=history_messages,
        latest_user_message=latest_user_message,
        session_id=identity["session_id"],
        command=command,
        script_contexts=script_contexts,
        workspace_agent_id=identity["agent_id"],
        policy_domain=policy_domain,
    )

    try:
        raw_analysis = await run_intent_analysis_via_ai_socket(prompt=prompt, ctx=ctx)
    except Exception as exc:
        return _deny(f"Fusion-Guard analysis failed: {exc}")

    parsed = parse_intent_analysis_reply(raw_analysis)
    if parsed.decision == "deny":
        return _deny("Fusion-Guard denied the requested operation")

    try:
        install_result = await install_allowed_policy(parsed.rules, ctx)
    except Exception as exc:
        return _deny(f"Fusion-Guard policy install failed: {exc}")
    if not _policy_install_succeeded(install_result):
        return _deny(_policy_install_failure_reason(install_result))

    try:
        return await execute_bash(command, cwd=cwd, ctx=ctx)
    except Exception as exc:
        return f"Error executing command: {exc}"


async def run_intent_analysis_via_ai_socket(*, prompt: str, ctx: Any) -> str:
    ai_socket = str(getattr(ctx, "ai_socket", "") or "")
    if not ai_socket:
        raise RuntimeError("missing ai_socket")

    return await _request_ai_socket(ai_socket=ai_socket, prompt=prompt)


async def install_allowed_policy(rules: list[str], ctx: Any) -> dict[str, Any]:
    identity = _policy_identity(ctx)

    request = build_policy_install_request(
        agent_id=identity["agent_id"],
        session_scope_key=identity["session_id"],
        rules=rules,
        workspace_path=identity["workspace_path"],
        workspace_root=identity["workspace_root"],
    )
    return await install_policy_with_daemon(request)


def _static_shell_risk(command: str) -> str:
    compact = " ".join(command.split())
    if re.search(
        r"(?:^|[;&|]\s*)(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+|env\s+)?(?:bash|sh|zsh|dash|ksh)\b",
        compact,
    ):
        return "remote content piped into a shell"
    if re.search(
        r"\b(?:bash|sh|zsh|dash|ksh)\b\s+(?:(?:-[A-Za-z]*c[A-Za-z]*)|--command(?:=|\s+))\s*['\"]?\$\(",
        compact,
    ):
        return "shell command substitution passed to bash -c"
    if re.search(r"(?:^|[;&|]\s*)(?:source|\.)\s+[^;&|]+", compact):
        return "source executes another file in the current shell"
    if re.search(r"(?:^|[;&|]\s*)chmod\b[^;&|]*(?:\+x|[0-7]{3,4})[^;&|]*&&\s*\./", compact):
        return "chmod followed by direct execution"
    return ""


def _script_contexts_for_command(command: str, *, working_dir: str, ctx: Any) -> list[dict[str, str]]:
    contexts: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in _script_paths_from_command(command):
        if raw_path in seen:
            continue
        seen.add(raw_path)
        contexts.append(_script_context_for_path(raw_path, working_dir=working_dir, ctx=ctx))
    return contexts


def _script_paths_from_command(command: str) -> list[str]:
    tokens = _shell_tokens(command)
    paths: list[str] = []
    for index, token in enumerate(tokens):
        if _shell_name(token) in SHELL_COMMANDS:
            shell_script = _first_shell_script_arg(tokens, index + 1)
            if shell_script:
                paths.append(shell_script)
        if _is_direct_script_execution(tokens, index):
            paths.append(token)
    return paths


def _shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return shlex.split(command, comments=False, posix=False)


def _first_shell_script_arg(tokens: list[str], start_index: int) -> str:
    index = start_index
    while index < len(tokens):
        token = tokens[index]
        if token in COMMAND_SEPARATORS:
            return ""
        if token == "--":
            index += 1
            continue
        if token == "-c" or token.startswith("--command"):
            return ""
        if token.startswith("-") and "c" in token.lstrip("-"):
            return ""
        if token.startswith("-"):
            index += 1
            continue
        return token if _is_sh_script_path(token) else ""
    return ""


def _is_direct_script_execution(tokens: list[str], index: int) -> bool:
    token = tokens[index]
    if not _is_sh_script_path(token):
        return False
    if not _looks_like_path_execution(token):
        return False
    return index == 0 or tokens[index - 1] in COMMAND_SEPARATORS


def _script_context_for_path(raw_path: str, *, working_dir: str, ctx: Any) -> dict[str, str]:
    path = _resolve_workspace_script_path(raw_path, working_dir=working_dir, ctx=ctx)
    if path is None:
        return {"path": raw_path, "content": "", "note": "not read because the path is outside the workspace"}
    display_path = _display_workspace_path(path, raw_path=raw_path, ctx=ctx)
    try:
        stat = path.stat()
    except OSError as exc:
        return {"path": display_path, "content": "", "note": f"not read: {exc}"}
    if not path.is_file():
        return {"path": display_path, "content": "", "note": "not read because the path is not a file"}

    read_size = min(stat.st_size, MAX_SCRIPT_CONTEXT_BYTES)
    try:
        with path.open("rb") as handle:
            raw = handle.read(read_size)
    except OSError as exc:
        return {"path": display_path, "content": "", "note": f"not read: {exc}"}

    note = ""
    if stat.st_size > MAX_SCRIPT_CONTEXT_BYTES:
        note = f"truncated to {MAX_SCRIPT_CONTEXT_BYTES} bytes"
    return {"path": display_path, "content": raw.decode("utf-8", errors="replace").strip(), "note": note}


def _resolve_workspace_script_path(raw_path: str, *, working_dir: str, ctx: Any) -> Path | None:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(working_dir) / path
    try:
        resolved = path.resolve(strict=False)
        workspace_path = Path(str(getattr(ctx, "workspace_path", "") or ".")).resolve(strict=False)
    except OSError:
        return None
    if not _is_relative_to(resolved, workspace_path):
        return None
    return resolved


def _display_workspace_path(path: Path, *, raw_path: str, ctx: Any) -> str:
    try:
        workspace_path = Path(str(getattr(ctx, "workspace_path", "") or ".")).resolve(strict=False)
        return path.resolve(strict=False).relative_to(workspace_path).as_posix()
    except OSError:
        return raw_path
    except ValueError:
        return raw_path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _shell_name(token: str) -> str:
    return Path(token).name


def _is_sh_script_path(token: str) -> bool:
    return token.endswith(".sh")


def _looks_like_path_execution(token: str) -> bool:
    return token.startswith((".", "~")) or any(separator in token for separator in (os.sep, os.altsep) if separator)


def _working_directory(cwd: str | None, ctx: Any) -> str:
    default_cwd = getattr(ctx, "workspace_path", None)
    return cwd or (str(default_cwd) if default_cwd else ".")


async def _selinux_enforcing_status() -> tuple[bool, str]:
    getenforce_exe = shutil.which("getenforce")
    if not getenforce_exe:
        return False, "SELinux enforcing check failed: getenforce executable was not found on PATH"
    proc = await asyncio.create_subprocess_exec(
        getenforce_exe,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or f"exit code {proc.returncode}"
        return False, f"SELinux enforcing check failed: {detail}"
    if stdout != "Enforcing":
        mode = stdout or "empty output"
        return False, f"SELinux is not enforcing: getenforce returned {mode}"
    return True, ""


async def execute_bash(command: str, *, cwd: str | None, ctx: Any) -> str:
    working_dir = _working_directory(cwd, ctx)
    bash_exe = shutil.which("bash")
    if not bash_exe:
        return "Error executing command: bash executable was not found on PATH"
    runcon_exe = shutil.which("runcon")
    if not runcon_exe:
        return "Error executing command: runcon executable was not found on PATH"
    session_id = str(getattr(ctx, "session_id", "") or "").strip()
    if not session_id:
        return "Error executing command: missing session_id"
    enforcing, enforcing_error = await _selinux_enforcing_status()
    if not enforcing:
        return f"Error executing command: {enforcing_error}"
    identity = _policy_identity(ctx)
    domain = build_agent_session_domain(identity["agent_id"], identity["session_id"])
    inner_command = _runcon_shell_command(bash_exe=bash_exe, working_dir=working_dir, command=command)
    proc = await asyncio.create_subprocess_exec(
        runcon_exe,
        "-t",
        domain,
        "--",
        bash_exe,
        "--noprofile",
        "--norc",
        "-c",
        inner_command,
        cwd=os.sep,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    if proc.returncode != 0 and stderr:
        return f"[stderr]\n{stderr}"
    if stderr:
        return f"{stdout}\n[stderr]\n{stderr}" if stdout else f"[stderr]\n{stderr}"
    return stdout or "(no output)"


def _runcon_shell_command(*, bash_exe: str, working_dir: str, command: str) -> str:
    quoted_working_dir = shlex.quote(working_dir)
    quoted_bash = shlex.quote(bash_exe)
    quoted_command = shlex.quote(command)
    return f"cd -- {quoted_working_dir} && exec {quoted_bash} --noprofile --norc -c {quoted_command}"


def _policy_identity(ctx: Any) -> dict[str, str]:
    session_id = str(getattr(ctx, "session_id", "") or "").strip()
    workspace_path = str(getattr(ctx, "workspace_path", "") or "").strip()
    if not session_id:
        raise RuntimeError("missing session_id")
    if not workspace_path:
        raise RuntimeError("missing workspace_path")

    explicit_agent_id = str(getattr(ctx, "agent_id", "") or "").strip()
    agent_id = explicit_agent_id or _workspace_agent_id(workspace_path, session_id)
    workspace_root = str(getattr(ctx, "workspace_root", "") or "").strip()
    if not workspace_root:
        workspace_root = _workspace_root_for_agent(agent_id, workspace_path)
    return {
        "agent_id": agent_id,
        "session_id": session_id,
        "workspace_path": workspace_path,
        "workspace_root": workspace_root,
    }


def _workspace_agent_id(workspace_path: str, fallback: str) -> str:
    workspace_name = Path(workspace_path).name
    if re.fullmatch(r"[A-Za-z0-9_]+", workspace_name):
        return workspace_name
    return fallback


def _workspace_root_for_agent(agent_id: str, workspace_path: str) -> str:
    path = Path(workspace_path)
    if agent_id == path.name and agent_id != "main":
        return str(path.parent)
    return workspace_path


async def _request_ai_socket(*, ai_socket: str, prompt: str) -> str:
    try:
        import aiohttp  # noqa: PLC0415
    except Exception as exc:
        raise RuntimeError("Dolphin ai_socket client dependencies are unavailable") from exc
    resolve_connector_and_endpoint = _load_dolphin_socket_resolver()

    timeout_seconds = _intent_timeout_seconds()
    connector, endpoint = resolve_connector_and_endpoint(ai_socket)
    body = {
        "messages": [{"role": "system", "content": prompt}],
        "stream": True,
        "temperature": 0,
    }
    chunks: list[str] = []

    async with (
        aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session,
        session.post(endpoint, json=body) as resp,
    ):
        if resp.status != 200:
            detail = (await resp.text())[:500]
            raise RuntimeError(f"ai_socket returned HTTP {resp.status}: {detail}")

        async for raw_line in resp.content:
            line = raw_line.decode(errors="replace").strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices", [])
            if len(choices) > 1:
                raise RuntimeError(f"intent analyzer expected one choice, got {len(choices)}")
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason") == "error":
                raise RuntimeError("ai_socket returned finish_reason=error")
            delta = choice.get("delta")
            if isinstance(delta, dict) and delta.get("content"):
                chunks.append(str(delta["content"]))

    return "".join(chunks).strip()


def _load_dolphin_socket_resolver() -> Callable[..., Any]:
    try:
        from psi_agent._sockets import resolve_connector_and_endpoint  # noqa: PLC0415

        return resolve_connector_and_endpoint
    except Exception as exc:
        raise RuntimeError("Dolphin ai_socket client dependencies are unavailable") from exc


def _history_messages_from_context(ctx: Any) -> list[dict[str, Any]]:
    in_memory_messages = _normalize_history_messages(getattr(ctx, "history_messages", None))
    host_snapshot_path = _host_history_snapshot_path(ctx)
    if _latest_user_message(in_memory_messages) is not None:
        _write_history_snapshot(host_snapshot_path, in_memory_messages)
        return in_memory_messages

    for candidate_path in _history_candidate_paths(ctx, host_snapshot_path):
        messages = _read_history_messages(candidate_path)
        if _latest_user_message(messages) is not None:
            if not _same_path(candidate_path, host_snapshot_path):
                _write_history_snapshot(host_snapshot_path, messages)
            return messages
    return in_memory_messages


def _history_candidate_paths(ctx: Any, host_snapshot_path: Path | None) -> list[Path]:
    candidates: list[Path] = []
    for attr_name in ("history_path", "workspace_history_path"):
        raw_path = getattr(ctx, attr_name, None)
        if raw_path:
            candidates.append(Path(str(raw_path)))
    if host_snapshot_path is not None:
        candidates.append(host_snapshot_path)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _host_history_snapshot_path(ctx: Any) -> Path | None:
    session_id = str(getattr(ctx, "session_id", "") or "").strip()
    workspace_path = str(getattr(ctx, "workspace_path", "") or "").strip()
    if not session_id or not workspace_path:
        return None
    root = os.environ.get("DOLPHIN_FUSION_GUARD_HISTORY_DIR", "").strip()
    base = Path(root).expanduser() if root else Path.home() / ".dolphin" / "security" / "fusion-guard-history"
    workspace_key = hashlib.sha256(workspace_path.encode("utf-8")).hexdigest()[:16]
    return base / workspace_key / f"{session_id}.jsonl"


def _write_history_snapshot(path: Path | None, messages: list[dict[str, Any]]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(message, ensure_ascii=False) for message in messages) + "\n"
        tmp_path = path.with_suffix(".jsonl.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        return


def _same_path(left: Path, right: Path | None) -> bool:
    if right is None:
        return False
    return str(left.expanduser()) == str(right.expanduser())


def _normalize_history_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("speaker") or "").strip()
        content = _message_content(item.get("content"))
        if role and content:
            messages.append({"role": role, "content": content})
    return messages


def _read_history_messages(history_path: Any) -> list[dict[str, Any]]:
    if not history_path:
        return []
    path = Path(str(history_path))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    messages: list[dict[str, Any]] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("speaker") or "").strip()
        content = _message_content(item.get("content"))
        if role and content:
            messages.append({"role": role, "content": content})
    return messages


def _latest_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            return {"role": "user", "content": str(message.get("content") or "")}
    return None


def _message_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(value).strip()


def _intent_timeout_seconds() -> float:
    raw = os.environ.get("FUSION_GUARD_INTENT_TIMEOUT_SECONDS", "60")
    try:
        value = float(raw)
    except ValueError:
        return 60.0
    return value if value > 0 else 60.0


def _deny(reason: str) -> str:
    safe_reason = reason.strip() or "request denied"
    return f"{DENIAL_PREFIX}: {safe_reason}"


def _policy_install_succeeded(result: dict[str, Any]) -> bool:
    return bool(result.get("ok")) and bool(result.get("workspaceReady", True))


def _policy_install_failure_reason(result: dict[str, Any]) -> str:
    reason = str(result.get("reason") or "").strip()
    detail = str(result.get("detail") or "").strip()
    if not reason and not detail and result.get("workspaceReady") is False:
        reason = "workspace labeling is not ready"
    if reason and detail:
        return f"Fusion-Guard policy install failed: {reason}: {detail}"
    if reason or detail:
        return f"Fusion-Guard policy install failed: {reason or detail}"
    return "Fusion-Guard policy install failed"
