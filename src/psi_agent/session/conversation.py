from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import anyio
from loguru import logger

from psi_agent.session.protocol import AgentChunk


class Conversation:
    """Owns the conversation history, its JSONL backing file, and schedule-
    produced chunks that should be flushed before the next user message.

    ``messages`` is public so that ``agent.run()`` can read it directly.
    ``session_id`` is the filename stem of the backing file — also reused
    as the per-session identifier for ``sys.modules`` isolation.
    """

    def __init__(self, *, messages: list[dict] | None = None, path: Path | None = None):
        self.messages: list[dict] = list(messages or [])
        self._path = path
        self._pending: list[AgentChunk] = []

    @property
    def session_id(self) -> str:
        """Identifier derived from the history file path stem."""
        return self._path.stem if self._path else ""

    # -- construction ----------------------------------------------------------

    @classmethod
    async def from_workspace(cls, workspace_path: Path, session_id: str | None = None) -> Conversation:
        """Create the histories directory, load an existing JSONL (if any),
        and return a ready-to-use Conversation."""
        if session_id is not None and not re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
            raise ValueError(f"Invalid session_id: {session_id!r} (only alphanumeric, dash, underscore allowed)")
        session_id = session_id or uuid.uuid4().hex
        logger.info(f"Starting session: {session_id}")

        histories_dir = anyio.Path(str(workspace_path / "histories"))
        dir_created = False
        if not await histories_dir.is_dir():
            await histories_dir.mkdir(parents=True)
            logger.info(f"Created histories directory: {histories_dir}")
            dir_created = True
        if dir_created:
            await (histories_dir / ".gitignore").write_text("*\n")
            logger.debug(f"Created .gitignore in {histories_dir}")

        path = workspace_path / "histories" / f"{session_id}.jsonl"
        messages = await cls._load(path)
        return cls(messages=messages, path=path)

    # -- mutation --------------------------------------------------------------

    def add(self, msg: dict) -> None:
        """Append a message to history."""
        self.messages.append(msg)

    def replace_system(self, content: str) -> None:
        """Replace the system message (``messages[0]``) in-place."""
        if self.messages:
            self.messages[0] = {"role": "system", "content": content}

    def stash(self, chunks: list[AgentChunk]) -> None:
        """Store schedule-produced chunks for the next channel request."""
        self._pending = chunks

    def flush_pending(self) -> list[AgentChunk]:
        """Pop and return pending schedule chunks, clearing the buffer."""
        chunks = self._pending
        self._pending = []
        return chunks

    # -- persistence -----------------------------------------------------------

    async def save(self) -> None:
        """Overwrite the JSONL file with the current ``messages``.  Errors
        are caught and logged — a failed save does not interrupt the session."""
        if self._path is None:
            return
        try:
            content = "\n".join(json.dumps(msg, ensure_ascii=False) for msg in self.messages) + "\n"
            await anyio.Path(str(self._path)).write_text(content)
            logger.debug(f"History saved to {self._path} ({len(self.messages)} messages)")
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    # -- internals -------------------------------------------------------------

    @staticmethod
    async def _load(path: Path) -> list[dict]:
        messages: list[dict] = []
        ap = anyio.Path(str(path))
        if not await ap.exists():
            logger.info(f"No history file found at {path}")
            return messages
        content = await ap.read_text()
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
