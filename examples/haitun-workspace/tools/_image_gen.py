"""Image generation helper — MiniMax image-01 API (BYOK)."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import _minimax as _mx
import _multimodal_env as _env
import aiohttp
import anyio
from loguru import logger

DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512
_BACKEND = _mx.PROVIDER_NAME


@dataclass(frozen=True)
class ImageGenResult:
    ok: bool
    path: str
    mode: str
    seed: int
    width: int
    height: int
    backend: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": self.path,
            "mode": self.mode,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "backend": self.backend,
            "message": self.message,
        }


def resolve_workspace(raw: str) -> anyio.Path:
    if raw.strip():
        return anyio.Path(raw.strip())
    env = os.environ.get("WORKSPACE_DIR", "").strip()
    if env:
        return anyio.Path(env)
    return anyio.Path(str(Path(__file__).resolve().parents[1]))


def _parse_reference_images(raw: str) -> tuple[list[str] | None, str | None]:
    if not raw.strip():
        return [], None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"reference_images is not valid JSON: {e}"
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None, "reference_images must be a JSON array of path strings"
    return parsed, None


def _resolve_dimensions(width: int, height: int) -> tuple[int, int]:
    return _mx.snap_dimension(width), _mx.snap_dimension(height)


def _resolve_seed(seed: int) -> int:
    return seed if seed >= 0 else random.randint(0, 2**31 - 1)


def _infer_mode(reference_images: list[str]) -> str:
    return "image_to_image" if reference_images else "text_to_image"


def _failure(
    *,
    message: str,
    mode: str = "",
    seed: int = -1,
    width: int = 0,
    height: int = 0,
    backend: str = "",
) -> ImageGenResult:
    return ImageGenResult(
        ok=False,
        path="",
        mode=mode,
        seed=seed,
        width=width,
        height=height,
        backend=backend,
        message=message,
    )


async def _resolve_output_path(workspace: anyio.Path, output_path: str) -> anyio.Path:
    if output_path.strip():
        return anyio.Path(output_path.strip())
    out_dir = workspace / "generated" / "images"
    if not await out_dir.exists():
        await out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"gen-{uuid.uuid4().hex[:12]}.png"


async def _encode_reference_image(path_str: str) -> tuple[str | None, str | None]:
    path = anyio.Path(path_str.strip())
    if not await path.exists():
        return None, f"reference image not found: {path_str}"
    if not await path.is_file():
        return None, f"reference image is not a file: {path_str}"
    raw = await path.read_bytes()
    mime, _ = mimetypes.guess_type(path_str)
    mime_type = mime or "application/octet-stream"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"
    return data_url, None


def _build_minimax_payload(
    *,
    cfg: _env.ImageGenApiConfig,
    desc: str,
    width: int,
    height: int,
    seed: int,
    subject_reference: list[dict[str, str]] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": cfg.model,
        "prompt": desc,
        "width": width,
        "height": height,
        "aspect_ratio": _mx.nearest_aspect_ratio(width, height),
        "response_format": "base64",
        "n": 1,
        "seed": seed,
    }
    if subject_reference:
        payload["subject_reference"] = subject_reference
    return payload


async def _call_image_gen_api(
    *,
    path_str: str,
    desc: str,
    mode: str,
    seed: int,
    width: int,
    height: int,
    refs: list[str],
    cfg: _env.ImageGenApiConfig,
) -> ImageGenResult:
    subject_reference: list[dict[str, str]] | None = None
    if refs:
        if len(refs) > 1:
            return _failure(
                message="MiniMax image-to-image supports only one reference image per request",
                mode=mode,
                seed=seed,
                width=width,
                height=height,
                backend=_BACKEND,
            )
        ref_data, ref_err = await _encode_reference_image(refs[0])
        if ref_err is not None or ref_data is None:
            return _failure(
                message=ref_err or "invalid reference image",
                mode=mode,
                seed=seed,
                width=width,
                height=height,
                backend=_BACKEND,
            )
        subject_reference = [{"type": "character", "image_file": ref_data}]

    payload = _build_minimax_payload(
        cfg=cfg,
        desc=desc,
        width=width,
        height=height,
        seed=seed,
        subject_reference=subject_reference,
    )
    url = _mx.image_generation_url(cfg.api_host)
    headers = _mx.auth_headers(cfg.api_key)
    logger.debug(f"MiniMax image POST {url} model={cfg.model!r} mode={mode} size={width}x{height}")

    try:
        timeout = aiohttp.ClientTimeout(total=180)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, headers=headers, json=payload) as resp,
        ):
            body = await resp.text()
            if resp.status >= 400:
                logger.error(f"MiniMax image HTTP {resp.status}: {body[:500]}")
                return _failure(
                    message=f"MiniMax image API HTTP {resp.status}: {body[:300]}",
                    mode=mode,
                    seed=seed,
                    width=width,
                    height=height,
                    backend=_BACKEND,
                )
            data = json.loads(body)
    except json.JSONDecodeError as e:
        return _failure(
            message=f"MiniMax image API invalid JSON: {e}",
            mode=mode,
            seed=seed,
            width=width,
            height=height,
            backend=_BACKEND,
        )
    except Exception as e:
        logger.error(f"MiniMax image API request failed: {e!r}")
        return _failure(
            message=f"MiniMax image API request failed: {e!r}",
            mode=mode,
            seed=seed,
            width=width,
            height=height,
            backend=_BACKEND,
        )

    if not isinstance(data, dict):
        return _failure(
            message="MiniMax image API response is not a JSON object",
            mode=mode,
            seed=seed,
            width=width,
            height=height,
            backend=_BACKEND,
        )

    raw, err = _mx.extract_image_bytes(data)
    if err is not None or raw is None:
        return _failure(
            message=err or "MiniMax image API returned no image",
            mode=mode,
            seed=seed,
            width=width,
            height=height,
            backend=_BACKEND,
        )

    await anyio.Path(path_str).write_bytes(raw)
    logger.info(f"MiniMax image ok: {path_str} ({width}x{height}, seed={seed}, mode={mode})")
    return ImageGenResult(
        ok=True,
        path=path_str,
        mode=mode,
        seed=seed,
        width=width,
        height=height,
        backend=_BACKEND,
        message="OK",
    )


async def generate_image_impl(
    description: str,
    reference_images_raw: str = "",
    output_path: str = "",
    width: int = 0,
    height: int = 0,
    seed: int = -1,
    workspace_raw: str = "",
) -> ImageGenResult:
    desc = description.strip()
    if not desc:
        return _failure(message="description must be non-empty")

    refs, ref_err = _parse_reference_images(reference_images_raw)
    if ref_err is not None or refs is None:
        return _failure(message=ref_err or "invalid reference_images")

    w, h = _resolve_dimensions(width, height)
    resolved_seed = _resolve_seed(seed)
    mode = _infer_mode(refs)

    workspace = resolve_workspace(workspace_raw)
    resolved_ws = str(await workspace.resolve())
    _env.apply_workspace_env_file(resolved_ws)
    out = await _resolve_output_path(workspace, output_path)
    parent = out.parent
    if not await parent.exists():
        await parent.mkdir(parents=True, exist_ok=True)

    api_cfg = _env.read_image_gen_api_config()
    if not api_cfg.ready:
        return _failure(
            message=api_cfg.not_ready_message(),
            mode=mode,
            seed=resolved_seed,
            width=w,
            height=h,
        )

    path_str = str(await out.resolve())
    return await _call_image_gen_api(
        path_str=path_str,
        desc=desc,
        mode=mode,
        seed=resolved_seed,
        width=w,
        height=h,
        refs=refs,
        cfg=api_cfg,
    )


def dumps_result(result: ImageGenResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)
