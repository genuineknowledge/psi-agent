"""Read-only legacy-row classification for manual migration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def backfill_legacy_rows(adapter: Any, user_key: str) -> list[dict[str, Any]]:
    rows: Iterable[Mapping[str, Any]] = adapter.list_legacy_rows(user_key)
    decisions: list[dict[str, Any]] = []
    for row in rows:
        record_id = row.get("record_id")
        if all(row.get(key) for key in ("source_key", "canonical_incident_id", "cross_source_fingerprint")):
            status = "already_keyed"
        else:
            status = "legacy_unkeyed"
        decisions.append({"record_id": record_id, "status": status})
    return decisions
