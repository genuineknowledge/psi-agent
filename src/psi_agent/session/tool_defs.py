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
the Session.

Freezing is not trimming, and this module does not trim. *Which* tools are in
the array is decided per content layer by ``tool_exposure``, which runs before
``freeze`` — the two are separate because they answer separate questions
(``freeze``: does the array move? ``tool_exposure``: what went into it?) and
because freezing is free while narrowing carries a discovery cost that has to be
argued layer by layer.
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
