"""Assemble the request's ``tools`` array, and hold it still for the Session.

``tools`` is part of the upstream prefix-cache key. Measured against
deepseek-v4-flash on 2026-09-03: removing one tool from an 8-tool array dropped
the cache hit from 19456 to 13568 tokens even though the tool region was 0.7%
of the body. Every change to the array therefore costs a re-prefill of
everything cached behind it.

The array is not naturally stable. ``ToolRegistry.refresh()`` re-reads the tool
roots at the top of every turn, so a tool file appearing mid-Session — or an
edited description, or a registry that happens to enumerate in another order —
rewrites it. Production showed this directly: a build that exposed tools as they
were first used logged ``tools_exposed=53 of 210`` against a 49-entry list, i.e.
the array had already changed at least four times in one Session.

So the array is frozen: assembled once, then reused verbatim for the life of
the Session. Freezing is deliberately *not* trimming — the first array is sent
whole, all 210 tools of it. Reducing the tool count is a separate change with
its own capability-loss risk; this one only stops the array from moving, which
is free.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Protocol


class _ToolLike(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]


# ``Mapping``, not ``dict``: only ``.values()`` is used, and ``dict`` is
# invariant in its value type — a ``dict[str, ConcreteTool]`` would not be
# accepted, which rules out passing a registry of any real tool class.
def build_tool_defs(tools: Mapping[str, _ToolLike]) -> list[dict[str, Any]]:
    """Render a registry's tools as an OpenAI-shaped ``tools`` array."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools.values()
    ]


# TMPFIX-20260902 (M2), deploy-only — NOT part of the merged batch.
#
# Kept because production measured 285566 → 83725 chars, 省 70.7% with this gate
# on, and the merged batch deliberately scoped trimming out (see this module's
# docstring: "Freezing is deliberately *not* trimming"). Deploying without it
# would hand that 70.7% back on every turn.
#
# Correctness does not depend on this list: dispatch resolves names through
# ``ToolRegistry``, not through the ``tools`` array, so an omitted tool stays
# callable once ``tool_search`` surfaces its name. What narrows is discovery,
# not capability. Names measured from 3h of production traffic (496 calls, 44
# distinct). Remove together with the rest of tmpfix-20260902.
TMPFIX_M2_CORE_TOOLS = frozenset(
    {
        "bash",
        "read",
        "edit",
        "write",
        "list_dir",
        "find_files",
        "search_content",
        "fetch",
        "todo",
        "clarify",
        "tool_search",
        "tool_describe",
        "tool_search_code",
        "serper_google_search",
        "wiki_search",
        "describe_image",
        "read_document",
        "read_pdf",
        "write_word",
        "write_word_from_markdown",
        "session_keyword_search",
        "sessions_history",
        "session_status",
        "memory_search",
        "memory_answer_context",
        "feishu_api",
        "feishu_attendance_query",
        "feishu_doc_read",
        "feishu_doc_update_block",
        "feishu_doc_list_blocks",
        "feishu_doc_append_content",
        "feishu_doc_create",
        "feishu_docs_search",
        "feishu_sheet_read",
        "feishu_sheet_write",
        "feishu_sheet_read_grid",
        "feishu_sheet_find_columns",
        "feishu_wiki_list_nodes",
        "feishu_wiki_list_spaces",
        "feishu_message_list",
        "feishu_message_send",
        "feishu_image_get",
        "feishu_identity_get",
        "feishu_department_members",
        "feishu_permission_list_members",
        "trigger_manage",
    }
)


def tmpfix_m2_gate(tools: Mapping[str, _ToolLike]) -> dict[str, _ToolLike]:
    """Narrow a registry to the M2 core set, leaving dispatch untouched.

    Returns the registry unchanged when no core tool is present at all — that
    is the async-load window where the registry is still filling, and gating it
    there would freeze a Session onto a near-empty array.
    """
    kept = {name: tool for name, tool in tools.items() if name in TMPFIX_M2_CORE_TOOLS}
    return kept or dict(tools)


class ToolDefsCache:
    """Holds one Session's ``tools`` array still after its first assembly.

    Per Session, not global: two Sessions can run different agent packs in the
    same process, and a shared array would send one pack's tools to the other.

    Empty input does not freeze. Tool roots load asynchronously, so the first
    turn can legitimately see an empty registry; freezing that would leave the
    Session with no tools for as long as it lived.
    """

    def __init__(self) -> None:
        self._frozen: list[dict[str, Any]] | None = None

    @property
    def is_frozen(self) -> bool:
        return self._frozen is not None

    def freeze(self, tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return this Session's array: the first non-empty one it was given.

        A deep copy goes in and a deep copy comes out. The caller owns the list
        it receives and the request path does mutate its own copy (``pop`` of
        the stream-only fields, for one); without copying, that would edit the
        frozen array and the "stable" prefix would drift after all.
        """
        if self._frozen is None:
            if not tool_defs:
                return []
            self._frozen = copy.deepcopy(tool_defs)
        return copy.deepcopy(self._frozen)
