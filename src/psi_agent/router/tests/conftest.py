from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """aiohttp requires the asyncio backend even though production code uses anyio."""

    return "asyncio"
