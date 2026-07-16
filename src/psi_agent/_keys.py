from __future__ import annotations

from typing import Any

from aiohttp import web

# Shared AppKey definitions to avoid circular imports and string-based access.
PROVIDER_KEY: web.AppKey[str] = web.AppKey("provider", str)
MODEL_KEY: web.AppKey[str] = web.AppKey("model", str)
API_KEY_KEY: web.AppKey[str] = web.AppKey("api_key", str)
BASE_URL_KEY: web.AppKey[str] = web.AppKey("base_url", str)

# Gateway specific
AIM_KEY: web.AppKey[Any] = web.AppKey("aim", Any)
SM_KEY: web.AppKey[Any] = web.AppKey("sm", Any)
TM_KEY: web.AppKey[Any] = web.AppKey("tm", Any)
WM_KEY: web.AppKey[Any] = web.AppKey("wm", Any)
CM_KEY: web.AppKey[Any] = web.AppKey("cm", Any)
HM_KEY: web.AppKey[Any] = web.AppKey("hm", Any)
FAVICON_PATH_KEY: web.AppKey[str | None] = web.AppKey("favicon_path")
APP_NAME_KEY: web.AppKey[str] = web.AppKey("app_name", str)
ATTENTION_KEY: web.AppKey[Any] = web.AppKey("attention", Any)
