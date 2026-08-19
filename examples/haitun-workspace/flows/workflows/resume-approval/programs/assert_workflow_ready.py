"""Hard-stop a workflow before external writes or Human checkpoints when validation blocks."""

from __future__ import annotations

import json
import sys
from typing import Any


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> None:
    """Return silently only when every supplied validation artifact is ready."""
    del workspace_root
    if not inputs:
        raise ValueError("readiness gate requires validation artifacts")
    errors: list[str] = []
    for name, value in inputs.items():
        if isinstance(value, list):
            if not value:
                errors.append(f"{name} must be a non-empty list")
            continue
        if not isinstance(value, dict):
            errors.append(f"{name} must be a validation object or non-empty list")
            continue
        if value.get("status") != "complete":
            errors.append(f"{name}.status must equal complete")
        reported_errors = value.get("errors")
        if reported_errors not in (None, []):
            errors.append(f"{name}.errors must be empty")
        required_collection = {
            "validated_candidate_assessments": "assessments",
            "validated_hiring_conclusions": "conclusions",
        }.get(name)
        if required_collection is not None:
            collection = value.get(required_collection)
            if not isinstance(collection, list) or not collection:
                errors.append(f"{name}.{required_collection} must be a non-empty list")
    if errors:
        raise ValueError("workflow readiness gate blocked: " + "; ".join(errors))


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    run(_load_inputs())


if __name__ == "__main__":
    main()
