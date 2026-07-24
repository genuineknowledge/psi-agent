"""Image understanding helper — MiniMax-M3 vision API (BYOK)."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import _minimax as _mx
import _multimodal_env as _env
import aiohttp
import anyio
from loguru import logger

_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_DEFAULT_QUESTION = "Describe this image in detail."
_BACKEND = _mx.PROVIDER_NAME


@dataclass(frozen=True)
class VisionResult:
    ok: bool
    text: str
    backend: str
    message: str
    image_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "text": self.text,
            "backend": self.backend,
            "message": self.message,
            "image_path": self.image_path,
        }


def resolve_workspace(raw: str) -> anyio.Path:
    """User workspace — prefer shared resolver (ContextVar-aware)."""
    from _background_process_registry import resolve_workspace as _shared

    return _shared(raw)


def _normalize_question(question: str) -> str:
    q = question.strip()
    return q if q else _DEFAULT_QUESTION


def _suffix_allowed(path: anyio.Path) -> bool:
    return path.suffix.lower() in _ALLOWED_SUFFIXES


async def _call_vision_api(
    *,
    image_path: str,
    question: str,
    cfg: _env.VisionApiConfig,
) -> VisionResult:
    path = anyio.Path(image_path)
    raw = await path.read_bytes()
    mime, _ = mimetypes.guess_type(image_path)
    mime_type = mime or "application/octet-stream"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"

    payload = {
        "model": cfg.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "default"},
                    },
                    {"type": "text", "text": question},
                ],
            }
        ],
        "max_tokens": 1024,
    }
    url = _mx.chat_completions_url(cfg.api_host)
    headers = _mx.auth_headers(cfg.api_key)
    logger.debug(f"MiniMax vision POST {url} model={cfg.model!r}")

    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, headers=headers, json=payload) as resp,
        ):
            body = await resp.text()
            if resp.status >= 400:
                logger.error(f"MiniMax vision HTTP {resp.status}: {body[:500]}")
                return VisionResult(
                    ok=False,
                    text="",
                    backend=_BACKEND,
                    message=f"MiniMax vision API HTTP {resp.status}: {body[:300]}",
                    image_path=image_path,
                )
            data = json.loads(body)
    except json.JSONDecodeError as e:
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message=f"MiniMax vision API invalid JSON: {e}",
            image_path=image_path,
        )
    except Exception as e:
        logger.error(f"MiniMax vision API request failed: {e!r}")
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message=f"MiniMax vision API request failed: {e!r}",
            image_path=image_path,
        )

    if not isinstance(data, dict):
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message="MiniMax vision API response is not a JSON object",
            image_path=image_path,
        )

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message="MiniMax vision API response missing choices",
            image_path=image_path,
        )
    first = choices[0]
    if not isinstance(first, dict):
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message="MiniMax vision API choice shape invalid",
            image_path=image_path,
        )
    message = first.get("message")
    if not isinstance(message, dict):
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message="MiniMax vision API message shape invalid",
            image_path=image_path,
        )
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message="MiniMax vision API returned empty content",
            image_path=image_path,
        )

    text = _mx.strip_thinking(content)
    if not text:
        return VisionResult(
            ok=False,
            text="",
            backend=_BACKEND,
            message="MiniMax vision API returned only thinking content",
            image_path=image_path,
        )

    logger.info(f"MiniMax vision ok: {len(text)} chars for {image_path}")
    return VisionResult(
        ok=True,
        text=text,
        backend=_BACKEND,
        message="OK",
        image_path=image_path,
    )


async def describe_image_impl(
    image_path: str,
    question: str = "",
    workspace_raw: str = "",
) -> VisionResult:
    ws = resolve_workspace(workspace_raw)
    resolved_ws = str(await ws.resolve())
    _env.apply_workspace_env_file(resolved_ws)
    path = anyio.Path(image_path.strip())
    if not image_path.strip():
        return VisionResult(
            ok=False,
            text="",
            backend="",
            message="image_path must be non-empty",
            image_path="",
        )
    if not await path.exists():
        return VisionResult(
            ok=False,
            text="",
            backend="",
            message=f"image not found: {image_path}",
            image_path=image_path,
        )
    if not await path.is_file():
        return VisionResult(
            ok=False,
            text="",
            backend="",
            message=f"not a file: {image_path}",
            image_path=image_path,
        )
    if not _suffix_allowed(path):
        return VisionResult(
            ok=False,
            text="",
            backend="",
            message=f"unsupported image type (allowed: {', '.join(sorted(_ALLOWED_SUFFIXES))})",
            image_path=image_path,
        )

    stat = await path.stat()
    if stat.st_size > _MAX_IMAGE_BYTES:
        return VisionResult(
            ok=False,
            text="",
            backend="",
            message=f"image too large ({stat.st_size} bytes; max {_MAX_IMAGE_BYTES})",
            image_path=image_path,
        )

    resolved = str(await path.resolve())
    q = _normalize_question(question)
    mime, _ = mimetypes.guess_type(resolved)
    logger.debug(f"describe_image: path={resolved} mime={mime} size={stat.st_size}")

    api_cfg = _env.read_vision_api_config()
    if not api_cfg.ready:
        return VisionResult(
            ok=False,
            text="",
            backend="",
            message=api_cfg.not_ready_message(),
            image_path=resolved,
        )

    return await _call_vision_api(image_path=resolved, question=q, cfg=api_cfg)


def dumps_result(result: VisionResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)
