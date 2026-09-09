"""Haitun feature-UAT tool — A→B dialogue runner for capability playbooks."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feature_uat_impl as _impl
import _runtime_paths as _rp
import _session_helpers as _h
import _subagent_helpers as _sub

try:
    from psi_agent.session.runtime_context import get_session_id
except ImportError:  # pragma: no cover — tool loaded outside package

    def get_session_id() -> str | None:
        return None


def _resolve_target_mode(
    *,
    agent: str,
    target_gateway_url: str,
    target_session_id: str,
) -> str:
    if target_session_id.strip():
        return "fixed_session"
    if target_gateway_url.strip():
        return "remote_gateway"
    if agent.strip():
        return "agent_override"
    return "local_defaults"


async def poc_feature_uat(
    playbook_path: str = "",
    playbook_json: str = "",
    case_ids: str = "",
    max_cases: int = 0,
    timeout_seconds: float = 180.0,
    agent: str = "",
    target_gateway_url: str = "",
    target_session_id: str = "",
) -> str:
    """Run Haitun feature UAT by driving Session B with playbook dialogue.

    Use for Haitun feature acceptance / automated UAT: load structured cases
    (user utterances + Pass/Fail substring rules), talk to a target Session,
    send turns in order, and score replies. Do not sync-chat the current session.

    Targeting (pick what the feature under test actually lives on):

    - Default: same Gateway, ``GET /defaults``.agent (already-deployed pack).
    - ``agent``: same Gateway, ``POST /sessions`` with explicit capability-pack
      path (WIP pack on this machine).
    - ``target_gateway_url``: create/chat on that Gateway via HTTP SSE
      (cross-stack; Named Pipe / Unix sockets are host-local).
    - ``target_session_id``: reuse an existing Session (optional with either
      local or ``target_gateway_url``); skips create.

    Args:
        playbook_path: Playbook under agent/workspace, e.g.
            ``skills/haitun-feature-uat/playbooks/proposal-capabilities-natural.json``.
        playbook_json: Inline playbook JSON (alternative to path).
        case_ids: Optional comma-separated case ids to run (e.g. ``A-N1,A-N3``).
        max_cases: Cap how many cases run after filtering (0 = no cap). Smoke: 1-2.
        timeout_seconds: Per-message wait for B's reply (default 180).
        agent: Optional agent-package path for new Session B.
        target_gateway_url: Optional remote Gateway base URL.
        target_session_id: Optional existing Session id (no create).

    Returns:
        JSON with ``verdict`` (``pass`` / ``fail`` / ``blocked``), ``target_mode``,
        per-case ``reply_text`` + ``score``, ``gaps``, and ``disclaimer``.
    """
    agent_root = Path(str(_rp.agent_dir()))
    workspace = Path(str(_rp.workspace_dir()))
    playbook, err = _impl.load_playbook(
        playbook_json=playbook_json,
        playbook_path=playbook_path,
        agent_dir=agent_root,
        workspace_dir=workspace,
    )
    if playbook is None:
        return json.dumps(
            {
                "ok": False,
                "verdict": "blocked",
                "operable": False,
                "message": err,
                "cases": [],
                "gaps": [err],
                "disclaimer": _impl.DISCLAIMER,
                "target_mode": _resolve_target_mode(
                    agent=agent,
                    target_gateway_url=target_gateway_url,
                    target_session_id=target_session_id,
                ),
            },
            ensure_ascii=False,
        )

    current = ""
    try:
        current = str(get_session_id() or "").strip()
    except Exception:
        current = ""

    agent_path = agent.strip()
    remote = target_gateway_url.strip().rstrip("/")
    fixed = target_session_id.strip()
    target_mode = _resolve_target_mode(
        agent=agent_path,
        target_gateway_url=remote,
        target_session_id=fixed,
    )

    async def create_session(sid_hint: str) -> dict[str, Any]:
        return await _h.create_session(
            workspace_raw="",
            session_id=sid_hint,
            ai_id="",
            agent=agent_path,
            gateway_url=remote,
            include_gateway=True,
            wait_channel=not bool(remote),
        )

    async def send_message(
        target_sid: str,
        message: str,
        timeout_s: float,
    ) -> dict[str, Any]:
        if remote:
            return await _sub.chat_via_gateway(
                gateway_url=remote,
                session_id=target_sid,
                message=message,
                timeout_seconds=timeout_s,
            )
        return await _h.send_session_message(
            target_session_id=target_sid,
            message=message,
            workspace_raw="",
            wait=True,
            timeout_seconds=timeout_s,
            include_gateway=True,
        )

    result = await _impl.run_feature_uat(
        playbook,
        create_session=create_session,
        send_message=send_message,
        current_session_id=current,
        fixed_session_id=fixed,
        case_ids=case_ids,
        max_cases=max_cases,
        timeout_seconds=timeout_seconds,
        target_mode=target_mode,
    )
    if agent_path:
        result["agent"] = agent_path
    if remote:
        result["target_gateway_url"] = remote
    if fixed:
        result["target_session_id"] = fixed
    return json.dumps(result, ensure_ascii=False)
