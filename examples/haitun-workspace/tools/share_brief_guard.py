"""Deterministic evidence, revision, and publish checks for shared briefs."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime
from typing import Any, Literal

import anyio
from loguru import logger

from psi_agent._appdata import resolve_appdata_root as _resolve_appdata_root
from psi_agent.session.runtime_context import get_session_id as _get_session_id

SCHEMA_VERSION = 1
MAX_JSON_CHARS = 500_000
MAX_EVIDENCE = 500
MAX_CLAIMS = 500
MAX_ACTIONS = 200
VALID_SUBJECT_KINDS = frozenset({"person", "task", "both"})
VALID_CLAIM_STATUSES = frozenset({"supported", "conflict", "ambiguous", "inference", "missing"})
VALID_RETRIEVAL_STATUSES = frozenset({"complete", "partial", "truncated", "permission_denied", "failed"})
UNKNOWN_VALUES = frozenset({"", "unknown", "待确认"})
EXPLICIT_CONFIRMATIONS = (
    "确认创建草稿文档",
    "确认生成草稿文档",
    "按这个版本创建草稿文档",
    "按此版本创建草稿文档",
)
SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")

_LOCKS: dict[str, anyio.Lock] = {}


def _result(*, ok: bool, **values: Any) -> str:
    return json.dumps({"ok": ok, **values}, ensure_ascii=False, indent=2)


def _parse_json(raw: str, *, label: str, expected: type[Any]) -> tuple[Any | None, str]:
    if len(raw) > MAX_JSON_CHARS:
        return None, f"{label} exceeds {MAX_JSON_CHARS} characters"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{label} is invalid JSON: {exc.msg}"
    if not isinstance(value, expected):
        return None, f"{label} must be a JSON {expected.__name__}"
    return value, ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    return value is True


def _is_unknown(value: Any) -> bool:
    return _text(value).casefold() in UNKNOWN_VALUES


def _parse_time(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw or raw.casefold() == "unknown":
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _is_explicit_confirmation(value: str) -> bool:
    normalized = value.strip().rstrip("。.!")
    return normalized in EXPLICIT_CONFIRMATIONS


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_id(value: str, *, fallback: str) -> str:
    cleaned = SAFE_ID.sub("_", value.strip()).strip("._-")
    return cleaned[:96] or fallback


async def _state_path(brief_id: str) -> anyio.Path:
    root = anyio.Path(await _resolve_appdata_root())
    session_id = _safe_id(_get_session_id(), fallback="default")
    return root / "share_brief_guard" / session_id / f"{_safe_id(brief_id, fallback='brief')}.json"


def _lock_for(path: anyio.Path) -> anyio.Lock:
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = anyio.Lock()
        _LOCKS[key] = lock
    return lock


async def _read_state(path: anyio.Path) -> dict[str, Any] | None:
    if not await path.exists():
        return None
    try:
        value = json.loads(await path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Unable to read share brief state {path.name}: {type(exc).__name__}")
        return None
    return value if isinstance(value, dict) else None


async def _write_state(path: anyio.Path, state: dict[str, Any]) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        await temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        await temporary.replace(path)
    finally:
        with anyio.CancelScope(shield=True):
            if await temporary.exists():
                await temporary.unlink()


def _validate_scope(scope: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    blockers: list[str] = []
    subject_kind = _text(scope.get("subject_kind")).lower()
    if subject_kind not in VALID_SUBJECT_KINDS:
        errors.append(f"scope.subject_kind must be one of: {', '.join(sorted(VALID_SUBJECT_KINDS))}")
    for key in ("subject", "desired_action", "output_location"):
        if not _text(scope.get(key)):
            errors.append(f"scope.{key} is required")
    recipients = scope.get("recipients")
    if not isinstance(recipients, list) or not recipients:
        errors.append("scope.recipients must be a non-empty array")
    else:
        for index, recipient in enumerate(recipients):
            if not isinstance(recipient, dict) or not _text(recipient.get("id")):
                errors.append(f"scope.recipients[{index}].id is required")
    time_range = scope.get("time_range")
    if not isinstance(time_range, dict):
        errors.append("scope.time_range must be an object")
    else:
        start_raw = _text(time_range.get("start"))
        end_raw = _text(time_range.get("end"))
        if not start_raw or not end_raw:
            blockers.append("time range start and end must be confirmed before retrieval")
        else:
            start = _parse_time(start_raw)
            end = _parse_time(end_raw)
            if start is None or end is None:
                errors.append("scope.time_range.start/end must be ISO timestamps with UTC offsets")
            elif start > end:
                errors.append("scope.time_range.start must not be after end")
    sources = scope.get("sources")
    if not isinstance(sources, list):
        errors.append("scope.sources must be an array")
    return errors, blockers


def _validate_evidence(items: list[Any]) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    sources: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    blockers: list[str] = []
    if len(items) > MAX_EVIDENCE:
        return {}, [f"evidence cannot exceed {MAX_EVIDENCE} items"], []
    required = (
        "source_id",
        "source_type",
        "locator",
        "author",
        "event_time",
        "time_kind",
        "retrieved_at",
        "content",
        "original_url",
        "retrieval_status",
    )
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"evidence[{index}] must be an object")
            continue
        missing = [key for key in required if key not in item]
        if missing:
            errors.append(f"evidence[{index}] missing fields: {', '.join(missing)}")
            continue
        source_id = _text(item.get("source_id"))
        if not source_id:
            errors.append(f"evidence[{index}].source_id is required")
            continue
        if source_id in sources:
            errors.append(f"duplicate evidence source_id: {source_id}")
            continue
        status = _text(item.get("retrieval_status")).lower()
        if status not in VALID_RETRIEVAL_STATUSES:
            errors.append(f"evidence[{index}].retrieval_status is invalid")
        elif status != "complete":
            blockers.append(f"source {source_id} retrieval is {status}")
        if not _text(item.get("content")):
            errors.append(f"evidence[{index}].content is required")
        retrieved_at = _text(item.get("retrieved_at"))
        if _parse_time(retrieved_at) is None:
            errors.append(f"evidence[{index}].retrieved_at must be an ISO timestamp with a UTC offset")
        event_time = _text(item.get("event_time"))
        if event_time.casefold() != "unknown" and _parse_time(event_time) is None:
            errors.append(f"evidence[{index}].event_time must be unknown or an ISO timestamp with a UTC offset")
        sources[source_id] = item
    return sources, errors, blockers


def _validate_retrieval(
    scope: dict[str, Any],
    retrievals: list[Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    summaries: list[dict[str, Any]] = []
    errors: list[str] = []
    blockers: list[str] = []
    expected_sources = {
        _text(source.get("id")): source
        for source in scope.get("sources", [])
        if isinstance(source, dict) and _text(source.get("id"))
    }
    seen_sources: set[str] = set()
    start = _parse_time((scope.get("time_range") or {}).get("start"))
    for index, item in enumerate(retrievals):
        if not isinstance(item, dict):
            errors.append(f"retrieval[{index}] must be an object")
            continue
        source_id = _text(item.get("source_id"))
        requested_id = _text(item.get("requested_id"))
        actual_id = _text(item.get("actual_id"))
        status = _text(item.get("status")).lower()
        pages = item.get("pages")
        if not source_id or source_id not in expected_sources:
            errors.append(f"retrieval[{index}].source_id is not declared in scope.sources")
            continue
        seen_sources.add(source_id)
        if requested_id != _text(expected_sources[source_id].get("id")):
            blockers.append(f"retrieval {source_id} requested_id does not match locked source id")
        if actual_id != requested_id:
            blockers.append(f"retrieval {source_id} used {actual_id!r} instead of {requested_id!r}")
        if status not in VALID_RETRIEVAL_STATUSES:
            errors.append(f"retrieval[{index}].status is invalid")
        elif status != "complete":
            blockers.append(f"retrieval {source_id} is {status}")
        if not isinstance(pages, list) or not pages:
            errors.append(f"retrieval[{index}].pages must be a non-empty array")
            continue
        previous_output = ""
        input_tokens: set[str] = set()
        output_tokens: set[str] = set()
        oldest_values: list[datetime] = []
        newest_values: list[datetime] = []
        final_has_more = False
        for page_index, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f"retrieval[{index}].pages[{page_index}] must be an object")
                continue
            input_token = _text(page.get("input_page_token"))
            output_token = _text(page.get("output_page_token"))
            if page_index == 0 and input_token:
                blockers.append(f"retrieval {source_id} first page token must be empty")
            if page_index > 0 and input_token != previous_output:
                blockers.append(f"retrieval {source_id} page token chain is broken at page {page_index + 1}")
            if input_token in input_tokens:
                blockers.append(f"retrieval {source_id} repeated page token {input_token!r}")
            input_tokens.add(input_token)
            if output_token and output_token in output_tokens:
                blockers.append(f"retrieval {source_id} repeated output page token {output_token!r}")
            if output_token:
                output_tokens.add(output_token)
            previous_output = output_token
            final_has_more = _bool(page.get("has_more"))
            if final_has_more and not output_token:
                blockers.append(f"retrieval {source_id} page {page_index + 1} has_more without a next page token")
            if page_index < len(pages) - 1 and not final_has_more:
                blockers.append(f"retrieval {source_id} continued after page {page_index + 1} reported has_more=false")
            oldest = _parse_time(page.get("oldest_time"))
            newest = _parse_time(page.get("newest_time"))
            if oldest is not None:
                oldest_values.append(oldest)
            if newest is not None:
                newest_values.append(newest)
        reached_start = bool(start is not None and oldest_values and min(oldest_values) <= start)
        if final_has_more and not reached_start:
            blockers.append(f"retrieval {source_id} stopped before covering the requested start time")
        stop_reason = _text(item.get("stop_reason")) or "unknown"
        if final_has_more and reached_start and stop_reason != "start_reached":
            blockers.append(f"retrieval {source_id} stop_reason must be start_reached while more pages remain")
        if not final_has_more and stop_reason not in {"no_more", "empty", "not_paginated"}:
            blockers.append(f"retrieval {source_id} stop_reason must explain why pagination ended")
        summaries.append(
            {
                "source_id": source_id,
                "requested_id": requested_id,
                "actual_id": actual_id,
                "status": status,
                "page_count": len(pages),
                "earliest_time": min(oldest_values).isoformat() if oldest_values else "unknown",
                "latest_time": max(newest_values).isoformat() if newest_values else "unknown",
                "stop_reason": stop_reason,
            }
        )
    required_retrieval_ids = {
        source_id
        for source_id, source in expected_sources.items()
        if _text(source.get("type")).lower() not in {"user_text", "provided_text"}
    }
    for missing in sorted(required_retrieval_ids - seen_sources):
        blockers.append(f"required source {missing} has no retrieval record")
    return summaries, errors, blockers


def _supports_exact_text(
    supports: Any,
    *,
    value: str,
    sources: dict[str, dict[str, Any]],
    label: str,
) -> tuple[list[str], list[str]]:
    source_ids: list[str] = []
    errors: list[str] = []
    if not isinstance(supports, list) or not supports:
        return [], [f"{label}.supports must be a non-empty array"]
    value_supported = False
    for index, support in enumerate(supports):
        if not isinstance(support, dict):
            errors.append(f"{label}.supports[{index}] must be an object")
            continue
        source_id = _text(support.get("source_id"))
        quote = _text(support.get("quote"))
        source = sources.get(source_id)
        if source is None:
            errors.append(f"{label}.supports[{index}] references unknown source {source_id!r}")
            continue
        content = _text(source.get("content"))
        if not quote or quote not in content:
            errors.append(f"{label}.supports[{index}].quote is not an exact source substring")
            continue
        if value and value in quote:
            value_supported = True
        source_ids.append(source_id)
    if value and not value_supported:
        errors.append(f"{label}.value is not present in any exact supporting quote")
    return sorted(set(source_ids)), errors


def _validate_claims(
    items: list[Any], sources: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    claims: list[dict[str, Any]] = []
    errors: list[str] = []
    blockers: list[str] = []
    seen: set[str] = set()
    if len(items) > MAX_CLAIMS:
        return [], [f"claims cannot exceed {MAX_CLAIMS} items"], []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"claims[{index}] must be an object")
            continue
        claim_id = _text(item.get("claim_id"))
        text = _text(item.get("text"))
        status = _text(item.get("status")).lower()
        critical = _bool(item.get("critical"))
        if not claim_id or claim_id in seen:
            errors.append(f"claims[{index}].claim_id is missing or duplicated")
            continue
        seen.add(claim_id)
        if not text:
            errors.append(f"claims[{index}].text is required")
        if status not in VALID_CLAIM_STATUSES:
            errors.append(f"claims[{index}].status is invalid")
            continue
        source_ids: list[str] = []
        if status == "supported":
            source_ids, support_errors = _supports_exact_text(
                item.get("supports"), value=text, sources=sources, label=f"claims[{index}]"
            )
            errors.extend(support_errors)
        elif critical:
            blockers.append(f"critical claim {claim_id} is {status}")
        claims.append(
            {
                "claim_id": claim_id,
                "category": _text(item.get("category")) or "conclusion",
                "text": text,
                "status": status,
                "critical": critical,
                "source_ids": source_ids,
                "notes": _text(item.get("notes")),
            }
        )
    return claims, errors, blockers


def _validate_actions(
    items: list[Any], sources: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    actions: list[dict[str, Any]] = []
    errors: list[str] = []
    blockers: list[str] = []
    seen: set[str] = set()
    if len(items) > MAX_ACTIONS:
        return [], [f"actions cannot exceed {MAX_ACTIONS} items"], []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"actions[{index}] must be an object")
            continue
        action_id = _text(item.get("action_id"))
        critical = _bool(item.get("critical"))
        if not action_id or action_id in seen:
            errors.append(f"actions[{index}].action_id is missing or duplicated")
            continue
        seen.add(action_id)
        normalized: dict[str, Any] = {"action_id": action_id, "critical": critical}
        all_source_ids: set[str] = set()
        for field_name in ("action", "owner", "deadline", "completion_standard"):
            field = item.get(field_name)
            if not isinstance(field, dict):
                errors.append(f"actions[{index}].{field_name} must be an object")
                normalized[field_name] = "待确认"
                continue
            value = _text(field.get("value")) or "待确认"
            normalized[field_name] = value
            if _is_unknown(value):
                if critical and field_name in {"action", "owner", "deadline"}:
                    blockers.append(f"critical action {action_id}.{field_name} is missing")
                continue
            source_ids, support_errors = _supports_exact_text(
                field.get("supports"),
                value=value,
                sources=sources,
                label=f"actions[{index}].{field_name}",
            )
            errors.extend(support_errors)
            all_source_ids.update(source_ids)
        normalized["source_ids"] = sorted(all_source_ids)
        actions.append(normalized)
    return actions, errors, blockers


def _compare_drafts(
    scope: dict[str, Any],
    drafts: dict[str, Any],
    claims: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    blockers: list[str] = []
    expected_claims = {claim["claim_id"] for claim in claims}
    expected_actions = {action["action_id"] for action in actions}
    report: dict[str, Any] = {"required": _bool(scope.get("requires_multi_draft"))}
    if not report["required"]:
        report.update({"draft_a_missing": [], "draft_b_missing": [], "unknown_ids": []})
        return report, errors, blockers
    seen_unknown: set[str] = set()
    for name in ("draft_a", "draft_b"):
        draft = drafts.get(name)
        if not isinstance(draft, dict):
            errors.append(f"drafts.{name} must be an object when multi-draft comparison is required")
            continue
        claim_ids = {_text(value) for value in draft.get("claim_ids", []) if _text(value)}
        action_ids = {_text(value) for value in draft.get("action_ids", []) if _text(value)}
        report[f"{name}_missing_claims"] = sorted(expected_claims - claim_ids)
        report[f"{name}_missing_actions"] = sorted(expected_actions - action_ids)
        seen_unknown.update(claim_ids - expected_claims)
        seen_unknown.update(action_ids - expected_actions)
    report["unknown_ids"] = sorted(seen_unknown)
    if seen_unknown:
        blockers.append(f"drafts contain unknown claim/action ids: {', '.join(sorted(seen_unknown))}")
    return report, errors, blockers


def _source_labels(source_ids: list[str]) -> str:
    return ", ".join(source_ids) if source_ids else "待确认"


def _render_preview(
    scope: dict[str, Any],
    retrieval: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    comparison: dict[str, Any],
    blockers: list[str],
) -> str:
    recipients = scope.get("recipients", [])
    recipient_text = ", ".join(_text(item.get("name")) or _text(item.get("id")) for item in recipients)
    lines = [
        f"# {_text(scope.get('title')) or _text(scope.get('subject'))}",
        "",
        "## 共享范围与目的",
        f"- 接收对象: {recipient_text}",
        f"- 期望动作: {_text(scope.get('desired_action'))}",
        f"- 输出位置: {_text(scope.get('output_location'))}",
    ]
    for category, heading in (("background", "背景"), ("conclusion", "核心结论"), ("basis", "决策依据")):
        lines.extend(["", f"## {heading}"])
        selected = [claim for claim in claims if claim["category"] == category and claim["status"] == "supported"]
        if not selected:
            lines.append("- 待确认")
        for claim in selected:
            lines.append(f"- {claim['text']} [{_source_labels(claim['source_ids'])}]")
    lines.extend(
        [
            "",
            "## 行动项",
            "| 行动 | 负责人 | 截止时间 | 完成标准 | 依据来源 |",
            "|---|---|---|---|---|",
        ]
    )
    for action in actions:
        lines.append(
            f"| {action['action']} | {action['owner']} | {action['deadline']} | "
            f"{action['completion_standard']} | {_source_labels(action['source_ids'])} |"
        )
    if not actions:
        lines.append("| 待确认 | 待确认 | 待确认 | 待确认 | 待确认 |")
    lines.extend(["", "## 风险, 冲突与待确认"])
    unresolved = [claim for claim in claims if claim["status"] != "supported"]
    for claim in unresolved:
        lines.append(f"- {claim['status']}: {claim['text']} ({claim['claim_id']})")
    for blocker in blockers:
        lines.append(f"- blocker: {blocker}")
    if not unresolved and not blockers:
        lines.append("- 无未解决阻断项。")
    lines.extend(["", "## 检索范围与完整性说明"])
    time_range = scope.get("time_range", {})
    lines.append(f"- 请求时间: {_text(time_range.get('start'))} -> {_text(time_range.get('end'))}")
    for item in retrieval:
        lines.append(
            f"- {item['source_id']}: id={item['actual_id']}, pages={item['page_count']}, "
            f"coverage={item['earliest_time']} -> {item['latest_time']}, status={item['status']}, "
            f"stop={item['stop_reason']}"
        )
    lines.extend(["", "## 交叉审校摘要"])
    if comparison.get("required"):
        lines.append(f"- 草稿 A 遗漏结论: {comparison.get('draft_a_missing_claims', [])}")
        lines.append(f"- 草稿 A 遗漏行动: {comparison.get('draft_a_missing_actions', [])}")
        lines.append(f"- 草稿 B 遗漏结论: {comparison.get('draft_b_missing_claims', [])}")
        lines.append(f"- 草稿 B 遗漏行动: {comparison.get('draft_b_missing_actions', [])}")
        lines.append(f"- 无来源 ID: {comparison.get('unknown_ids', [])}")
    else:
        lines.append("- 单一简单来源, 未要求双草稿。")
    lines.extend(["", "## 原始资料"])
    for source_id, source in sources.items():
        lines.append(
            f"- {source_id}: type={_text(source.get('source_type'))}, author={_text(source.get('author'))}, "
            f"time={_text(source.get('event_time'))}, url={_text(source.get('original_url'))}"
        )
    return "\n".join(lines).strip()


async def _validate(
    *,
    scope_json: str,
    retrieval_json: str,
    evidence_json: str,
    claims_json: str,
    actions_json: str,
    drafts_json: str,
    brief_id: str,
) -> str:
    parsed: dict[str, Any] = {}
    for label, raw, expected in (
        ("scope", scope_json, dict),
        ("retrieval", retrieval_json, list),
        ("evidence", evidence_json, list),
        ("claims", claims_json, list),
        ("actions", actions_json, list),
        ("drafts", drafts_json, dict),
    ):
        value, error = _parse_json(raw, label=label, expected=expected)
        if error:
            return _result(ok=False, errors=[error], blockers=[])
        parsed[label] = value

    scope_errors, scope_blockers = _validate_scope(parsed["scope"])
    sources, evidence_errors, evidence_blockers = _validate_evidence(parsed["evidence"])
    retrieval, retrieval_errors, retrieval_blockers = _validate_retrieval(parsed["scope"], parsed["retrieval"])
    claims, claim_errors, claim_blockers = _validate_claims(parsed["claims"], sources)
    actions, action_errors, action_blockers = _validate_actions(parsed["actions"], sources)
    comparison, draft_errors, draft_blockers = _compare_drafts(parsed["scope"], parsed["drafts"], claims, actions)
    errors = scope_errors + evidence_errors + retrieval_errors + claim_errors + action_errors + draft_errors
    blockers = (
        scope_blockers + evidence_blockers + retrieval_blockers + claim_blockers + action_blockers + draft_blockers
    )
    if errors:
        return _result(ok=False, errors=errors, blockers=blockers)

    resolved_brief_id = _safe_id(brief_id, fallback=uuid.uuid4().hex)
    path = await _state_path(resolved_brief_id)
    logger.debug(f"Acquiring share brief prepare lock: {path.name}")
    async with _lock_for(path):
        previous = await _read_state(path)
        revision = int(previous.get("revision", 0)) + 1 if previous else 1
        preview = _render_preview(parsed["scope"], retrieval, sources, claims, actions, comparison, blockers)
        state = {
            "schema_version": SCHEMA_VERSION,
            "brief_id": resolved_brief_id,
            "revision": revision,
            "scope": parsed["scope"],
            "retrieval": retrieval,
            "evidence": parsed["evidence"],
            "claims": claims,
            "actions": actions,
            "comparison": comparison,
            "blockers": blockers,
            "preview": preview,
            "preview_hash": _hash_text(preview),
            "confirmed": False,
            "approval_token_hash": "",
        }
        await _write_state(path, state)
    return _result(
        ok=True,
        brief_id=resolved_brief_id,
        revision=revision,
        eligible_for_confirmation=not blockers,
        blockers=blockers,
        preview=preview,
        preview_hash=state["preview_hash"],
        retrieval=retrieval,
        comparison=comparison,
    )


async def _confirm(
    *,
    brief_id: str,
    revision: int,
    recipients_json: str,
    sensitive_confirmed: bool,
    confirmation_text: str,
) -> str:
    recipients, error = _parse_json(recipients_json, label="recipients", expected=list)
    if error:
        return _result(ok=False, errors=[error])
    path = await _state_path(brief_id)
    logger.debug(f"Acquiring share brief confirmation lock: {path.name}")
    async with _lock_for(path):
        state = await _read_state(path)
        if state is None:
            return _result(ok=False, errors=["brief state not found"])
        if int(state.get("revision", 0)) != revision:
            return _result(ok=False, errors=["revision is stale; show and confirm the latest preview"])
        blockers = state.get("blockers")
        if isinstance(blockers, list) and blockers:
            return _result(ok=False, errors=["brief has unresolved blockers"], blockers=blockers)
        if _canonical(recipients) != _canonical(state.get("scope", {}).get("recipients", [])):
            return _result(ok=False, errors=["recipients differ from the validated preview"])
        if not sensitive_confirmed:
            return _result(ok=False, errors=["sensitive information review is not confirmed"])
        if not _is_explicit_confirmation(confirmation_text):
            return _result(ok=False, errors=["confirmation text is ambiguous"])
        token = secrets.token_urlsafe(32)
        state["confirmed"] = True
        state["approval_token_hash"] = _hash_text(token)
        state["confirmed_preview_hash"] = state.get("preview_hash", "")
        state["confirmed_recipients"] = recipients
        await _write_state(path, state)
    return _result(
        ok=True,
        brief_id=brief_id,
        revision=revision,
        approval_token=token,
        preview_hash=state.get("preview_hash", ""),
        message="confirmation recorded; verify exact content and recipients immediately before publishing",
    )


async def _authorize_document(
    *,
    brief_id: str,
    revision: int,
    approval_token: str,
) -> str:
    path = await _state_path(brief_id)
    logger.debug(f"Acquiring share brief authorization lock: {path.name}")
    async with _lock_for(path):
        state = await _read_state(path)
        if state is None:
            return _result(ok=False, allowed=False, errors=["brief state not found"])
        content = state.get("preview") if isinstance(state.get("preview"), str) else ""
        recipients = state.get("scope", {}).get("recipients", [])
        errors: list[str] = []
        if int(state.get("revision", 0)) != revision:
            errors.append("revision is stale")
        if not state.get("confirmed"):
            errors.append("latest revision is not confirmed")
        if not secrets.compare_digest(_hash_text(approval_token), _text(state.get("approval_token_hash"))):
            errors.append("approval token is invalid")
        if _hash_text(content) != _text(state.get("confirmed_preview_hash")):
            errors.append("document content differs from the last confirmed preview")
        if _canonical(recipients) != _canonical(state.get("confirmed_recipients", [])):
            errors.append("document recipients differ from the confirmed recipients")
        blockers = state.get("blockers")
        if isinstance(blockers, list) and blockers:
            errors.append("brief has unresolved blockers")
        result: dict[str, Any] = {
            "ok": not errors,
            "allowed": not errors,
            "errors": errors,
            "brief_id": brief_id,
            "revision": revision,
            "preview_hash": state.get("preview_hash", ""),
        }
        if not errors:
            result.update(
                {
                    "document_title": _text(state.get("scope", {}).get("title"))
                    or _text(state.get("scope", {}).get("subject")),
                    "document_content": content,
                    "recipients": recipients,
                    "instruction": "Create a draft document with this exact title and content; do not share it.",
                }
            )
        return json.dumps(result, ensure_ascii=False, indent=2)


async def _get(*, brief_id: str) -> str:
    state = await _read_state(await _state_path(brief_id))
    if state is None:
        return _result(ok=False, errors=["brief state not found"])
    public = {key: value for key, value in state.items() if key != "approval_token_hash"}
    return _result(ok=True, state=public)


def _compact_retrievals(items: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(items, list):
        return [], ["prepare.retrievals must be an array"]
    expanded: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"prepare.retrievals[{index}] must be an object")
            continue
        tokens = item.get("page_tokens")
        more = item.get("has_more")
        oldest = item.get("oldest_times")
        newest = item.get("newest_times")
        if (
            not isinstance(tokens, list)
            or not isinstance(more, list)
            or not isinstance(oldest, list)
            or not isinstance(newest, list)
        ):
            errors.append(
                f"prepare.retrievals[{index}] page_tokens, has_more, oldest_times, and newest_times must be arrays"
            )
            continue
        arrays = (tokens, more, oldest, newest)
        lengths = {len(value) for value in arrays}
        if lengths == {0}:
            tokens = [""]
            more = [False]
            oldest = ["unknown"]
            newest = ["unknown"]
            arrays = (tokens, more, oldest, newest)
            lengths = {1}
        if len(lengths) != 1:
            errors.append(f"prepare.retrievals[{index}] page arrays must have the same non-zero length")
            continue
        pages: list[dict[str, Any]] = []
        input_token = ""
        for page_index in range(len(tokens)):
            output_token = _text(tokens[page_index])
            pages.append(
                {
                    "input_page_token": input_token,
                    "output_page_token": output_token,
                    "has_more": more[page_index],
                    "oldest_time": oldest[page_index],
                    "newest_time": newest[page_index],
                }
            )
            input_token = output_token
        requested_id = _text(item.get("requested_id"))
        expanded.append(
            {
                "source_id": _text(item.get("source_id")),
                "requested_id": requested_id,
                "actual_id": requested_id,
                "status": _text(item.get("status")) or "complete",
                "stop_reason": _text(item.get("stop_reason")) or "unknown",
                "pages": pages,
            }
        )
    return expanded, errors


def _compact_evidence(items: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(items, list):
        return [], ["prepare.evidence must be an array"]
    expanded: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"prepare.evidence[{index}] must be an object")
            continue
        expanded.append(
            {
                "source_id": _text(item.get("id")),
                "source_type": _text(item.get("type")),
                "locator": _text(item.get("locator")),
                "author": _text(item.get("author")) or "unknown",
                "event_time": _text(item.get("time")) or "unknown",
                "time_kind": _text(item.get("time_kind")) or "unknown",
                "retrieved_at": _text(item.get("retrieved_at")),
                "content": _text(item.get("text")),
                "original_url": _text(item.get("url")) or "unknown",
                "retrieval_status": _text(item.get("status")) or "complete",
            }
        )
    return expanded, errors


def _compact_claims(items: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(items, list):
        return [], ["prepare.claims must be an array"]
    expanded: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"prepare.claims[{index}] must be an object")
            continue
        value = _text(item.get("value"))
        source_id = _text(item.get("source_id"))
        supports = [{"source_id": source_id, "quote": value}] if source_id and value else []
        expanded.append(
            {
                "claim_id": _text(item.get("id")),
                "category": _text(item.get("section")) or "conclusion",
                "text": value,
                "status": _text(item.get("status")),
                "critical": _bool(item.get("critical")),
                "supports": supports,
                "notes": _text(item.get("notes")),
            }
        )
    return expanded, errors


def _compact_actions(items: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(items, list):
        return [], ["prepare.actions must be an array"]
    expanded: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"prepare.actions[{index}] must be an object")
            continue
        action: dict[str, Any] = {
            "action_id": _text(item.get("id")),
            "critical": _bool(item.get("critical")),
        }
        for field_name in ("action", "owner", "deadline", "completion_standard"):
            value = _text(item.get(field_name)) or "待确认"
            source_id = _text(item.get(f"{field_name}_source_id"))
            supports = [{"source_id": source_id, "quote": value}] if source_id and not _is_unknown(value) else []
            action[field_name] = {"value": value, "supports": supports}
        expanded.append(action)
    return expanded, errors


async def _prepare_compact(request: dict[str, Any]) -> str:
    scope = request.get("scope")
    if not isinstance(scope, dict):
        return _result(ok=False, errors=["prepare.scope must be an object"], blockers=[])
    retrievals, retrieval_errors = _compact_retrievals(request.get("retrievals", []))
    evidence, evidence_errors = _compact_evidence(request.get("evidence"))
    claims, claim_errors = _compact_claims(request.get("claims"))
    actions, action_errors = _compact_actions(request.get("actions"))
    compact_errors = retrieval_errors + evidence_errors + claim_errors + action_errors
    if compact_errors:
        return _result(ok=False, errors=compact_errors, blockers=[])

    expanded_scope = {
        "title": _text(scope.get("title")),
        "subject_kind": _text(scope.get("subject_kind")),
        "subject": _text(scope.get("subject")),
        "time_range": {"start": _text(scope.get("time_start")), "end": _text(scope.get("time_end"))},
        "sources": scope.get("sources", []),
        "recipients": scope.get("recipients", []),
        "desired_action": _text(scope.get("desired_action")),
        "output_location": _text(scope.get("output_location")),
        "requires_multi_draft": True,
    }
    claim_ids = [_text(item.get("claim_id")) for item in claims if _text(item.get("claim_id"))]
    action_ids = [_text(item.get("action_id")) for item in actions if _text(item.get("action_id"))]
    internal_drafts = {
        "draft_a": {"claim_ids": claim_ids, "action_ids": action_ids},
        "draft_b": {"claim_ids": list(reversed(claim_ids)), "action_ids": list(reversed(action_ids))},
    }
    raw = await _validate(
        scope_json=_canonical(expanded_scope),
        retrieval_json=_canonical(retrievals),
        evidence_json=_canonical(evidence),
        claims_json=_canonical(claims),
        actions_json=_canonical(actions),
        drafts_json=_canonical(internal_drafts),
        brief_id=_text(request.get("brief_id")),
    )
    result = json.loads(raw)
    if not result.get("ok"):
        return raw
    preview = _text(result.get("preview"))
    expected_values = [
        _text(claim.get("text")) for claim in claims if _text(claim.get("status")).lower() == "supported"
    ]
    for action in actions:
        for field_name in ("action", "owner", "deadline", "completion_standard"):
            field = action.get(field_name)
            if isinstance(field, dict) and not _is_unknown(field.get("value")):
                expected_values.append(_text(field.get("value")))
    missing_values = sorted({value for value in expected_values if value and value not in preview})
    if missing_values:
        return _result(ok=False, errors=["internal preview coverage audit failed"], missing_values=missing_values)
    result["audit"] = {
        "mode": "internal_dual_projection_and_exact_preview_coverage",
        "projection_a_ids": claim_ids + action_ids,
        "projection_b_ids": list(reversed(action_ids)) + list(reversed(claim_ids)),
        "missing_values": [],
    }
    result["display_instruction"] = "Return preview verbatim as the complete user-visible body; add nothing."
    return json.dumps(result, ensure_ascii=False, indent=2)


async def share_brief_guard(
    operation: Literal["prepare", "confirm", "authorize_document", "get"],
    request_json: str,
) -> str:
    """Prepare, confirm, authorize, or read one exact evidence-backed brief.

    Claim and action values are treated as exact quotes and matched as literal
    source substrings. This tool derives pagination chains, renders the preview,
    stores revisions, and returns frozen content for an unshared draft document.
    It never reads Feishu, changes permissions, sends messages, or publishes.

    Args:
        operation: prepare, confirm, authorize_document, or get.
        request_json: Compact JSON request described by the feishu-share-brief skill.
    """
    logger.debug(f"Executing share_brief_guard operation={operation}")
    request, error = _parse_json(request_json, label="request", expected=dict)
    if error or not isinstance(request, dict):
        return _result(ok=False, errors=[error])
    if operation == "prepare":
        return await _prepare_compact(request)
    brief_id = _text(request.get("brief_id"))
    if operation == "get":
        return await _get(brief_id=brief_id)
    state = await _read_state(await _state_path(brief_id))
    if state is None:
        return _result(ok=False, errors=["brief state not found"])
    if operation == "confirm":
        return await _confirm(
            brief_id=brief_id,
            revision=int(request.get("revision", 0)),
            recipients_json=_canonical(state.get("scope", {}).get("recipients", [])),
            sensitive_confirmed=_bool(request.get("sensitive_confirmed")),
            confirmation_text=_text(request.get("confirmation_text")),
        )
    if operation == "authorize_document":
        return await _authorize_document(
            brief_id=brief_id,
            revision=int(request.get("revision", 0)),
            approval_token=_text(request.get("approval_token")),
        )
    return _result(ok=False, errors=["operation must be prepare, confirm, authorize_document, or get"])
