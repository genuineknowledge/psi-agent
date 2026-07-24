"""MiniMax multimodal API credential readers (BYOK, separate from Gateway primary AI).

MiniMax uses **one** pay-as-you-go API key for all modalities. Per-feature env
(``VISION_*`` / ``IMAGE_GEN_*``) only overrides model or host when needed.

Required:
  - ``MINIMAX_API_KEY`` (or per-feature ``*_API_KEY`` alias)

Optional (non-model):
  - ``MINIMAX_API_HOST`` — default ``https://api.minimaxi.com`` (CN)
  - ``MINIMAX_GROUP_ID`` — legacy speech/old APIs only; unused by vision/image

Models (defaults applied when unset):
  - vision → ``MiniMax-M3`` via ``/v1/chat/completions``
  - image gen → ``image-01`` via ``/v1/image_generation``

Configure via workspace ``.env.multimodal`` (auto-loaded when tools run).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import _minimax as _mx

_PLACEHOLDER_HINTS = (
    "your_",
    "changeme",
    "change-me",
    "todo",
    "placeholder",
    "<",
    "xxx",
    "fill_me",
    "replace_me",
    "example.com",
)


def _is_placeholder_value(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    lower = v.lower()
    return any(hint in lower for hint in _PLACEHOLDER_HINTS)


_STALE_ENV_KEYS = (
    "VISION_BASE_URL",
    "VISION_API_KEY",
    "IMAGE_GEN_BASE_URL",
    "IMAGE_GEN_API_KEY",
    "USTC_API_BASE_URL",
    "USTC_API_KEY",
)

_WORKSPACE_ENV_LOADED: set[str] = set()


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    idx = stripped.find("=")
    if idx < 1:
        return None
    return stripped[:idx].strip(), stripped[idx + 1 :].strip()


def apply_workspace_env_file(workspace: str) -> None:
    """Load ``{user_workspace}/.env.multimodal`` into ``os.environ`` once per workspace."""
    from _background_process_registry import resolve_workspace

    ws = str(resolve_workspace(workspace))
    resolved = str(Path(ws).resolve())
    if resolved in _WORKSPACE_ENV_LOADED:
        return
    _WORKSPACE_ENV_LOADED.add(resolved)
    env_file = Path(resolved) / ".env.multimodal"
    if not env_file.is_file():
        return
    names_in_file: set[str] = set()
    for line in env_file.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        name, value = parsed
        if not name:
            continue
        names_in_file.add(name)
        existing = os.environ.get(name, "").strip()
        if existing and not _is_placeholder_value(existing):
            continue
        os.environ[name] = value
    for stale in _STALE_ENV_KEYS:
        if stale not in names_in_file:
            os.environ.pop(stale, None)


def _resolve_api_key(*names: str) -> str:
    """Prefer ``MINIMAX_API_KEY`` when set (MiniMax-only deployment)."""
    minimax = os.environ.get("MINIMAX_API_KEY", "").strip()
    if minimax and not _is_placeholder_value(minimax):
        return minimax
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not _is_placeholder_value(value):
            return value
    return ""


def _resolve_api_host(*names: str) -> str:
    """Prefer ``MINIMAX_API_HOST`` when set (MiniMax-only deployment)."""
    minimax_host = os.environ.get("MINIMAX_API_HOST", "").strip()
    if minimax_host and not _is_placeholder_value(minimax_host):
        return _mx.normalize_api_host(minimax_host)
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not _is_placeholder_value(value):
            return _mx.normalize_api_host(value)
    return _mx.DEFAULT_API_HOST


@dataclass(frozen=True)
class ImageGenApiConfig:
    """MiniMax image-01 credentials and endpoint host."""

    api_key: str
    api_host: str
    model: str
    group_id: str
    provider: str

    @property
    def request_url(self) -> str:
        return _mx.image_generation_url(self.api_host)

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def not_ready_message(self) -> str:
        return "MiniMax image-generation API is not configured. Set MINIMAX_API_KEY in workspace `.env.multimodal`."


@dataclass(frozen=True)
class VisionApiConfig:
    """MiniMax-M3 vision credentials and endpoint host."""

    api_key: str
    api_host: str
    model: str
    group_id: str
    provider: str

    @property
    def request_url(self) -> str:
        return _mx.chat_completions_url(self.api_host)

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def not_ready_message(self) -> str:
        return "MiniMax vision API is not configured. Set MINIMAX_API_KEY in workspace `.env.multimodal`."


def _first_non_empty_or_default(*values: str, default: str) -> str:
    picked = _first_non_empty(*values)
    return picked if picked else default


def read_image_gen_api_config() -> ImageGenApiConfig:
    return ImageGenApiConfig(
        api_key=_resolve_api_key("IMAGE_GEN_API_KEY", "MINIMAX_API_KEY"),
        api_host=_resolve_api_host(
            "IMAGE_GEN_BASE_URL",
            "IMAGE_GEN_API_HOST",
            "MINIMAX_API_HOST",
            "MINIMAX_BASE_URL",
        ),
        model=_first_non_empty_or_default(
            os.environ.get("IMAGE_GEN_MODEL", ""),
            os.environ.get("IMAGE_GEN_API_MODEL", ""),
            default=_mx.DEFAULT_IMAGE_MODEL,
        ),
        group_id=_first_non_empty(os.environ.get("MINIMAX_GROUP_ID", "")),
        provider=_mx.PROVIDER_NAME,
    )


def read_vision_api_config() -> VisionApiConfig:
    return VisionApiConfig(
        api_key=_resolve_api_key("VISION_API_KEY", "MINIMAX_API_KEY"),
        api_host=_resolve_api_host(
            "VISION_BASE_URL",
            "VISION_API_HOST",
            "MINIMAX_API_HOST",
            "MINIMAX_BASE_URL",
        ),
        model=_first_non_empty_or_default(
            os.environ.get("VISION_MODEL", ""),
            os.environ.get("VISION_API_MODEL", ""),
            default=_mx.DEFAULT_VISION_MODEL,
        ),
        group_id=_first_non_empty(os.environ.get("MINIMAX_GROUP_ID", "")),
        provider=_mx.PROVIDER_NAME,
    )


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value.strip() and not _is_placeholder_value(value):
            return value.strip()
    return ""
