"""Load Feishu destinations for a deterministic interview-conclusion run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any

_INTERVIEW_RECORD_ID = re.compile(r"^rec[A-Za-z0-9]{8,64}$")
_REQUIRED_FEISHU_KEYS = (
    "app_token",
    "base_url",
    "talent_pool_table_id",
    "interview_table_id",
    "report_document_id",
    "user_key",
    "identity",
)


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def _load_defaults(workspace_root: str) -> dict[str, Any]:
    relative = "flows/workflows/resume-approval/resume-approval.defaults.json"
    path = os.path.realpath(os.path.join(workspace_root, relative))
    if not _inside(path, workspace_root) or not os.path.isfile(path):
        raise FileNotFoundError(f"Required local configuration is missing: {relative}")
    with open(path, encoding="utf-8") as source:
        defaults = json.load(source)
    if not isinstance(defaults, dict):
        raise TypeError("resume-approval.defaults.json must be an object")
    return defaults


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    values = inputs.get("interview_record_ids")
    if not isinstance(values, list) or not values:
        raise ValueError("interview_record_ids must be a non-empty list")
    record_ids: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or _INTERVIEW_RECORD_ID.fullmatch(value) is None:
            raise ValueError(f"interview_record_ids[{index}] is not a valid Feishu record id")
        if value in seen:
            raise ValueError(f"interview_record_ids[{index}] is duplicated")
        seen.add(value)
        record_ids.append(value)

    defaults = _load_defaults(resolved_workspace)
    feishu_config = defaults.get("feishu_config")
    if not isinstance(feishu_config, dict):
        raise TypeError("feishu_config must be an object")
    missing = [key for key in _REQUIRED_FEISHU_KEYS if not feishu_config.get(key)]
    if missing:
        raise ValueError(f"feishu_config is missing required values: {', '.join(missing)}")
    digest = hashlib.sha256("\n".join(sorted(record_ids)).encode()).hexdigest()[:16]
    return {
        "conclusion_run_id": f"interview-conclusion-{digest}",
        "feishu_config": feishu_config,
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stdout.write(json.dumps(run(_load_inputs()), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
