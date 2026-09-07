"""Private AppData storage for case drafts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from _positive_negative_list.models import CaseDraft


def _path(root: str | Path, writer_key: str, case_id: str) -> Path:
    if not writer_key or not case_id:
        raise ValueError("writer and case ID are required")
    digest = hashlib.sha256(writer_key.encode()).hexdigest()
    return Path(root) / "positive-negative-list" / "drafts" / digest / f"{case_id}.json"


def save_draft(root: str | Path, writer_key: str, session_id: str, case_id: str, case: CaseDraft) -> Path:
    path = _path(root, writer_key, case_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "writer_key": writer_key,
        "session_id": session_id,
        "case_id": case_id,
        "updated_at": time.time(),
        "case": case.to_mapping(),
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    return path


def find_draft(root: str | Path, writer_key: str, session_id: str, case_id: str) -> CaseDraft | None:
    path = _path(root, writer_key, case_id)
    if not path.is_file():
        return None
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("writer_key") != writer_key or payload.get("session_id") != session_id:
        return None
    return CaseDraft.from_mapping(payload["case"])


def delete_draft_body(root: str | Path, writer_key: str, case_id: str) -> None:
    _path(root, writer_key, case_id).unlink(missing_ok=True)


def expire_drafts(root: str | Path, max_age_seconds: float = 7 * 24 * 3600) -> int:
    cutoff = time.time() - max_age_seconds
    base = Path(root) / "positive-negative-list" / "drafts"
    count = 0
    for path in base.glob("*/*.json") if base.exists() else ():
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            count += 1
    return count
