"""Gateway plumbing for ``max_context_tokens`` (the compaction threshold).

Before this existed the Gateway constructed ``Ai`` without the field, so the
threshold was stuck at whatever ``Ai.run()`` resolved (env var, else 100K) and
could not be configured per AI backend.
"""

from __future__ import annotations

import anyio
import pytest
from anyio.abc import TaskGroup

from psi_agent.runtime._ai_manager import AIManager


async def _close(tg: TaskGroup) -> None:
    tg.cancel_scope.cancel()
    await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_defaults_to_sentinel_preserving_ai_resolution() -> None:
    """Omitting it must keep ``Ai``'s own env/100K resolution, not force a value."""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(
            provider="openai", model="gpt-4o", api_key="sk-test", base_url="https://api.example.com"
        )
        assert info.max_context_tokens == -1
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_explicit_value_is_recorded() -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(
            provider="openai",
            model="gpt-4o",
            api_key="sk-test",
            base_url="https://api.example.com",
            max_context_tokens=150_000,
        )
        assert info.max_context_tokens == 150_000

        listed = await mgr.list_all()
        assert listed[0].max_context_tokens == 150_000
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_create_without_id_reuses_identical_config() -> None:
    """Same provider/model/key/base (no explicit id) must not spawn a second AI."""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-dedupe", _tg=tg)
        first = await mgr.create(
            provider="openai",
            model="deepseek-v4-flash",
            api_key="haitun-default",
            base_url="https://misakamikoto.genuineknowledge.cn/",
        )
        second = await mgr.create(
            provider="openai",
            model="deepseek-v4-flash",
            api_key="haitun-default",
            base_url="https://misakamikoto.genuineknowledge.cn",
        )
        assert second.id == first.id
        assert len(await mgr.list_all()) == 1

        # Explicit id still creates a parallel instance (Session revive).
        other = await mgr.create(
            provider="openai",
            model="deepseek-v4-flash",
            api_key="haitun-default",
            base_url="https://misakamikoto.genuineknowledge.cn",
            id="revived-session-ai",
        )
        assert other.id == "revived-session-ai"
        assert len(await mgr.list_all()) == 2
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_zero_is_preserved_as_disable() -> None:
    """0 disables compaction and must survive as 0, not collapse into the default."""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(
            provider="openai",
            model="gpt-4o",
            api_key="sk-test",
            base_url="https://api.example.com",
            max_context_tokens=0,
        )
        assert info.max_context_tokens == 0
    finally:
        await _close(tg)
