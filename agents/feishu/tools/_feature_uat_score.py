"""Score a Haitun feature-UAT reply against playbook expect rules.

expect_any_of: list of groups; each group is OR; groups are AND.
forbid_any: any hit → fail.
"""

from __future__ import annotations

from typing import Any


def score_reply(text: str, case: dict[str, Any]) -> dict[str, Any]:
    """Return {ok, missing_groups, forbid_hits, empty}."""
    body = text if isinstance(text, str) else ""
    empty = not body.strip()
    missing: list[list[str]] = []
    for group in case.get("expect_any_of") or []:
        if not isinstance(group, list) or not group:
            continue
        needles = [str(n) for n in group if str(n).strip()]
        if not needles:
            continue
        if not any(n in body for n in needles):
            missing.append(needles)
    forbid_hits = [
        str(f)
        for f in (case.get("forbid_any") or [])
        if str(f).strip() and str(f) in body
    ]
    ok = (not empty) and (not missing) and (not forbid_hits)
    return {
        "ok": ok,
        "empty": empty,
        "missing_groups": missing,
        "forbid_hits": forbid_hits,
    }
