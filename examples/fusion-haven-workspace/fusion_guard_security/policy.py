from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

BLOCKED_SELINUX_TYPES = {
    "admin_home_t",
    "auditd_log_t",
    "cron_spool_t",
    "etc_runtime_t",
    "etc_t",
    "passwd_file_t",
    "root_t",
    "security_t",
    "selinux_config_t",
    "shadow_t",
    "staff_home_t",
    "sudoers_t",
    "sysfs_t",
    "user_home_dir_t",
    "user_home_t",
    "var_log_t",
}

DB_HELPER_DIR_TYPE = "fusionclaw_db_helper_dir_t"
DB_HELPER_FILE_TYPE = "fusionclaw_db_helper_file_t"
TE_DAEMON_RUNTIME_DIR_TYPE = "fusionclaw_te_daemon_runtime_dir_t"
TE_DAEMON_SOCKET_TYPE = "fusionclaw_te_daemon_socket_t"
SHARED_WORKSPACE_ROOT_TYPE = "fusionclaw_file_t"


def filter_allow_rules(lines: Iterable[str]) -> list[str]:
    rules: list[str] = []
    for line in lines:
        normalized = line.strip()
        if not normalized.startswith("allow ") or not normalized.endswith(";"):
            continue
        tokens = normalized.replace("{", " ").replace("}", " ").replace(";", " ").split()
        if any(token.split(":", 1)[0] in BLOCKED_SELINUX_TYPES for token in tokens):
            continue
        rules.append(normalized)
    return rules


def build_policy_install_request(
    *,
    agent_id: str,
    session_scope_key: str,
    rules: list[str],
    workspace_path: str,
) -> dict[str, object]:
    safe_agent_id = _assert_valid_agent_id(agent_id)
    policy = generate_agent_policy(agent_id=safe_agent_id, session_scope_key=session_scope_key)
    return {
        "agentId": safe_agent_id,
        "tePath": policy["file_name"],
        "policyContent": policy["content"],
        "cwdHint": workspace_path,
        "workspaceRoot": workspace_path,
        "extraRules": rules,
    }


def generate_agent_policy(*, agent_id: str, session_scope_key: str) -> dict[str, str]:
    safe_agent_id = _assert_valid_agent_id(agent_id)
    session_slug = _session_slug(session_scope_key)
    session_domain = build_agent_session_domain(safe_agent_id, session_scope_key)
    workspace_file_type = build_agent_workspace_file_type(safe_agent_id)
    te_file_name = f"fusionclaw_{safe_agent_id}_{session_slug}.te"
    content = f"""policy_module(fusionclaw_{safe_agent_id}_{session_slug}, 1.0.0)

require {{
    type bin_t;
    type cert_t;
    type io_uring_t;
    type user_devpts_t;
    type proc_t;
    type shell_exec_t;
    type unconfined_service_t;
    type {DB_HELPER_DIR_TYPE};
    type {DB_HELPER_FILE_TYPE};
    type {TE_DAEMON_RUNTIME_DIR_TYPE};
    type {TE_DAEMON_SOCKET_TYPE};
}}

type {SHARED_WORKSPACE_ROOT_TYPE};
files_type({SHARED_WORKSPACE_ROOT_TYPE})

type {workspace_file_type};
files_type({workspace_file_type})

type {session_domain};
domain_type({session_domain})

optional_policy(`
    gen_require(`
        role staff_r;
        type staff_t;
    ')
    role staff_r types {session_domain};
    domain_trans(staff_t, shell_exec_t, {session_domain})
    domain_trans(staff_t, bin_t, {session_domain})
')

optional_policy(`
    gen_require(`
        role sysadm_r;
        type sysadm_t;
    ')
    role sysadm_r types {session_domain};
    domain_trans(sysadm_t, shell_exec_t, {session_domain})
    domain_trans(sysadm_t, bin_t, {session_domain})
')

optional_policy(`
    gen_require(`
        role unconfined_r;
        type unconfined_t;
    ')
    role unconfined_r types {session_domain};
    domain_trans(unconfined_t, shell_exec_t, {session_domain})
    domain_trans(unconfined_t, bin_t, {session_domain})
')

allow {session_domain} shell_exec_t:file {{ entrypoint map execute execute_no_trans }};
allow {session_domain} bin_t:file {{ entrypoint map execute execute_no_trans }};
allow {session_domain} bin_t:file {{ entrypoint map execute }};
allow {session_domain} cert_t:dir {{ search open read getattr }};
allow {session_domain} cert_t:file {{ map open read getattr }};
allow {session_domain} io_uring_t:anon_inode {{ create map read write }};
allow {session_domain} proc_t:file {{ open read }};
allow {session_domain} self:process execmem;
allow {session_domain} user_devpts_t:chr_file {{ read write getattr ioctl }};
allow {session_domain} {SHARED_WORKSPACE_ROOT_TYPE}:dir {{ search open read getattr }};
allow {session_domain} {SHARED_WORKSPACE_ROOT_TYPE}:file {{ map open read getattr }};
allow {session_domain} {workspace_file_type}:dir {{ search open read write getattr add_name remove_name create rmdir }};
allow {session_domain} {workspace_file_type}:file {{ map open read write getattr create unlink ioctl }};
allow {session_domain} {DB_HELPER_DIR_TYPE}:dir {{ search getattr open read }};
allow {session_domain} {DB_HELPER_FILE_TYPE}:file {{ open read getattr execute map ioctl }};
allow {session_domain} {TE_DAEMON_RUNTIME_DIR_TYPE}:dir {{ search getattr open read }};
allow {session_domain} {TE_DAEMON_SOCKET_TYPE}:sock_file {{ open read write getattr }};
allow {session_domain} {TE_DAEMON_SOCKET_TYPE}:unix_stream_socket connectto;
allow {session_domain} unconfined_service_t:unix_stream_socket connectto;
allow {session_domain} self:tcp_socket {{ create connect getattr read write setopt getopt }};

optional_policy(`
    gen_require(`
        type user_home_dir_t;
    ')
    files_search_home({session_domain})
    allow {session_domain} user_home_dir_t:dir {{ search getattr }};
')
"""
    return {
        "file_name": te_file_name,
        "content": content,
        "domain": session_domain,
    }


