"""Conversation history with JSONL persistence and schedule-pending buffer.

``Conversation`` owns the conversation history (``list[dict[str, Any]]``), its
JSONL backing file, and schedule-pending chunks.  ``session_id`` is
derived from the filename stem — also reused for ``sys.modules``
isolation.

Step 4C: JSONL lives under AppData ``histories/``; legacy
``{workspace}/histories/`` is dual-read until rewritten.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

import anyio
from loguru import logger

from psi_agent._appdata import (
    appdata_history_path,
    resolve_appdata_root,
    resolve_history_read_path,
)
from psi_agent.session.protocol import AgentChunk


class Conversation:
    """Owns the conversation history, its JSONL backing file, and schedule-
    produced chunks that should be flushed before the next user message.

    ``messages`` is public so that ``agent.run()`` can read it directly.
    ``session_id`` is the filename stem of the backing file — also reused
    as the per-session identifier for ``sys.modules`` isolation.

    Turn-level atomicity: the first mutation after creation (or after
    ``commit`` / ``rollback``) automatically snapshots the current state.
    ``commit`` persists to disk and clears the snapshot; ``rollback``
    restores to the snapshot.  This ensures memory and disk are always
    synchronised at the last consistent checkpoint.

    Usable as an async context manager — on exit with an exception,
    ``rollback()`` is called automatically, restoring state to the most
    recent snapshot.
    """

    def __init__(self, *, messages: list[dict[str, Any]] | None = None, path: Path | None = None):
        self.messages: list[dict[str, Any]] = list(messages or [])
        self._pending: list[AgentChunk] = []
        self._snapshot_messages: list[dict[str, Any]] | None = None
        self._snapshot_pending: list[AgentChunk] | None = None
        self._path: Path | None = path

    @property
    def session_id(self) -> str:
        """Identifier derived from the history file path stem."""
        return self._path.stem if self._path else ""

    # -- construction ----------------------------------------------------------

    @classmethod
    async def from_workspace(
        cls,
        workspace_path: Path,
        session_id: str | None = None,
        *,
        appdata_root: str = "",
    ) -> Conversation:
        """Load history (AppData preferred, legacy workspace dual-read).

        New writes always go to ``{appdata}/histories/{session_id}.jsonl``.
        """
        if session_id is not None and not re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
            raise ValueError(f"Invalid session_id: {session_id!r} (only alphanumeric, dash, underscore allowed)")
        session_id = session_id or uuid.uuid4().hex
        logger.info(f"Starting session: {session_id}")

        resolved_appdata = appdata_root.strip() or await resolve_appdata_root()
        histories_dir = anyio.Path(resolved_appdata) / "histories"
        if not await histories_dir.is_dir():
            await histories_dir.mkdir(parents=True)
            logger.info(f"Created AppData histories directory: {histories_dir}")
            await (histories_dir / ".gitignore").write_text("*\n", encoding="utf-8")
            logger.debug(f"Created .gitignore in {histories_dir}")

        write_path = Path(str(appdata_history_path(resolved_appdata, session_id)))
        read_path = Path(
            str(
                await resolve_history_read_path(
                    appdata_root=resolved_appdata,
                    workspace=str(workspace_path),
                    session_id=session_id,
                )
            )
        )
        messages = await cls._load(read_path)
        return cls(messages=messages, path=write_path)

    # -- mutation --------------------------------------------------------------

    def add(self, msg: dict[str, Any]) -> None:
        """Append a message to history.  Automatically snapshots on the
        first mutation after creation / ``commit`` / ``rollback``."""
        self._begin_if_needed()
        self.messages.append(msg)

    def trim_after(self, index: int) -> None:
        """Delete all messages after the given index (exclusive).
        Auto-snapshots on first mutation."""
        self._begin_if_needed()
        del self.messages[index + 1 :]

    def replace_system(self, content: str) -> None:
        """Replace the system message (``messages[0]``) in-place,
        or add it if the conversation is empty.  Automatically
        snapshots on the first mutation."""
        self._begin_if_needed()
        if self.messages:
            self.messages[0] = {"role": "system", "content": content}
        else:
            self.messages.append({"role": "system", "content": content})

    def stash(self, chunks: list[AgentChunk]) -> None:
        """Store schedule-produced chunks for the next channel request."""
        self._pending = chunks

    def peek_pending(self) -> list[AgentChunk]:
        """Return a copy of pending schedule chunks without clearing.
        The caller MUST call ``clear_pending()`` after successfully yielding
        all chunks, so that a yield failure (e.g. client disconnect) does not
        permanently lose the pending chunk data."""
        return list(self._pending)

    def clear_pending(self) -> None:
        """Drop all pending schedule chunks (call after successful yield).
        Auto-snapshots to preserve pending chunks in case of rollback."""
        self._begin_if_needed()
        self._pending.clear()

    # -- turn-level snapshot ----------------------------------------------------

    def _begin_if_needed(self) -> None:
        """Lazily snapshot the current state on the first mutation."""
        if self._snapshot_messages is None:
            self._snapshot_messages = list(self.messages)
            self._snapshot_pending = list(self._pending)

    async def commit(self) -> None:
        """Persist the current messages to disk and clear the snapshot.
        The next mutation will automatically create a new snapshot."""
        await self.save()
        self._snapshot_messages = None
        self._snapshot_pending = None

    def rollback(self) -> None:
        """Restore messages and pending chunks to the most recent
        snapshot.  Idempotent — safe to call when no snapshot exists.
        Clears the snapshot so the next mutation starts fresh."""
        if self._snapshot_messages is not None:
            self.messages = self._snapshot_messages
            self._pending = self._snapshot_pending or []
            self._snapshot_messages = None
            self._snapshot_pending = None

    # -- async context manager -------------------------------------------------

    async def __aenter__(self) -> Conversation:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            self.rollback()
        return False

    # -- persistence -----------------------------------------------------------

    async def save(self) -> None:
        """Overwrite the JSONL file with the current ``messages``.  Errors
        are caught and logged — a failed save does not interrupt the session.
        Uses a tempfile + replace for atomicity."""
        if self._path is None:
            return
        try:
            parent = anyio.Path(str(self._path.parent))
            if not await parent.is_dir():
                await parent.mkdir(parents=True)
            content = "\n".join(json.dumps(msg, ensure_ascii=False) for msg in self.messages) + "\n"
            tmp_path = self._path.with_suffix(".jsonl.tmp")
            await anyio.Path(str(tmp_path)).write_text(content, encoding="utf-8")
            await anyio.Path(str(tmp_path)).replace(str(self._path))
            logger.debug(f"History saved to {self._path} ({len(self.messages)} messages)")
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    # -- internals -------------------------------------------------------------

    @staticmethod
    async def _load(path: Path) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        ap = anyio.Path(str(path))
        if not await ap.exists():
            logger.info(f"No history file found at {path}")
            return messages
        content = await ap.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                messages.append(json.loads(stripped))
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed line {lineno} in {path}")
        logger.info(f"History loaded from {path} ({len(messages)} messages)")
        return messages
