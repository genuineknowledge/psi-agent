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

    def __init__(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        path: Path | None = None,
        persisted_bytes: int | None = None,
    ):
        self.messages: list[dict[str, Any]] = list(messages or [])
        self._pending: list[AgentChunk] = []
        self._snapshot_messages: list[dict[str, Any]] | None = None
        self._snapshot_pending: list[AgentChunk] | None = None
        self._path: Path | None = path
        # Append-only bookkeeping (see ``save``).  ``_persisted_count`` counts the
        # messages already on disk and ``_persisted_bytes`` the size we left the
        # file at; a mismatch against the real size means somebody else wrote to
        # it, so we rewrite rather than append onto an unknown tail.
        #
        # ``persisted_bytes`` carries the baseline over from ``_load_with_baseline``
        # so a session resumed from disk can append straight away.  Without it
        # every restart would pay one full rewrite — which is the common case in
        # production, where Gateway reloads history from the file.  Omitted (or
        # ``None``) means "we have not written this file", so the first save
        # rewrites it and takes ownership from there.
        self._persisted_count: int = len(self.messages) if persisted_bytes is not None else 0
        self._persisted_bytes: int = persisted_bytes or 0
        self._rewrite_needed: bool = False

    @property
    def session_id(self) -> str:
        """Identifier derived from the history file path stem."""
        return self._path.stem if self._path else ""

    @property
    def history_path(self) -> Path | None:
        """Backing history path used for the most recent successful commit."""
        return self._path

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
        messages, baseline = await cls._load_with_baseline(read_path)
        # Only inherit the append baseline when the file we read is the file we
        # write.  On the legacy dual-read path they differ, and the AppData file
        # does not exist yet — appending to it would drop the loaded history.
        if read_path != write_path:
            baseline = None
        return cls(messages=messages, path=write_path, persisted_bytes=baseline)

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
        # Dropped lines have to leave the file; appending cannot remove anything.
        self._rewrite_needed = True

    def truncate_to(self, length: int) -> None:
        """Keep only ``messages[:length]`` (drop the rest).

        Used when an early-committed turn is abandoned (Stop / disconnect):
        the user row was already on disk, so ``rollback()`` alone cannot
        remove it — the snapshot was cleared by that early ``commit()``.
        """
        if length < 0:
            raise ValueError(f"truncate_to length must be >= 0, got {length}")
        self._begin_if_needed()
        if length < len(self.messages):
            del self.messages[length:]
            self._rewrite_needed = True

    def replace_system(self, content: str) -> None:
        """Replace the system message (``messages[0]``) in-place,
        or add it if the conversation is empty.  Automatically
        snapshots on the first mutation."""
        self._begin_if_needed()
        if self.messages:
            self.messages[0] = {"role": "system", "content": content}
            # Line 0 changed in place — an append would leave the stale prompt.
            self._rewrite_needed = True
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

    async def commit(self) -> bool:
        """Persist the current messages and report whether it succeeded.

        The next mutation will automatically create a new snapshot.  A failed
        persistence attempt is recoverable by callers, but must not be treated
        as a committed turn by provenance-sensitive hooks.
        """
        persisted = await self.save()
        self._snapshot_messages = None
        self._snapshot_pending = None
        return persisted

    def rollback(self) -> None:
        """Restore messages and pending chunks to the most recent
        snapshot.  Idempotent — safe to call when no snapshot exists.
        Clears the snapshot so the next mutation starts fresh."""
        if self._snapshot_messages is not None:
            self.messages = self._snapshot_messages
            self._pending = self._snapshot_pending or []
            self._snapshot_messages = None
            self._snapshot_pending = None
            # Defensive, not currently reachable: a snapshot is only ever taken
            # at a commit boundary, so the restored state is what is already on
            # disk.  It is set anyway because the alternative — a rollback that
            # lands behind the file — silently loses user history, and this
            # rebuilds a whole conversation's state from one bool.
            self._rewrite_needed = True

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

    async def save(self) -> bool:
        """Persist ``messages`` to the JSONL file.  Errors are caught and
        logged — a failed save does not interrupt the session.

        Two paths.  When history has only grown since the last write, the new
        lines are **appended**; a commit then costs the new messages instead of
        the whole file (a 66MB session used to write 66MB per commit, and one
        turn commits several times).  When anything before the tail changed —
        ``replace_system`` / ``trim_after`` / ``truncate_to`` / ``rollback`` — or the file no
        longer looks the way we left it, the file is rewritten in full through
        a tempfile + ``replace``.

        Durability differs between the two, deliberately.  A rewrite is atomic:
        a crash leaves either the old file or the new one.  An append is not —
        a crash mid-write can leave a torn final line.  That is survivable
        because ``_load`` skips unparsable lines, and the torn line is always
        *last*, so at worst the crash costs the messages that same crash
        interrupted.  A torn file also disables the append baseline on reload,
        so the next save rewrites in full and heals the file.
        """
        if self._path is None:
            return True
        try:
            parent = anyio.Path(str(self._path.parent))
            if not await parent.is_dir():
                await parent.mkdir(parents=True)
            if await self._can_append():
                await self._append_new_lines()
            else:
                await self._rewrite_all()
            return True
        except Exception as e:
            logger.error(f"Failed to save history: {e}")
            return False

    async def _can_append(self) -> bool:
        """True when the file on disk is still the prefix we last wrote.

        Conservative by construction: every doubt answers "no" and falls back
        to a full rewrite.  Cheap on purpose — it must not re-serialise the
        history, which is the cost the append path exists to avoid.
        """
        if self._path is None or self._rewrite_needed or self._persisted_count == 0:
            return False
        if len(self.messages) < self._persisted_count:
            return False
        try:
            size = (await anyio.Path(str(self._path)).stat()).st_size
        except OSError:
            return False
        if size != self._persisted_bytes:
            logger.warning(
                f"History file {self._path} is {size} bytes, expected {self._persisted_bytes} "
                "— rewriting in full instead of appending"
            )
            return False
        return True

    async def _append_new_lines(self) -> None:
        """Append the messages added since the last write, and nothing else."""
        if self._path is None:
            return
        new = self.messages[self._persisted_count :]
        if not new:
            logger.debug(f"History unchanged since last save ({self._persisted_count} messages), nothing written")
            return
        payload = "".join(json.dumps(msg, ensure_ascii=False) + "\n" for msg in new).encode("utf-8")
        # Binary append: text mode would rewrite "\n" as os.linesep on Windows,
        # so the bytes written would not match the bytes counted.
        #
        # A failed append (disk full, I/O error) needs no flag of its own: it
        # either wrote nothing — leaving the file exactly as recorded, so the
        # retry appends safely — or wrote part of the payload, which changes the
        # size and makes ``_can_append`` fall back to a rewrite.
        async with await anyio.Path(str(self._path)).open("ab") as fh:
            await fh.write(payload)
        self._persisted_bytes += len(payload)
        self._persisted_count = len(self.messages)
        logger.debug(
            f"History appended to {self._path} (+{len(new)} messages, "
            f"{len(payload)} bytes, {self._persisted_count} total)"
        )

    async def _rewrite_all(self) -> None:
        """Rewrite the whole file atomically through a tempfile + replace."""
        if self._path is None:
            return
        payload = "".join(json.dumps(msg, ensure_ascii=False) + "\n" for msg in self.messages).encode("utf-8")
        tmp_path = self._path.with_suffix(".jsonl.tmp")
        await anyio.Path(str(tmp_path)).write_bytes(payload)
        await anyio.Path(str(tmp_path)).replace(str(self._path))
        self._persisted_bytes = len(payload)
        self._persisted_count = len(self.messages)
        self._rewrite_needed = False
        logger.debug(f"History rewritten to {self._path} ({len(self.messages)} messages, {len(payload)} bytes)")

    # -- internals -------------------------------------------------------------

    @staticmethod
    async def _load(path: Path) -> list[dict[str, Any]]:
        messages, _ = await Conversation._load_with_baseline(path)
        return messages

    @staticmethod
    async def _load_with_baseline(path: Path) -> tuple[list[dict[str, Any]], int | None]:
        """Load history, and report the byte size the file can be appended to.

        The second element is the on-disk size when the file is exactly the
        serialisation of the messages returned — meaning a later ``save`` may
        append to it — and ``None`` when it is not.  Anything unexpected
        (malformed line, torn tail, stray blank line, different formatting)
        yields ``None``, which forces the next save to rewrite the file and
        thereby heal it.
        """
        messages: list[dict[str, Any]] = []
        ap = anyio.Path(str(path))
        if not await ap.exists():
            logger.info(f"No history file found at {path}")
            return messages, None
        raw = await ap.read_bytes()
        # Read bytes (the byte-identity check below needs them) but decode
        # strictly, exactly as the previous ``read_text(encoding="utf-8")`` did.
        # ``errors="replace"`` would be a behaviour change on a data path: a
        # corrupt byte would turn into U+FFFD, the line would then fail to parse
        # and be skipped, and the following rewrite would make that loss
        # permanent.  Raising leaves the file untouched for a human to look at.
        content = raw.decode("utf-8")
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                messages.append(json.loads(stripped))
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed line {lineno} in {path}")
        logger.info(f"History loaded from {path} ({len(messages)} messages)")
        if not messages:
            return messages, None
        # One check decides it: re-serialise and compare byte for byte.  This
        # subsumes every specific defect — torn tail, malformed line, stray
        # blank line, CRLF, an older writer's formatting — so there is no
        # separate "was it clean" flag to keep in sync.  Compares in memory and
        # writes nothing; a mismatch means appending would produce a file this
        # code cannot account for, so it rewrites once and owns it afterwards.
        expected = "".join(json.dumps(msg, ensure_ascii=False) + "\n" for msg in messages).encode("utf-8")
        if expected != raw:
            logger.info(f"History file {path} is not byte-identical to its re-serialisation; will rewrite on next save")
            return messages, None
        return messages, len(raw)