async def install_policy_with_daemon(request: dict[str, object]) -> dict[str, Any]:
    try:
        import aiohttp  # noqa: PLC0415
    except Exception as exc:
        raise RuntimeError("Fusion-Guard daemon client dependencies are unavailable") from exc

    connector, endpoint = _daemon_connector_and_endpoint(aiohttp)
    headers = {"Content-Type": "application/json"}
    token = _daemon_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout_seconds = _daemon_timeout_seconds()
    async with (
        aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session,
        session.post(endpoint, json=request, headers=headers) as resp,
    ):
        raw = await resp.text()
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"te-daemon returned invalid JSON: {raw[:500]}") from exc
        if resp.status < 200 or resp.status >= 300:
            reason = body.get("reason") if isinstance(body, dict) else None
            detail = body.get("detail") if isinstance(body, dict) else None
            message = reason or detail or raw[:500] or f"HTTP {resp.status}"
            raise RuntimeError(f"te-daemon install failed: {message}")
        if not isinstance(body, dict):
            raise RuntimeError("te-daemon returned non-object JSON")
        return body


def build_agent_session_domain(agent_id: str, session_scope_key: str) -> str:
    safe_agent_id = _assert_valid_agent_id(agent_id)
    return f"fusionclaw_agent_{safe_agent_id}_session_{_session_slug(session_scope_key)}_t"


def build_agent_workspace_file_type(agent_id: str) -> str:
    return f"fusionclaw_agent_{_assert_valid_agent_id(agent_id)}_file_t"


def _daemon_connector_and_endpoint(aiohttp_module: Any) -> tuple[Any, str]:
    socket_path = os.environ.get("DOLPHIN_TE_DAEMON_SOCKET", "").strip()
    if not socket_path:
        default_socket = Path(os.sep) / "run" / "dolphin-security" / "te-daemon.sock"
        if default_socket.exists():
            socket_path = str(default_socket)
    if socket_path:
        return aiohttp_module.UnixConnector(path=socket_path), "http://localhost/install"

    raw_port = os.environ.get("DOLPHIN_TE_DAEMON_PORT", "18790")
    try:
        port = int(raw_port)
    except ValueError:
        port = 18790
    if port <= 0:
        port = 18790
    return aiohttp_module.TCPConnector(), f"http://127.0.0.1:{port}/install"


def _daemon_token() -> str:
    from_env = os.environ.get("DOLPHIN_TE_DAEMON_TOKEN", "").strip()
    if from_env:
        return from_env
    token_file = os.environ.get("DOLPHIN_TE_DAEMON_TOKEN_FILE", "").strip()
    path = Path(token_file).expanduser() if token_file else Path.home() / ".dolphin" / "security" / "te-daemon.token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _daemon_timeout_seconds() -> float:
    raw = os.environ.get("DOLPHIN_TE_DAEMON_TIMEOUT_SECONDS", os.environ.get("DOLPHIN_TE_DAEMON_TIMEOUT_MS", "30"))
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value > 1000:
        value = value / 1000
    return value if value > 0 else 30.0


def _assert_valid_agent_id(agent_id: str) -> str:
    normalized = (agent_id or "").strip()
    if not normalized:
        raise ValueError("agent_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError(f"invalid agent_id: {agent_id!r}")
    return normalized


def _session_slug(session_scope_key: str) -> str:
    return hashlib.sha256(session_scope_key.encode("utf-8")).hexdigest()[:16]
