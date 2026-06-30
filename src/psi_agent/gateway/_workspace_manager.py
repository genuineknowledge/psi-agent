from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio


class WorkspaceManager:
    def get_cwd(self) -> str:
        """Return the current working directory."""
        return str(Path.cwd())

    async def browse(self, path: str) -> dict[str, Any]:
        entries: list[dict[str, str]] = []
        dir_path = anyio.Path(path)
        async for entry in dir_path.iterdir():
            name = entry.name
            if not name.startswith(".") and await entry.is_dir():
                entries.append({"name": name, "path": str(await entry.resolve())})
        return {"entries": sorted(entries, key=lambda e: e["name"])}
