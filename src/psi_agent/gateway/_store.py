"""Persistent SQLite store for Gateway AI/Session/Title state."""

from __future__ import annotations

import sqlite3

import anyio
from loguru import logger


class GatewayStore:
    """SQLite-backed persistence for Gateway runtime state.

    Wraps stdlib ``sqlite3`` via ``anyio.to_thread.run_sync`` — no extra
    dependencies.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init(self) -> None:
        """Create tables and parent directories if needed."""
        await anyio.Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        await self._execute(
            """\
            CREATE TABLE IF NOT EXISTS ais (
                id       TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model    TEXT NOT NULL,
                api_key  TEXT NOT NULL,
                base_url TEXT NOT NULL
            )
            """
        )
        await self._execute(
            """\
            CREATE TABLE IF NOT EXISTS sessions (
                id        TEXT PRIMARY KEY,
                ai_id     TEXT NOT NULL,
                workspace TEXT NOT NULL
            )
            """
        )
        await self._execute(
            """\
            CREATE TABLE IF NOT EXISTS titles (
                session_id TEXT PRIMARY KEY,
                title      TEXT NOT NULL
            )
            """
        )
        logger.info(f"Gateway store initialized at {self._db_path!r}")

    async def _execute(self, sql: str, params: tuple = ()) -> None:
        def _run() -> None:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(sql, params)
                conn.commit()

        await anyio.to_thread.run_sync(_run)  # ty: ignore

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, object]]:
        def _run() -> list[dict[str, object]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(sql, params).fetchall()]

        return await anyio.to_thread.run_sync(_run)  # ty: ignore

    # ── AI ──────────────────────────────────────────────────────────

    async def list_ais(self) -> list[dict[str, object]]:
        return await self._fetchall("SELECT * FROM ais")

    async def save_ai(self, id: str, provider: str, model: str, api_key: str, base_url: str) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO ais (id, provider, model, api_key, base_url) VALUES (?, ?, ?, ?, ?)",
            (id, provider, model, api_key, base_url),
        )

    async def delete_ai(self, id: str) -> None:
        await self._execute("DELETE FROM ais WHERE id = ?", (id,))
        await self._execute("DELETE FROM sessions WHERE ai_id = ?", (id,))

    # ── Session ─────────────────────────────────────────────────────

    async def list_sessions(self) -> list[dict[str, object]]:
        return await self._fetchall("SELECT * FROM sessions")

    async def save_session(self, id: str, ai_id: str, workspace: str) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO sessions (id, ai_id, workspace) VALUES (?, ?, ?)",
            (id, ai_id, workspace),
        )

    async def delete_session(self, id: str) -> None:
        await self._execute("DELETE FROM sessions WHERE id = ?", (id,))
        await self._execute("DELETE FROM titles WHERE session_id = ?", (id,))

    # ── Title ───────────────────────────────────────────────────────

    async def list_titles(self) -> dict[str, str]:
        rows = await self._fetchall("SELECT * FROM titles")
        return {str(row["session_id"]): str(row["title"]) for row in rows}

    async def save_title(self, session_id: str, title: str) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO titles (session_id, title) VALUES (?, ?)",
            (session_id, title),
        )

    async def delete_title(self, session_id: str) -> None:
        await self._execute("DELETE FROM titles WHERE session_id = ?", (session_id,))
