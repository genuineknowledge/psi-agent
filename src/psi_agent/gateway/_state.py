from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent._app_paths import state_dir as app_state_dir


class GatewayState:
    """Persist AI / Session / Title registry under AppData ``state/``."""

    def __init__(
        self,
        *,
        app_data_root: str | None = None,
        state_root: Path | str | None = None,
    ) -> None:
        """*state_root* overrides the directory that holds ``latest.json`` (tests).

        Otherwise uses ``platformdirs`` AppData ``state/`` (or ``app_data_root``).
        """
        if state_root is not None:
            root = Path(state_root)
        else:
            root = app_state_dir(override=app_data_root)
        self._path = anyio.Path(str(root / "latest.json"))
        self._history_dir = anyio.Path(str(root))
        self._startup_ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    async def load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = await self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            legacy = anyio.Path("state/latest.json")
            try:
                raw = await legacy.read_text(encoding="utf-8")
                logger.info(f"Loaded legacy state from {legacy}; will rewrite to {self._path}")
            except FileNotFoundError:
                logger.debug(f"State file {self._path} not found, starting fresh")
                return {"ais": [], "sessions": [], "titles": []}
            except OSError as e:
                logger.warning(f"Failed to read legacy state: {e!r}")
                return {"ais": [], "sessions": [], "titles": []}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"State file {self._path} is corrupt, starting fresh")
            return {"ais": [], "sessions": [], "titles": []}
        if not isinstance(data, dict):
            logger.warning(f"State file {self._path} is not a dict, starting fresh")
            return {"ais": [], "sessions": [], "titles": []}
        return {
            "ais": data.get("ais", []),
            "sessions": data.get("sessions", []),
            "titles": data.get("titles", []),
        }

    async def save(
        self,
        ais: list[dict[str, str]],
        sessions: list[dict[str, str]],
        titles: list[dict[str, str]],
    ) -> None:
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
            "sessions": [
                {
                    "id": s["id"],
                    "ai_id": s["ai_id"],
                    "workspace": s["workspace"],
                    **({"agent": s["agent"]} if s.get("agent") else {}),
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
                await history_path.write_text(json_str, encoding="utf-8")
                logger.debug(f"State saved to {history_path}")
            except Exception as e:
                logger.warning(f"Failed to save history to {history_path}: {e!r}")
