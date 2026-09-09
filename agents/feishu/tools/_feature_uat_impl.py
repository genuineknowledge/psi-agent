"""Haitun feature-UAT runner — A drives clean Session B with playbook dialogue.

刻意为之: never chat the current Session (turn-lock deadlock). B must be a
fresh Gateway Session. Keyword scoring is deterministic v1; tune playbook needles
rather than adding an LLM judge here.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from _feature_uat_score import score_reply

DISCLAIMER = (
    "功能 UAT 结果只说明「目标 Session 上对话是否命中剧本子串规则」; "
    "不等于人工盲评通过, 也不等于组织验收盖章. 子串规则可假阴/假阳, 以改 playbook 收敛. "
    "同栈默认测的是 Gateway defaults.agent 已部署能力; "
    "测 WIP 须传 agent= 或 target_gateway_url= (跨栈走 HTTP chat)."
)

CreateSessionFn = Callable[[str], Awaitable[dict[str, Any]]]
SendMessageFn = Callable[[str, str, float], Awaitable[dict[str, Any]]]


def load_playbook(
    *,
    playbook_json: str = "",
    playbook_path: str = "",
    agent_dir: Path,
    workspace_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    raw = (playbook_json or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"invalid playbook_json: {exc}"
        if not isinstance(data, dict):
            return None, "playbook_json must be a JSON object"
        return data, ""

    rel = (playbook_path or "").strip()
    if not rel:
        return None, "playbook_path or playbook_json is required"
    if ".." in Path(rel).parts:
        return None, "playbook_path must not contain .."

    for root in (agent_dir, workspace_dir):
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return None, f"failed to read playbook: {exc}"
            if not isinstance(data, dict):
                return None, "playbook file must contain a JSON object"
            return data, ""
    return None, f"playbook not found: {rel}"


def _filter_cases(
    cases: list[dict[str, Any]],
    *,
    case_ids: set[str],
    max_cases: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        cid = str(case.get("id", "")).strip()
        if not cid:
            continue
        if case.get("skip"):
            continue
        if case_ids and cid not in case_ids:
            continue
        out.append(case)
        if max_cases > 0 and len(out) >= max_cases:
            break
    return out


def _parse_case_ids(raw: str) -> set[str]:
    if not (raw or "").strip():
        return set()
    return {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}


async def run_feature_uat(
    playbook: dict[str, Any],
    *,
    create_session: CreateSessionFn,
    send_message: SendMessageFn,
    current_session_id: str = "",
    fixed_session_id: str = "",
    case_ids: str = "",
    max_cases: int = 0,
    timeout_seconds: float = 180.0,
    target_mode: str = "local_defaults",
) -> dict[str, Any]:
    """Run dialogue cases against Session B(s). Inject create/send for tests.

    *fixed_session_id*: reuse one existing Session for every case key (no create).
    *target_mode*: recorded in the report (``local_defaults`` / ``agent_override`` /
    ``remote_gateway`` / ``fixed_session``) — does not change runner logic.
    """
    cases_raw = playbook.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        return {
            "ok": False,
            "verdict": "blocked",
            "operable": False,
            "message": "playbook.cases must be a non-empty list",
            "cases": [],
            "gaps": ["missing cases"],
            "disclaimer": DISCLAIMER,
            "target_mode": target_mode,
        }

    wanted = _parse_case_ids(case_ids)
    cases = _filter_cases(cases_raw, case_ids=wanted, max_cases=max_cases)
    if not cases:
        return {
            "ok": False,
            "verdict": "blocked",
            "operable": False,
            "message": "no cases matched case_ids / max_cases filter",
            "cases": [],
            "gaps": ["no matching cases"],
            "disclaimer": DISCLAIMER,
            "target_mode": target_mode,
        }

    fixed = fixed_session_id.strip()
    if fixed and current_session_id and fixed == current_session_id:
        return {
            "ok": False,
            "verdict": "blocked",
            "operable": False,
            "message": "refused to chat current session (deadlock risk)",
            "cases": [],
            "gaps": ["target_session_id == current session"],
            "disclaimer": DISCLAIMER,
            "target_mode": target_mode,
        }

    # session_key → created session_id
    sessions: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    gaps: list[str] = []
    blocked = False

    for case in cases:
        cid = str(case.get("id", "")).strip()
        session_key = str(case.get("session", cid)).strip() or cid
        required = bool(case.get("required", True))
        user = str(case.get("user", "")).strip()
        if not user:
            results.append(
                {
                    "id": cid,
                    "session_key": session_key,
                    "required": required,
                    "ok": False,
                    "status": "fail",
                    "detail": "case.user is empty",
                    "reply_text": "",
                    "score": {},
                }
            )
            gaps.append(f"{cid}: empty user")
            continue

        if session_key not in sessions:
            if fixed:
                sessions[session_key] = fixed
            else:
                sid_hint = f"uat-{playbook.get('id', 'feat')}-{session_key}-{uuid.uuid4().hex[:8]}"
                created = await create_session(sid_hint)
                if not created.get("ok"):
                    blocked = True
                    msg = str(created.get("message", "create_session failed"))
                    results.append(
                        {
                            "id": cid,
                            "session_key": session_key,
                            "required": required,
                            "ok": False,
                            "status": "blocked",
                            "detail": msg,
                            "reply_text": "",
                            "score": {},
                            "target_session_id": "",
                        }
                    )
                    gaps.append(f"{cid}: blocked create — {msg}")
                    continue
                new_id = str(created.get("session_id", "")).strip()
                if not new_id:
                    blocked = True
                    results.append(
                        {
                            "id": cid,
                            "session_key": session_key,
                            "required": required,
                            "ok": False,
                            "status": "blocked",
                            "detail": "create_session returned no session_id",
                            "reply_text": "",
                            "score": {},
                        }
                    )
                    gaps.append(f"{cid}: no session_id")
                    continue
                if current_session_id and new_id == current_session_id:
                    blocked = True
                    results.append(
                        {
                            "id": cid,
                            "session_key": session_key,
                            "required": required,
                            "ok": False,
                            "status": "blocked",
                            "detail": "refused to chat current session (deadlock risk)",
                            "reply_text": "",
                            "score": {},
                            "target_session_id": new_id,
                        }
                    )
                    gaps.append(f"{cid}: target == current session")
                    continue
                sessions[session_key] = new_id

        target = sessions[session_key]
        sent = await send_message(target, user, timeout_seconds)
        if not sent.get("ok"):
            # Still score empty/partial reply if any text returned
            reply = str(sent.get("reply_text", "") or sent.get("text", ""))
            if not reply.strip():
                blocked = True
                msg = str(sent.get("message", "send_message failed"))
                results.append(
                    {
                        "id": cid,
                        "session_key": session_key,
                        "required": required,
                        "ok": False,
                        "status": "blocked",
                        "detail": msg,
                        "reply_text": "",
                        "score": {},
                        "target_session_id": target,
                    }
                )
                gaps.append(f"{cid}: blocked send — {msg}")
                continue
        else:
            reply = str(sent.get("reply_text", "") or sent.get("text", ""))

        score = score_reply(reply, case)
        status = "pass" if score["ok"] else "fail"
        if not score["ok"]:
            gap_bits: list[str] = []
            if score.get("empty"):
                gap_bits.append("empty reply")
            for group in score.get("missing_groups") or []:
                gap_bits.append(f"missing any of {group}")
            if score.get("forbid_hits"):
                gap_bits.append(f"forbid hit {score['forbid_hits']}")
            gaps.append(f"{cid}: " + "; ".join(gap_bits))

        results.append(
            {
                "id": cid,
                "session_key": session_key,
                "required": required,
                "ok": bool(score["ok"]),
                "status": status,
                "detail": "",
                "reply_text": reply[:2000],
                "reply_chars": len(reply),
                "score": score,
                "target_session_id": target,
            }
        )

    required_results = [r for r in results if r.get("required", True)]
    required_failed = [r for r in required_results if r.get("status") != "pass"]
    any_blocked = blocked or any(r.get("status") == "blocked" for r in results)

    if any_blocked and not required_results:
        verdict = "blocked"
    elif any_blocked and required_failed:
        # Prefer fail when we also have scored failures; blocked if only blocks
        scored_fails = [r for r in required_failed if r.get("status") == "fail"]
        verdict = "fail" if scored_fails else "blocked"
    elif required_failed:
        verdict = "fail"
    else:
        verdict = "pass"

    return {
        "ok": verdict == "pass",
        "verdict": verdict,
        "operable": True,
        "playbook_id": str(playbook.get("id", "")),
        "title": str(playbook.get("title", "")),
        "target_mode": target_mode,
        "sessions_created": 0 if fixed else len(sessions),
        "cases": results,
        "summary": {
            "total": len(results),
            "required": len(required_results),
            "passed": sum(1 for r in results if r.get("status") == "pass"),
            "failed": sum(1 for r in results if r.get("status") == "fail"),
            "blocked": sum(1 for r in results if r.get("status") == "blocked"),
        },
        "gaps": gaps,
        "disclaimer": DISCLAIMER,
    }
