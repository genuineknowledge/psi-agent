"""Turn one整理完成的候选事件包 into the normal writer-confirmation flow.

Meeting-note candidates remain private until this tool receives a complete
analysis.  It never writes a table itself; the existing case-prepare tool
creates the writer confirmation card, and only that card's confirmation can
write the robot-owned test table.
"""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import candidate_batches
from positive_negative_list import positive_negative_case_prepare

_REQUIRED_ANALYSIS_FIELDS = (
    "observed_behavior",
    "context",
    "impact",
    "evidence_sources",
    "nature",
    "category",
    "primary_rule_id",
)


def _parse_object(value: str, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(value) if value.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _missing_analysis_fields(analysis: dict[str, Any]) -> list[str]:
    missing = [name for name in _REQUIRED_ANALYSIS_FIELDS if not analysis.get(name)]
    evidence = analysis.get("evidence_sources")
    if (
        (not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence))
        and "evidence_sources" not in missing
    ):
        missing.append("evidence_sources")
    return missing


async def positive_negative_candidate_analyze(
    batch_id: str = "",
    analysis_json: str = "",
    user_key: str = "",
) -> str:
    """Validate a complete event analysis and open the existing case card."""
    try:
        if not batch_id.strip() or not user_key.strip():
            return _f.dumps_result({"ok": False, "status": "candidate_identity_required"})
        batch = await candidate_batches.load_batch(batch_id.strip())
        if not batch:
            return _f.dumps_result({"ok": False, "status": "candidate_batch_not_found"})
        if batch.get("person_open_id") != user_key:
            return _f.dumps_result({"ok": False, "status": "unauthorized"})
        if batch.get("status") not in {"analysis_started", "ready_for_analysis"}:
            return _f.dumps_result({"ok": False, "status": "candidate_batch_not_ready"})
        candidates = candidate_batches.analysis_candidates(batch)
        if len(candidates) != 1:
            return _f.dumps_result(
                {"ok": False, "status": "candidate_event_package_count_invalid", "count": len(candidates)}
            )
        analysis = _parse_object(analysis_json, "analysis_json")
        missing = _missing_analysis_fields(analysis)
        if missing:
            return _f.dumps_result(
                {"ok": False, "status": "candidate_evidence_incomplete", "missing": missing}
            )
        nature = str(analysis.get("nature") or "").strip()
        if nature not in {"positive", "negative"}:
            return _f.dumps_result({"ok": False, "status": "candidate_nature_invalid"})
        if nature == "negative":
            missing_negative = [
                name
                for name in ("correct_behavior", "immediate_remedy", "prevention")
                if not str(analysis.get(name) or "").strip()
            ]
            if missing_negative:
                return _f.dumps_result(
                    {"ok": False, "status": "candidate_coaching_incomplete", "missing": missing_negative}
                )
        candidate = candidates[0]
        case = {
            "reporter_user_key": user_key,
            "subject_user_key": batch["person_open_id"],
            "occurred_at": str(analysis.get("occurred_at") or batch.get("meeting_date") or "").strip(),
            "observed_behavior": str(analysis["observed_behavior"]).strip(),
            "context": str(analysis["context"]).strip(),
            "impact": str(analysis["impact"]).strip(),
            "evidence_sources": analysis["evidence_sources"],
            "nature": nature,
            "category": str(analysis["category"]).strip(),
            "primary_rule_id": str(analysis["primary_rule_id"]).strip(),
            "secondary_rule_ids": analysis.get("secondary_rule_ids") or [],
            "rule_version": str(analysis.get("rule_version") or "6.0").strip(),
            "fact_summary": str(analysis.get("fact_summary") or candidate["observed_behavior"]).strip(),
            "agent_inference": str(analysis.get("agent_inference") or "根据事件包及补充证据完成判断。").strip(),
            "correct_behavior": str(analysis.get("correct_behavior") or "").strip(),
            "immediate_remedy": str(analysis.get("immediate_remedy") or "").strip(),
            "prevention": str(analysis.get("prevention") or "").strip(),
        }
        result = await positive_negative_case_prepare(
            json.dumps(case, ensure_ascii=False),
            # The candidate was surfaced in a trusted private chat.  Keep the
            # existing MVP source contract; the meeting-note label is retained
            # in the event-package metadata, not as a new write path.
            source_type="feishu_private_chat",
            source_event_id=str(batch.get("source_key") or batch_id),
            source_message_id=batch_id,
            user_key=user_key,
        )
        batch["status"] = "case_prepared"
        batch["analysis"] = {**analysis, "case_id": json.loads(result).get("case_id", "")}
        await candidate_batches.save_batch(batch)
        return result
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _f.dumps_result({"ok": False, "status": "invalid_candidate_analysis", "error": str(exc)})


__all__ = ["positive_negative_candidate_analyze"]
