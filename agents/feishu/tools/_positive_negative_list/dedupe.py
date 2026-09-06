"""Deterministic same-source and cross-source deduplication primitives."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from _positive_negative_list.models import CaseDraft


@dataclass(frozen=True)
class DedupeReservation:
    source_key: str
    case_id: str
    status: str


def make_source_key(source_type: str, source_event_id: str | None, source_message_id: str | None = None) -> str:
    if not isinstance(source_type, str) or not source_type.strip():
        raise ValueError("source_type is required")
    trusted = source_event_id or source_message_id
    if not isinstance(trusted, str) or not trusted.strip() or trusted.startswith("model:"):
        raise ValueError("trusted source event or message ID is required")
    digest = hashlib.sha256(f"{source_type.strip()}\0{trusted.strip()}".encode()).digest()
    return digest.hex()


def build_cross_source_fingerprint(case: CaseDraft, dedupe_secret: str | bytes) -> str:
    secret = dedupe_secret.encode() if isinstance(dedupe_secret, str) else dedupe_secret
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("dedupe secret must be at least 32 bytes")
    subjects = ",".join(
        sorted(
            {
                identity.strip()
                for identity in case.subject_user_key.replace("\N{FULLWIDTH COMMA}", ",").split(",")
                if identity.strip()
            }
        )
    )
    payload = "\0".join(
        (
            subjects,
            case.occurred_at.strip(),
            case.primary_rule_id or "",
            case.category.strip(),
            " ".join(case.observed_behavior.split()),
        )
    )
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def reserve_source_key(appdata_root: str | Path, source_key: str, case_id: str) -> DedupeReservation:
    if not re.fullmatch(r"[0-9a-f]{64}", source_key) or not case_id:
        raise ValueError("invalid reservation input")
    directory = Path(appdata_root) / "positive-negative-list" / "dedupe"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{source_key}.json"
    record = {"source_key": source_key, "case_id": case_id, "status": "reserved"}
    encoded = json.dumps(record, separators=(",", ":")).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{source_key}.", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Hard-linking is an atomic create-if-absent operation on the same
            # filesystem, while the final path is always a complete JSON file.
            os.link(temporary, path)
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError, TypeError:
                # A file left by an older interrupted writer is not a valid
                # reservation. Reclaim it and retry once with a fresh claim.
                path.unlink(missing_ok=True)
                return reserve_source_key(appdata_root, source_key, case_id)
            if not isinstance(existing, dict) or not existing.get("case_id"):
                path.unlink(missing_ok=True)
                return reserve_source_key(appdata_root, source_key, case_id)
            status = "idempotent" if existing.get("case_id") == case_id else "exact_duplicate"
            return DedupeReservation(source_key, str(existing.get("case_id", "")), status)
        return DedupeReservation(source_key, case_id, "reserved")
    finally:
        temporary.unlink(missing_ok=True)


def release_source_key(appdata_root: str | Path, source_key: str, case_id: str) -> bool:
    """Release a reservation owned by a failed confirmation-card attempt."""
    if not re.fullmatch(r"[0-9a-f]{64}", source_key) or not case_id:
        raise ValueError("invalid reservation input")
    path = Path(appdata_root) / "positive-negative-list" / "dedupe" / f"{source_key}.json"
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    if not isinstance(existing, dict) or str(existing.get("case_id") or "") != case_id:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
