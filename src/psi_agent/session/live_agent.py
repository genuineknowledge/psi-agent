"""Reach the live ``SessionAgent`` from work that outlived the turn that started it.

Some work cannot finish inside the turn that began it. Feishu authorization is the
standard case: the code comes back only when the user gets around to tapping
「同意授权」, so the wait is handed to a background task and the turn ends (see
``agents/feishu/tools/_feishu_auth_watch.py`` for why waiting inside a
turn reads as "the bot is dead"). When that task finally succeeds, the thing the
user actually asked for — 把文档建在他名下 —— is still undone, and there is no turn
left to do it in: the background task has no tool loop, no model, no conversation.

Sending a plain "授权成功" message from the background does not close that gap. It
tells the user something happened while leaving the original request unexecuted, and
the bot's own message never re-enters the session, so nothing picks the work back up.
What is needed is a **turn** — the model, its tools and the prior conversation — begun
from outside any turn.

This module is the narrow seam for that. ``SessionAgent`` registers itself per session
id while it serves, and out-of-band work looks it up and calls
:func:`resume_session_turn`, which takes the agent's turn lock and runs one ordinary
turn carrying a structured message. It is deliberately **not** a general "run agent
from anywhere" facility:

- **Keyed by session id, not global.** Gateway runs many Sessions in one process; a
  process-wide "current agent" would resume whichever session registered last. The
  caller passes the id it already has from ``runtime_context.get_session_id()``.
- **Registration is tied to serving.** ``register`` returns a context manager, so an
  agent stops being reachable when its server stops. A stale handle would resume a
  conversation nobody is listening to.
- **The turn takes the lock like any other.** Resuming is not privileged: if a real
  user turn is in flight, the resume waits for it. Skipping the lock would interleave
  two turns writing the same conversation.

Delivery is the caller's job, done from inside the resumed turn by calling the normal
tools. Chunks yielded here go nowhere on their own — nothing is streaming them — so a
resumed turn that wants to speak must send a message as a tool call, exactly as a
scheduled task does.

Because nobody is streaming it, a resumed turn is also **not a chat bubble**: its rows
carry ``trigger.silent`` provenance, like every other out-of-band turn (see ``kind``
defaults below).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import aclosing, contextmanager
from typing import TYPE_CHECKING, Any

from loguru import logger

from psi_agent.session.history_display import KIND_TRIGGER_SILENT, with_kind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psi_agent.session.agent import SessionAgent

_live_agents: dict[str, SessionAgent] = {}


@contextmanager
def register(session_id: str, agent: SessionAgent) -> Iterator[None]:
    """Make *agent* reachable as *session_id* for as long as the block runs.

    Empty ids are ignored rather than sharing one slot: a resume must land on the
    conversation it came from, and "" would collide across sessions.
    """
    key = session_id.strip()
    if not key:
        yield
        return
    _live_agents[key] = agent
    logger.debug(f"Live agent registered for session {key!r}")
    try:
        yield
    finally:
        if _live_agents.get(key) is agent:
            del _live_agents[key]
        logger.debug(f"Live agent unregistered for session {key!r}")


def get(session_id: str) -> SessionAgent | None:
    """The live agent serving *session_id*, or ``None`` if nothing is serving it."""
    return _live_agents.get(session_id.strip())


def reset_all() -> None:
    """Testing hook: forget every registration."""
    _live_agents.clear()


async def resume_session_turn(
    session_id: str,
    content: str,
    *,
    kind: str = KIND_TRIGGER_SILENT,
) -> bool:
    """Run one ordinary turn on *session_id* carrying *content*; ``True`` if it ran.

    ``False`` means no live agent is serving that id — the caller must then fall back
    to whatever it can do without a turn (typically a plain notification), because the
    work will otherwise be silently dropped.

    The turn is driven to completion here: ``run`` is a generator, so an un-iterated
    stream would execute nothing at all. Chunks are drained rather than returned —
    nothing is streaming them anywhere, so the turn must deliver its own output via
    tools (see the module docstring).

    ``kind`` defaults to ``trigger.silent`` (刻意为之): a resume is an out-of-band turn,
    exactly like a trigger or a silent schedule, so neither its injected ``<event>``
    block nor its reply may surface as a chat bubble in Gateway ``/history``. ``chat``
    would leak the raw instruction block into the transcript as if the user had typed
    it, and would double the reply the turn already delivered through a tool. Callers
    wanting the reply in the Web Console pass ``trigger.display`` deliberately.
    """
    agent = get(session_id)
    if agent is None:
        logger.warning(f"No live agent for session {session_id!r}; cannot resume a turn")
        return False

    message = with_kind({"role": "user", "content": content}, kind)
    logger.info(f"Resuming a turn on session {session_id!r} ({len(content)} chars, kind={kind!r})")
    # Take the same guard a Channel turn takes (``turn_lock``: the session lock plus
    # the deferred compaction it owes on release): a resume is an ordinary turn and must
    # not interleave with one that is already writing this conversation. ``aclosing``
    # rather than a bare ``finally: aclose()``: the agent loop's own cleanup (rollback /
    # commit) must run before this frame unwinds — the ordering every other
    # ``agent.run`` call site uses.
    async with agent.turn_lock(), aclosing(agent.run(message, response_kind=kind)) as chunks:
        async for _chunk in chunks:
            pass
    logger.info(f"Resumed turn on session {session_id!r} finished")
    return True


def resume_payload(event: str, fields: dict[str, Any]) -> str:
    """Render a resume message the model reads as an event, not as user speech.

    Tagged and structured on purpose: the model has to be able to tell "the thing you
    were waiting for happened, carry on" apart from a person typing, and a bare
    sentence in the user role is indistinguishable from the latter.
    """
    lines = [f"<{event}>"]
    lines += [f"{key}: {value}" for key, value in fields.items() if str(value).strip()]
    lines.append(f"</{event}>")
    return "\n".join(lines)
