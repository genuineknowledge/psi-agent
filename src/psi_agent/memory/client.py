"""Async HTTP client for the external Fusion Memory service."""

from __future__ import annotations

from typing import Any

import aiohttp


class FusionMemoryError(RuntimeError):
    """Raised when Fusion Memory returns an error or cannot be reached."""


def friendly_memory_error(_exc: BaseException) -> str:
    return (
        "Fusion Memory is not available. Continue without memory, "
        "then run fusion-memory doctor."
    )


class FusionMemoryClient:
    """Small async client for the Fusion Memory HTTP wrapper."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 1.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> FusionMemoryClient:
        await self._ensure_session()
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def add(
        self,
        input_data: Any,
        scope: dict[str, Any],
        *,
        session_time: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input": input_data,
            "scope": scope,
            "metadata": metadata or {},
        }
        if session_time is not None:
            payload["session_time"] = session_time
        return await self._request("POST", "/add", payload)

    async def search(
        self,
        query: str,
        scope: dict[str, Any],
        *,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/search",
            {"query": query, "scope": scope, "options": options or {}},
        )

    async def answer_context(
        self,
        query: str,
        scope: dict[str, Any],
        *,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/answer-context",
            {"query": query, "scope": scope, "budget": budget or {}},
        )

    async def clear(
        self,
        scope: dict[str, Any],
        *,
        allow_cross_session: bool = False,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/clear",
            {"scope": scope, "allow_cross_session": allow_cross_session},
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        try:
            async with session.request(method, url, json=payload) as response:
                if response.status >= 400:
                    raise FusionMemoryError(f"Fusion Memory request failed with HTTP {response.status}.")
                try:
                    data = await response.json(content_type=None)
                except Exception as exc:
                    raise FusionMemoryError("Fusion Memory returned an invalid response.") from exc
                if not isinstance(data, dict):
                    raise FusionMemoryError("Fusion Memory returned an invalid response.")
                return data
        except FusionMemoryError:
            raise
        except TimeoutError as exc:
            raise FusionMemoryError("Fusion Memory request timed out.") from exc
        except aiohttp.ClientConnectionError as exc:
            raise FusionMemoryError("Fusion Memory connection failed.") from exc
        except aiohttp.ClientError as exc:
            raise FusionMemoryError("Fusion Memory request failed.") from exc
