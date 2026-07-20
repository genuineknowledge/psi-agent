"""Resolve built-in AI defaults for Gateway open-and-use bootstrap.

When the AI pool is empty, always attach the Haitun remote default proxy.
Users override by connecting models in Hub / AiDialog (those stay in the pool).

VPS Nginx (see ``spa/remote-ai/``) injects the real upstream key; clients never
ship secrets — only the placeholder ``DEFAULT_REMOTE_API_KEY``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger

from psi_agent.gateway._ai_manager import AiInfo, AIManager

DEFAULT_LABEL = "Haitun Default"
DEFAULT_MODEL = "glm-4-flash"

# Remote open-and-use proxy on the shared Tencent VPS
DEFAULT_REMOTE_PROVIDER = "openai"  # OpenAI-compatible talk to our proxy
DEFAULT_REMOTE_BASE_URL = "https://haitun.addchess.cn"
# Placeholder Bearer; VPS Nginx replaces it with the real key (spa/remote-ai/).
DEFAULT_REMOTE_API_KEY = "haitun-default"

# Back-compat aliases
DEFAULT_PROVIDER = DEFAULT_REMOTE_PROVIDER
DEFAULT_BASE_URL = DEFAULT_REMOTE_BASE_URL

_REMOTE_URL_ENV_VARS = (
    "PSI_HAITUN_AI_URL",
    "HAITUN_DEFAULT_AI_URL",
)

SourceKind = Literal["remote_default"]


@dataclass(frozen=True)
class ResolvedAiDefaults:
    label: str
    provider: str
    model: str
    base_url: str
    api_key: str
    source: SourceKind


def _first_env(*names: str) -> str:
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def resolve_ai_defaults() -> ResolvedAiDefaults:
    """Built-in remote proxy defaults.

    Only ``PSI_HAITUN_AI_URL`` / ``HAITUN_DEFAULT_AI_URL`` (and optional
    ``PSI_AI_MODEL``) may tweak the remote endpoint — local API keys are
    ignored for bootstrap; users who want their own key use Hub / AiDialog.
    """
    model = _first_env("PSI_AI_MODEL") or DEFAULT_MODEL
    remote_url = _first_env(*_REMOTE_URL_ENV_VARS) or DEFAULT_REMOTE_BASE_URL
    return ResolvedAiDefaults(
        label=DEFAULT_LABEL,
        provider=DEFAULT_REMOTE_PROVIDER,
        model=model,
        base_url=remote_url,
        api_key=DEFAULT_REMOTE_API_KEY,
        source="remote_default",
    )


def ai_defaults_public_dict(resolved: ResolvedAiDefaults) -> dict[str, Any]:
    """Public view for GET /ais/default-config — never exposes api_key."""
    return {
        "label": resolved.label,
        "provider": resolved.provider,
        "model": resolved.model,
        "base_url": resolved.base_url,
        "source": resolved.source,
        "api_key_configured": False,
    }


async def bootstrap_default_ai(aim: AIManager) -> AiInfo | None:
    """Create one AI from remote defaults when the pool is empty."""
    if await aim.list_all():
        return None
    defaults = resolve_ai_defaults()
    logger.info(
        f"Auto-bootstrapping default AI ({defaults.label}, source={defaults.source}, "
        f"provider={defaults.provider!r}, model={defaults.model!r}, base_url={defaults.base_url!r})"
    )
    return await aim.create(
        provider=defaults.provider,
        model=defaults.model,
        api_key=defaults.api_key,
        base_url=defaults.base_url,
    )
