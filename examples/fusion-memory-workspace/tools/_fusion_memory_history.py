from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def history_paths(workspace_root: Path, session_id: str) -> tuple[Path, Path]:
    history_path = workspace_root / "histories" / f"{session_id}.jsonl"
    checkpoint_path = workspace_root / ".fusion-memory" / "haitun-history-watcher" / f"{session_id}.json"
    return history_path, checkpoint_path


def completed_history_batches(history_path: Path, session_id: str) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    batches: list[dict[str, Any]] = []
    pending_user: dict[str, Any] | None = None
    source_identity = str(history_path.resolve())
    for line_number, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = _content_text(item.get("content"))
        if role == "user":
            pending_user = {"role": "user", "content": content, "line_number": line_number} if content else None
            continue
        if role != "assistant" or pending_user is None or item.get("tool_calls") or not content:
            continue
        assistant = {"role": "assistant", "content": content, "line_number": line_number}
        batches.append(_batch(pending_user, assistant, session_id=session_id, source_identity=source_identity))
        pending_user = None
    return batches


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"submitted_batches": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {"submitted_batches": []}
    if not isinstance(payload, dict):
        return {"submitted_batches": []}
    submitted = payload.get("submitted_batches")
    if not isinstance(submitted, list) or not all(isinstance(item, str) for item in submitted):
        payload["submitted_batches"] = []
    return payload


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    texts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text") or block.get("content")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n".join(texts)


def _batch(
    user: dict[str, Any],
    assistant: dict[str, Any],
    *,
    session_id: str,
    source_identity: str,
) -> dict[str, Any]:
    line_start = int(user["line_number"])
    line_end = int(assistant["line_number"])
    messages = [
        {"role": "user", "content": user["content"]},
        {"role": "assistant", "content": assistant["content"]},
    ]
    identity = {
        "source_identity": source_identity,
        "session_id": session_id,
        "line_start": line_start,
        "line_end": line_end,
        "messages": messages,
    }
    batch_hash = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    turn_id = f"haitun:{session_id}:lines:{line_start}-{line_end}:{batch_hash}"
    return {
        "messages": messages,
        "batch_id": batch_hash,
        "metadata": {
            "source": "haitun-history-watcher",
            "history_path": source_identity,
            "line_start": line_start,
            "line_end": line_end,
            "batch_hash": batch_hash,
            "turn_id": turn_id,
            "ended_with_error": "unknown",
        },
    }
