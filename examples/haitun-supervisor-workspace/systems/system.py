"""Stable system prompt for the isolated Haitun supervisor."""

from __future__ import annotations

import inspect
from typing import Any

import anyio


async def system_prompt_builder(_user_message: dict[str, Any] | None = None) -> str:
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    return await (current_file.parent.parent / "SOUL.md").read_text(encoding="utf-8")


async def system_prompt_rebuild_checker(_user_message: dict[str, Any] | None = None) -> bool:
    return False
