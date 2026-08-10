from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Literal

TOOLS_DIR = Path(__file__).resolve().parent
_mcp_path = TOOLS_DIR / "_fusion_memory_mcp.py"
_mcp_module_name = f"fusion_memory_tool__fusion_memory_mcp_{hashlib.sha256(str(_mcp_path).encode()).hexdigest()[:12]}"
_mcp_module = sys.modules.get(_mcp_module_name)
if _mcp_module is None:
    _mcp_module = types.ModuleType(_mcp_module_name)
    _mcp_module.__file__ = str(_mcp_path)
    sys.modules[_mcp_module_name] = _mcp_module
    exec(compile(_mcp_path.read_text(encoding="utf-8"), str(_mcp_path), "exec"), _mcp_module.__dict__)
CLIENT = _mcp_module.__dict__["CLIENT"]

CATEGORY = Literal["project_context", "decision", "status", "process", "constraint", "shared_reference"]
SOURCE_TYPE = Literal["feishu_message", "feishu_doc", "repository", "task", "other"]
CATEGORIES = frozenset(CATEGORY.__args__)
SOURCE_TYPES = frozenset(SOURCE_TYPE.__args__)


async def organization_memory_add(
    content: str,
    category: CATEGORY,
    source_type: SOURCE_TYPE,
    source_ref: str,
    project: str | None = None,
    observed_at: str | None = None,
    supersedes_fact_id: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Store one stable shared fact with traceable provenance for the current organization.

    Args:
        content: Confirmed, standalone fact that remains meaningful outside the current conversation.
        category: Shared fact kind from the supported organization-memory categories.
        source_type: Evidence medium from the supported source types.
        source_ref: Stable URI or identifier that lets another member inspect the evidence.
        project: Optional project or shared workstream name.
        observed_at: Optional ISO-8601 timestamp for when the source established the fact.
        supersedes_fact_id: Existing organization fact ID that this new fact explicitly replaces.
        tags: Optional short labels for retrieval; each item must be a non-empty string.
    """
    normalized_category = category.strip() if isinstance(category, str) else ""
    normalized_source_type = source_type.strip() if isinstance(source_type, str) else ""
    if normalized_category not in CATEGORIES:
        return _invalid("category is not supported")
    if normalized_source_type not in SOURCE_TYPES:
        return _invalid("source_type is not supported")
    if not isinstance(content, str) or not content.strip():
        return _invalid("content is required")
    if not isinstance(source_ref, str) or not source_ref.strip():
        return _invalid("source_ref is required")
    if tags is not None and (
        not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
    ):
        return _invalid("tags must be non-empty strings")
    result = await CLIENT.call_organization_write_tool(
        "organization_memory_add",
        {
            "content": content.strip(),
            "category": normalized_category,
            "source_type": normalized_source_type,
            "source_ref": source_ref.strip(),
            "project": project.strip() if isinstance(project, str) and project.strip() else None,
            "observed_at": observed_at.strip() if isinstance(observed_at, str) and observed_at.strip() else None,
            "supersedes_fact_id": (
                supersedes_fact_id.strip()
                if isinstance(supersedes_fact_id, str) and supersedes_fact_id.strip()
                else None
            ),
            "tags": tags,
        },
        retryable=False,
    )
    return json.dumps(result, ensure_ascii=False)


def _invalid(message: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": {"code": "invalid_argument", "message": message, "retryable": False},
        },
        ensure_ascii=False,
    )
