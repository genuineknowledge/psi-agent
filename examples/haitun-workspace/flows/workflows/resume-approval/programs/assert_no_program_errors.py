"""Stop a workflow immediately when an upstream Program returned an error Artifact."""

from __future__ import annotations

import json
import sys
from typing import Any

_PROGRAM_ERROR_KEY = "$fusion_flow/program_error"


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> None:
    """Return silently unless a consumed Artifact is a Program error envelope."""
    del workspace_root
    if not inputs:
        raise ValueError("program error guard requires at least one input Artifact")
    for artifact_id, value in inputs.items():
        if not isinstance(value, dict) or _PROGRAM_ERROR_KEY not in value:
            continue
        error = value.get(_PROGRAM_ERROR_KEY)
        if not isinstance(error, dict):
            raise ValueError(f"upstream Program failed at {artifact_id}: malformed error envelope")
        phase = error.get("phase", "unknown")
        kind = error.get("kind", "unknown")
        message = error.get("message", "upstream Program failed")
        raise ValueError(f"upstream Program failed at {artifact_id} ({phase}/{kind}): {message}")


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
