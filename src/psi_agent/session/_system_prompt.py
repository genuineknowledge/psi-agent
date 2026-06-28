from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from psi_agent.session._conversation import Conversation


class SystemPrompt:
    """Manages the system prompt lifecycle — lazy build and optional rebuild.

    *First call*: build the prompt and append it to the conversation.
    *Subsequent calls*: if a rebuild checker exists and returns True,
    replace the existing system message in-place.
    """

    def __init__(self, builder: Callable[..., Any] | None = None, checker: Callable[..., Any] | None = None):
        self._builder = builder
        self._checker = checker

    async def ensure(self, conversation: Conversation) -> None:
        """Build or rebuild the system prompt if needed."""
        if self._builder is None:
            return

        if not conversation.messages:
            await self._build(conversation)
        elif self._checker is not None:
            try:
                if await self._checker():
                    logger.info("Rebuild checker returned True — rebuilding system prompt")
                    assert self._builder is not None
                    conversation.replace_system(await self._builder())
            except Exception as e:
                logger.error(f"Rebuild check or rebuild failed: {e}")

    async def _build(self, conversation: Conversation) -> None:
        assert self._builder is not None
        try:
            sp = await self._builder()
            conversation.add({"role": "system", "content": sp})
            logger.info(f"System prompt loaded ({len(sp) if sp else 0} chars)")
        except Exception as e:
            logger.error(f"Failed to build system prompt: {e}")
