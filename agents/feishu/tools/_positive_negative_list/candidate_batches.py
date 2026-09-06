# ruff: noqa: RUF001
"""Deterministic event-package handling for meeting-note candidates.

Candidates are evidence leads, not ledger records.  This module keeps their
state in private AppData until a complete event package is sent through the
normal analysis and test-table confirmation path.
"""

from __future__ import annotations

import inspect
import json
import secrets
import time
from pathlib import Path
from typing import Any

from psi_agent._appdata import resolve_appdata_root

_resolve_appdata_root = resolve_appdata_root
_FINAL = {"merged", "ignored"}
_READY_ROW_STATUSES = {"kept", "merged", "ignored"}
_EVALUATIVE_TERMS = ("靠谱", "学习能力", "自驱力", "积极", "认真", "负责", "优秀", "能力强", "稳定")


def _now() -> float:
    return time.time()


def _text(value: Any, limit: int = 400) -> str:
    text = str(value or "").strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _state_dir(root: str | Path) -> Path:
    directory = Path(root) / "positive-negative-list" / "candidate-batches"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


async def _root_value() -> Path:
    value = _resolve_appdata_root()
    if inspect.isawaitable(value):
        value = await value
    return Path(value)


def assess_candidate_quality(text: str) -> dict[str, str]:
    """Classify whether a candidate has an observable action to analyze."""
    value = _text(text)
    if not value:
        return {"status": "invalid", "reason": "候选内容为空"}
    if any(term in value for term in _EVALUATIVE_TERMS) and not any(
        marker in value for marker in ("做了", "完成", "未", "没有", "主动", "及时", "按", "发", "写", "同步")
    ):
        return {"status": "needs_observable_behavior", "reason": "需要补充可观察行为，不能只写评价"}
    return {"status": "candidate", "reason": "已包含可继续核实的行为线索"}


def build_candidate_batch(
    *,
    person_open_id: str,
    person_name: str,
    source_label: str,
    meeting_date: str,
    candidates: list[dict[str, Any]],
    source_key: str = "",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        text = _text(item.get("text") if isinstance(item, dict) else item)
        quality = assess_candidate_quality(text)
        rows.append(
            {
                "index": index,
                "text": text,
                "source_candidates": [text] if text else [],
                "context": _text(item.get("context") if isinstance(item, dict) else "", 160),
                "quality_status": quality["status"],
                "quality_reason": quality["reason"],
                "status": "pending",
                "merged_into": "",
                "decided_at": "",
            }
        )
    return {
        "kind": "candidate_event_batch",
        "batch_id": f"cand_{secrets.token_urlsafe(10)}",
        "source_key": _text(source_key, 240),
        "person_open_id": _text(person_open_id, 160),
        "person_name": _text(person_name, 80),
        "source_label": _text(source_label, 80),
        "meeting_date": _text(meeting_date, 40),
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
        "rows": rows,
    }


def merge_candidate(batch: dict[str, Any], source_index: int, target_index: int) -> dict[str, Any]:
    rows = batch.get("rows") or []
    if source_index == target_index or not (0 <= source_index < len(rows) and 0 <= target_index < len(rows)):
        raise ValueError("source and target candidates must be different valid rows")
    source = rows[source_index]
    target = rows[target_index]
    if source.get("status") in _FINAL or target.get("status") in _FINAL:
        raise ValueError("cannot merge a finalized candidate")
    target_sources = list(target.get("source_candidates") or [target.get("text") or ""])
    for value in source.get("source_candidates") or [source.get("text") or ""]:
        if value and value not in target_sources:
            target_sources.append(value)
    target["source_candidates"] = target_sources
    target["text"] = "；".join(target_sources)
    if not target.get("context"):
        target["context"] = source.get("context") or ""
    source["status"] = "merged"
    source["merged_into"] = str(target.get("index", target_index))
    source["decided_at"] = _now()
    batch["updated_at"] = _now()
    return batch


def is_ready_for_analysis(batch: dict[str, Any]) -> bool:
    """Return true only when every source line has an explicit disposition.

    A line marked ``needs_evidence`` or ``needs_observable_behavior`` must stay
    outside the analysis path.  This prevents a meeting-note sentence from
    becoming a ledger row merely because somebody clicked through the card.
    """
    rows = batch.get("rows") or []
    return bool(rows) and all(row.get("status") in _READY_ROW_STATUSES for row in rows)


def analysis_candidates(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Project kept event packages into an agent-analysis payload.

    This is intentionally not a ``CaseDraft``: polarity, category, evidence,
    impact and remediation still require the normal conversational analysis
    step before the writer confirmation card can be shown.
    """
    if batch.get("status") not in {"ready_for_analysis", "analysis_started"}:
        return []
    result: list[dict[str, Any]] = []
    for row in batch.get("rows") or []:
        if row.get("status") != "kept":
            continue
        sources = [str(item).strip() for item in row.get("source_candidates") or [] if str(item).strip()]
        result.append(
            {
                "event_index": int(row.get("index", 0)),
                "person_name": str(batch.get("person_name") or "").strip(),
                "meeting_date": str(batch.get("meeting_date") or "").strip(),
                "context": str(row.get("context") or "").strip(),
                "observed_behavior": str(row.get("text") or "").strip(),
                "source_candidates": sources,
                "requires_case_analysis": True,
                "evidence_status": "待补充并核验",
            }
        )
    return result


async def save_batch(batch: dict[str, Any]) -> dict[str, Any]:
    root = await _root_value()
    path = _state_dir(root) / f"{batch['batch_id']}.json"
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return batch


async def load_batch(batch_id: str) -> dict[str, Any] | None:
    root = await _root_value()
    path = _state_dir(root) / f"{batch_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def find_batch_by_source_key(source_key: str) -> dict[str, Any] | None:
    if not source_key.strip():
        return None
    root = await _root_value()
    for path in _state_dir(root).glob("cand_*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("source_key") == source_key:
            return value
    return None


__all__ = [
    "analysis_candidates",
    "assess_candidate_quality",
    "build_candidate_batch",
    "find_batch_by_source_key",
    "is_ready_for_analysis",
    "load_batch",
    "merge_candidate",
    "save_batch",
]
