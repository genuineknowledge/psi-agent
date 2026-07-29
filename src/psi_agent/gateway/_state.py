from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anyio
from loguru import logger

from psi_agent._appdata import (
    appdata_state_dir,
    appdata_state_latest_path,
    legacy_state_latest_path,
    resolve_appdata_root,
)

_EMPTY_KEYS = ("ais", "routers", "sessions", "titles")


def _empty_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in _EMPTY_KEYS}


@dataclass
class GatewayState:
    """Persist AI / Session / Title snapshots under AppData ``state/`` (Step 4D).

    *Writes* always go to ``{appdata}/state/latest.json`` (+ timestamped snapshot).
    *Loads* prefer that file; if missing, dual-read legacy cwd ``state/latest.json``.
    """

    _path: anyio.Path = field(default_factory=lambda: anyio.Path("state/latest.json"))
    _history_dir: anyio.Path = field(default_factory=lambda: anyio.Path("state"))
    _legacy_path: anyio.Path = field(default_factory=legacy_state_latest_path)
    _startup_ts: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S"))

    @classmethod
    async def from_appdata(cls, appdata_root: str = "") -> GatewayState:
        """Build a state store rooted at *appdata_root* (empty → resolve)."""
        root = appdata_root.strip() or await resolve_appdata_root()
        history_dir = appdata_state_dir(root)
        return cls(
            _path=appdata_state_latest_path(root),
            _history_dir=history_dir,
            _legacy_path=legacy_state_latest_path(),
        )

    async def load(self) -> dict[str, list[dict[str, Any]]]:
        read_path = self._path
        if not await self._path.is_file():
            if await self._legacy_path.is_file():
                read_path = self._legacy_path
                logger.info(f"Loaded Gateway state from legacy path {read_path} (will save to {self._path})")
            else:
                logger.debug(f"State file {self._path} not found, starting fresh")
                return _empty_snapshot()
        try:
            raw = await read_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"State file {read_path} not found, starting fresh")
            return _empty_snapshot()
        except OSError as e:
            logger.warning(f"Failed to read state {read_path}: {e!r}")
            return _empty_snapshot()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"State file {read_path} is corrupt, starting fresh")
            return _empty_snapshot()
        if not isinstance(data, dict):
            logger.warning(f"State file {read_path} is not a dict, starting fresh")
            return _empty_snapshot()
        if read_path == self._path:
            logger.debug(f"Loaded Gateway state from {read_path}")
        return {
            "ais": data.get("ais", []),
            "routers": data.get("routers", []),
            "sessions": data.get("sessions", []),
            "titles": data.get("titles", []),
        }

    async def save(
        self,
        ais: list[dict[str, str]],
        sessions: list[dict[str, str]],
        titles: list[dict[str, str]],
        routers: list[dict[str, Any]] | None = None,
    ) -> None:
        routers = routers or []
        data = {
            "ais": [
                {
                    "id": a["id"],
                    "provider": a["provider"],
                    "model": a["model"],
                    "api_key": a["api_key"],
                    "base_url": a["base_url"],
                }
                for a in ais
            ],
            "routers": routers,
            "sessions": [
                {
                    "id": s["id"],
                    "backend_type": s.get("backend_type", "ai"),
                    "backend_id": s.get("backend_id", s.get("ai_id", "")),
                    "workspace": s["workspace"],
                }
                for s in sessions
            ],
            "titles": [{"id": t["id"], "title": t["title"]} for t in titles],
        }
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            await self._path.parent.mkdir(parents=True, exist_ok=True)
            await self._path.write_text(json_str, encoding="utf-8")
            logger.debug(f"State saved to {self._path}")
        except Exception as e:
            logger.warning(f"Failed to save state to {self._path}: {e!r}")
        if self._startup_ts:
            history_path = self._history_dir / f"{self._startup_ts}.json"
            try:
                await self._history_dir.mkdir(parents=True, exist_ok=True)
                await history_path.write_text(json_str, encoding="utf-8")
                logger.debug(f"State saved to {history_path}")
            except Exception as e:
                logger.warning(f"Failed to save history to {history_path}: {e!r}")
