from __future__ import annotations


def normalize_denial_message(reason: str) -> str:
    safe_reason = reason.strip() or "request denied"
    return f"[Fusion-Guard] Security policy denied this request: {safe_reason}"
