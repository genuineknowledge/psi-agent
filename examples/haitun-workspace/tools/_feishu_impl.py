"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.
"""

from __future__ import annotations

import json
import os
from typing import Any

_client: Any = None


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _config() -> tuple[str, str] | None:
    app_id = os.environ.get("PSI_FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("PSI_FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None
    return app_id, app_secret


def _reset_client() -> None:
    global _client
    _client = None


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client  # noqa: PLC0415

    app_id, app_secret = creds
    _client = Client.builder().app_id(app_id).app_secret(app_secret).build()
    return _client


async def _invoke(request: Any) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    try:
        resp = await client.arequest(request)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")

    code = getattr(resp, "code", None)
    msg = getattr(resp, "msg", "") or ""
    data: dict[str, Any] = {}
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        try:
            body = json.loads(bytes(content).decode("utf-8"))
            if isinstance(body, dict):
                data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
                if code is None:
                    code = body.get("code")
                if not msg:
                    msg = body.get("msg", "") or ""
        except ValueError, UnicodeDecodeError:
            pass

    ok = code == 0
    if not ok:
        return {
            "ok": False,
            "code": code,
            "msg": msg,
            "data": data,
            "message": f"Feishu API error {code}: {msg}",
        }
    return {"ok": True, "code": 0, "msg": msg, "data": data}
