from __future__ import annotations

from aiohttp import web

PROVIDER_KEY: web.AppKey[str] = web.AppKey("provider", str)
MODEL_KEY: web.AppKey[str] = web.AppKey("model", str)
API_KEY_KEY: web.AppKey[str] = web.AppKey("api_key", str)
BASE_URL_KEY: web.AppKey[str] = web.AppKey("base_url", str)
