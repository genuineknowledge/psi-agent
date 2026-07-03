from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anyio
from loguru import logger


@dataclass
class GatewayState:
    _path: anyio.Path

    async def load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = await self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"State file {self._path!s} not found, starting fresh")
            return {"ais": {}, "sessions": {}, "titles": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"State file {self._path!s} is corrupt, starting fresh")
            return {"ais": {}, "sessions": {}, "titles": {}}
        if not isinstance(data, dict):
            logger.warning(f"State file {self._path!s} is not a dict, starting fresh")
            return {"ais": {}, "sessions": {}, "titles": {}}
        return {
            "ais": data.get("ais", {}),
            "sessions": data.get("sessions", {}),
            "titles": data.get("titles", {}),
        }

    async def save(
        self,
        ais: list[dict[str, str]],
        sessions: list[dict[str, str]],
        titles: dict[str, str],
    ) -> None:
        data = {
            "ais": {
                a["id"]: {
                    "provider": a["provider"],
                    "model": a["model"],
                    "api_key": a["api_key"],
                    "base_url": a["base_url"],
                }
                for a in ais
            },
            "sessions": {s["id"]: {"ai_id": s["ai_id"], "workspace": s["workspace"]} for s in sessions},
            "titles": dict(titles),
        }
        try:
            await self._path.parent.mkdir(parents=True, exist_ok=True)
            await self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug(f"State saved to {self._path!s}")
        except Exception as e:
            logger.warning(f"Failed to save state to {self._path!s}: {e!r}")
