"""Persist local snapshots for workflow authoring.

This module lives beside the Workflow runtime rather than in the user-visible
tool directory. The Session/runtime calls the private authoring hook directly,
so recording does not depend on the model remembering to call a tool.

Each event is one immutable JSON file with this shape:

    {
      "schema_version": 1,
      "event_id": "random-hex-id",
      "flow_key": "sha256(workspace + NUL + relative-flow-path)",
      "created_at": "UTC ISO-8601 timestamp",
      "question": "initial user message or null",
      "adjustment": "later user message or null",
      "plan": ["public observable step", "..."],
      "flow": {
        "path": "flows/example.workflow",
        "source": "complete UTF-8 source at capture time",
        "sha256": "sha256(source)"
      }
    }

Field contract:

* schema_version identifies the JSON schema and is currently 1.
* event_id is an opaque random identifier; every event gets a new one.
* flow_key groups versions of one workspace-relative flow without storing the
  absolute workspace path in the event.
* created_at is the UTC capture time and is used to order events.
* Exactly one of question and adjustment is non-null. The original user text,
  including surrounding whitespace, is preserved verbatim.
* plan contains only short, public, observable workflow steps; it is metadata
  and never controls execution or stores private chain-of-thought.
* flow.path is a validated path below the workspace flows directory.
* flow.source and flow.sha256 preserve and identify the complete source version.

The files stay local under AppData/workflow-samples and are never uploaded.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import anyio

from psi_agent._appdata import resolve_appdata_root as _resolve_appdata_root
from psi_agent.session.runtime_context import get_workspace


def _workspace_dir() -> str:
    """Resolve the current user workspace without importing a sibling tool."""

    return get_workspace() or str(Path(__file__).parents[2])


async def _resolve_flow(flow_path: str) -> tuple[anyio.Path, str, str]:
    workspace = await anyio.Path(_workspace_dir()).resolve()
    candidate = anyio.Path(flow_path)
    if candidate.is_absolute():
        raise ValueError("flow_path must be relative to the workspace")
    resolved = await (workspace / flow_path).resolve()
    flows_dir = await (workspace / "flows").resolve()
    if not Path(str(resolved)).is_relative_to(Path(str(flows_dir))):
        raise ValueError("flow_path must stay inside the workspace flows directory")
    if resolved.suffix.lower() not in {".workflow", ".g4"} or not await resolved.is_file():
        raise ValueError("flow_path must name an existing .workflow or .g4 file")
    relative = Path(str(resolved)).relative_to(Path(str(workspace))).as_posix()
    flow_key = hashlib.sha256(f"{workspace}\0{relative}".encode()).hexdigest()
    return resolved, relative, flow_key


async def workflow_sample_record(
    flow_path: str,
    plan: list[str],
    question: str = "",
    adjustment: str = "",
) -> str:
    """Save one immutable local workflow-authoring snapshot; never upload it.

    Exactly one of question and adjustment must be provided. The original text
    is stored verbatim; whitespace is only inspected to reject empty values.
    The plan is stripped item-by-item and must remain a non-empty list.

    Args:
        flow_path: Workspace-relative .workflow or .g4 path below flows/.
        plan: Short public execution summary stored in the event's plan field.
        question: Exact initial user message; stored as question and adjustment
            is left null.
        adjustment: Exact later user message; stored as adjustment and question
            is left null.

    Returns:
        A JSON envelope containing ok, event_id, flow_key, and local_path.
        The event file itself follows the schema documented at module level.
    """

    has_question = bool(question.strip())
    has_adjustment = bool(adjustment.strip())
    if has_question == has_adjustment:
        raise ValueError("provide exactly one of question or adjustment")
    normalized_plan = [item.strip() for item in plan if isinstance(item, str) and item.strip()]
    if len(normalized_plan) != len(plan) or not normalized_plan:
        raise ValueError("plan must be a non-empty list of non-empty strings")

    resolved, relative_path, flow_key = await _resolve_flow(flow_path)
    source = await resolved.read_text(encoding="utf-8")
    event = {
        "schema_version": 1,
        "event_id": secrets.token_hex(16),
        "flow_key": flow_key,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "question": question if has_question else None,
        "adjustment": adjustment if has_adjustment else None,
        "plan": normalized_plan,
        "flow": {
            "path": relative_path,
            "source": source,
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        },
    }

    appdata = anyio.Path(await _resolve_appdata_root())
    event_dir = appdata / "workflow-samples" / flow_key
    await event_dir.mkdir(parents=True, exist_ok=True)
    target = event_dir / f"{event['event_id']}.json"
    temporary = event_dir / f".{event['event_id']}.tmp"
    await temporary.write_text(
        json.dumps(event, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    await temporary.replace(target)
    return json.dumps(
        {
            "ok": True,
            "event_id": event["event_id"],
            "flow_key": flow_key,
            "local_path": str(target),
        },
        ensure_ascii=False,
    )


async def _record_workflow_authoring(
    flow_path: str,
    plan: list[str],
    user_message: str,
    *,
    workflow_touched: bool,
) -> str | None:
    """Record a checked authoring event when this turn created or changed a flow."""

    if not user_message.strip():
        return None
    resolved, _relative_path, flow_key = await _resolve_flow(flow_path)
    source_hash = hashlib.sha256((await resolved.read_text(encoding="utf-8")).encode()).hexdigest()
    event_dir = anyio.Path(await _resolve_appdata_root()) / "workflow-samples" / flow_key
    has_events = False
    latest_created_at = ""
    latest_source_hash: str | None = None
    if await event_dir.is_dir():
        async for event_path in event_dir.glob("*.json"):
            if not await event_path.is_file():
                continue
            has_events = True
            try:
                payload = json.loads(await event_path.read_text(encoding="utf-8"))
                previous_hash = payload["flow"]["sha256"]
            except KeyError, TypeError, ValueError, OSError, UnicodeError:
                latest_created_at = "\uffff"
                latest_source_hash = None
            else:
                created_at = payload.get("created_at", "")
                if not isinstance(created_at, str):
                    created_at = ""
                if created_at >= latest_created_at:
                    latest_created_at = created_at
                    latest_source_hash = previous_hash

    if has_events and not workflow_touched and latest_source_hash == source_hash:
        return None
    if has_events:
        return await workflow_sample_record(flow_path, plan, adjustment=user_message)
    return await workflow_sample_record(flow_path, plan, question=user_message)
