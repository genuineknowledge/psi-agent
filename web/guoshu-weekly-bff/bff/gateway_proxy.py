"""Forward requests to the psi-agent Gateway, keeping SSE lines streaming.

``trust_env=False`` is load-bearing: a machine-level HTTP proxy would
otherwise hijack loopback traffic to the Gateway and answer 502 (hit once
already during B1 on a proxied dev box).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from .config import BffConfig


class GatewayProxy:
    def __init__(self, config: BffConfig) -> None:
        self._base = config.gateway_base_url
        self._shared_secret = config.gateway_shared_secret
        self._client = httpx.AsyncClient(
            base_url=self._base,
            trust_env=False,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def _headers(self) -> dict[str, str]:
        if self._shared_secret:
            return {"X-Gateway-Secret": self._shared_secret}
        return {}

    async def get(self, path: str, **kwargs: object) -> httpx.Response:
        return await self._client.get(path, headers=self._headers(), **kwargs)

    async def post_json(self, path: str, body: dict[str, object]) -> httpx.Response:
        return await self._client.post(path, headers=self._headers(), json=body)

    async def close(self) -> None:
        await self._client.aclose()

    async def stream_chat(self, session_id: str, body: dict[str, object]) -> AsyncIterator[bytes]:
        """POST /sessions/{id}/chat and yield raw SSE lines as they arrive.

        No buffering, no parsing: every line is forwarded the moment httpx
        hands it over — that is what keeps first-token latency visible
        (plan 6.2 / 7.1).
        """
        async with self._client.stream(
            "POST",
            f"/sessions/{session_id}/chat",
            headers=self._headers(),
            json=body,
            timeout=httpx.Timeout(300.0, connect=10.0),
        ) as response:
            async for line in response.aiter_lines():
                if line:
                    yield line.encode("utf-8")
