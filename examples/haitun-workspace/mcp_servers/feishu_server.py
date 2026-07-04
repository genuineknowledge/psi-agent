"""Feishu (Lark) MCP server — messaging, cards, image/file upload and sending.

Supports two authentication modes:

1. **Webhook mode** (simplest): Set ``FEISHU_WEBHOOK_URL`` env var.
   Only text and webhook-compatible messages are available.

2. **API mode** (full features): Set ``FEISHU_APP_ID`` + ``FEISHU_APP_SECRET``.
   Enables image upload, file sending, and interactive cards.

Run::

    python feishu_server.py
    # or: uv run python feishu_server.py
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import aiohttp
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "feishu-mcp",
    description="Feishu/Lark messaging — send text, cards, images, and files",
)

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

_WEBHOOK_URL: str = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
_APP_ID: str = os.environ.get("FEISHU_APP_ID", "").strip()
_APP_SECRET: str = os.environ.get("FEISHU_APP_SECRET", "").strip()
_API_BASE: str = "https://open.feishu.cn/open-apis"

# Token cache for API mode
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _valid(webhook_url: str = "") -> str:
    """Check that we have usable credentials. Returns "" on success, or an error string."""
    if webhook_url.strip():
        return ""
    if _WEBHOOK_URL:
        return ""
    if _APP_ID and _APP_SECRET:
        return ""
    return (
        "No Feishu credentials configured. Set one of:\n"
        "  - FEISHU_WEBHOOK_URL (webhook mode, simplest)\n"
        "  - FEISHU_APP_ID + FEISHU_APP_SECRET (API mode, full features)"
    )


async def _get_token() -> str:
    """Obtain a tenant access token for API mode. Cached until expiry."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    if not _APP_ID or not _APP_SECRET:
        raise RuntimeError(
            "API mode requires FEISHU_APP_ID and FEISHU_APP_SECRET env vars."
        )

    url = f"{_API_BASE}/auth/v3/tenant_access_token/internal"
    body = {"app_id": _APP_ID, "app_secret": _APP_SECRET}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body) as resp:
            data = await resp.json()

    code = data.get("code", -1)
    if code != 0:
        raise RuntimeError(
            f"Failed to get Feishu token: code={code}, msg={data.get('msg', data)}"
        )

    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = now + data.get("expire", 7200)
    return _token_cache["token"]


async def _webhook_post(webhook_url: str, body: dict[str, Any]) -> str:
    """Send a message via webhook URL."""
    url = webhook_url.strip() or _WEBHOOK_URL
    if not url:
        raise RuntimeError(
            "No webhook URL. Set FEISHU_WEBHOOK_URL env var or pass webhook_url."
        )

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body) as resp:
            data = await resp.json()

    code = data.get("code", -1)
    if code != 0:
        return f"[Error] Feishu webhook returned code {code}: {data.get('msg', data)}"
    return "[OK] Sent"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def feishu_send_text(content: str, webhook_url: str = "") -> str:
    """Send a plain text message to a Feishu chat via webhook or API.

    Uses FEISHU_WEBHOOK_URL by default. Pass webhook_url to override.
    Supports Feishu Markdown: **bold**, *italic*, <at id=open_id>@mention</at>.

    Args:
        content: Message text with optional Markdown formatting.
        webhook_url: Optional webhook URL override.

    Returns:
        "[OK] Sent" on success, or an error string.
    """
    err = _valid(webhook_url)
    if err:
        # If we have API credentials, try chat API (simplified: fallback to error for now)
        return f"[Error] {err}"

    return await _webhook_post(
        webhook_url,
        {"msg_type": "text", "content": {"text": content}},
    )


@mcp.tool()
async def feishu_send_card(
    title: str,
    content: str,
    elements_json: str = "[]",
    header_color: str = "blue",
    webhook_url: str = "",
) -> str:
    """Send an interactive card message to Feishu.

    Cards can contain rich layout with title, body text, and interactive elements
    (buttons, dropdowns, date pickers, etc.) passed as a JSON array.

    Args:
        title: Card title (shown in the header bar).
        content: Card body text (supports Markdown).
        elements_json: JSON array of interactive elements, e.g.
            ``'[{"tag":"button","text":{"tag":"plain_text","content":"Click me"}}]'``.
        header_color: Header colour — ``"blue"``, ``"red"``, ``"green"``, ``"yellow"``,
            ``"purple"``, ``"grey"``, or ``"turquoise"``.
        webhook_url: Optional webhook URL override.

    Returns:
        "[OK] Sent" on success, or an error string.
    """
    err = _valid(webhook_url)
    if err:
        return f"[Error] {err}"

    try:
        elements = json.loads(elements_json) if elements_json.strip() else []
    except json.JSONDecodeError as e:
        return f"[Error] Invalid elements_json: {e}"

    card = {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": header_color,
        },
        "elements": elements or [
            {"tag": "markdown", "content": content},
        ],
    }

    # If no explicit elements array was given, include content in the card body
    if not elements and content:
        card["elements"] = [{"tag": "markdown", "content": content}]

    body: dict[str, Any] = {
        "msg_type": "interactive",
        "card": card,
    }

    return await _webhook_post(webhook_url, body)


@mcp.tool()
async def feishu_upload_image(image_path: str) -> str:
    """Upload an image to Feishu and get its image_key for use in messages.

    Requires API mode (FEISHU_APP_ID + FEISHU_APP_SECRET).

    Args:
        image_path: Absolute path to the image file (PNG, JPG, GIF, WEBP).

    Returns:
        JSON with ``image_key`` on success, or an error string.
    """
    if not _APP_ID or not _APP_SECRET:
        return "[Error] Image upload requires FEISHU_APP_ID + FEISHU_APP_SECRET (API mode)."

    token = await _get_token()
    url = f"{_API_BASE}/im/v1/images"

    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("image_type", "message")
            data.add_field(
                "image",
                open(image_path, "rb"),
                filename=os.path.basename(image_path),
            )
            headers = {"Authorization": f"Bearer {token}"}
            async with session.post(url, data=data, headers=headers) as resp:
                result = await resp.json()

        code = result.get("code", -1)
        if code != 0:
            return f"[Error] Upload failed: code={code}, msg={result.get('msg', result)}"

        image_key = result.get("data", {}).get("image_key", "")
        return json.dumps({"image_key": image_key}, ensure_ascii=False)
    except FileNotFoundError:
        return f"[Error] Image file not found: {image_path}"
    except Exception as e:
        return f"[Error] Upload failed: {e}"


@mcp.tool()
async def feishu_send_image(image_key_or_url: str, webhook_url: str = "") -> str:
    """Send an image message to Feishu.

    Use ``feishu_upload_image`` first to get an image_key, or pass a public
    image URL directly.

    Args:
        image_key_or_url: An image_key from upload, or a public image URL.
        webhook_url: Optional webhook URL override.

    Returns:
        "[OK] Sent" on success, or an error string.
    """
    err = _valid(webhook_url)
    if err:
        return f"[Error] {err}"

    return await _webhook_post(
        webhook_url,
        {"msg_type": "image", "content": {"image_key": image_key_or_url}},
    )


@mcp.tool()
async def feishu_send_file(file_path: str, webhook_url: str = "") -> str:
    """Send a file to a Feishu chat.

    Supported types: PDF, DOC, XLS, PPT, TXT, ZIP, images, and more.

    Args:
        file_path: Absolute path to the file.
        webhook_url: Optional webhook URL override.

    Returns:
        "[OK] Sent" on success, or an error string.
    """
    err = _valid(webhook_url)
    if err:
        return f"[Error] {err}"

    if not os.path.isfile(file_path):
        return f"[Error] File not found: {file_path}"

    # File sending via webhook requires upload first, then send.
    # For now, we support webhook-only text/image; file needs API mode.
    if not _APP_ID or not _APP_SECRET:
        return (
            "[Error] File sending requires FEISHU_APP_ID + FEISHU_APP_SECRET (API mode). "
            "Consider sharing the file path via text message instead."
        )

    token = await _get_token()

    try:
        fname = os.path.basename(file_path)
        fsize = os.path.getsize(file_path)

        async with aiohttp.ClientSession() as session:
            # Step 1: Upload
            data = aiohttp.FormData()
            data.add_field("file_type", "stream")
            data.add_field("file_name", fname)
            data.add_field("file", open(file_path, "rb"), filename=fname)
            headers = {"Authorization": f"Bearer {token}"}

            async with session.post(
                f"{_API_BASE}/im/v1/files", data=data, headers=headers
            ) as resp:
                result = await resp.json()

            code = result.get("code", -1)
            if code != 0:
                return f"[Error] File upload failed: code={code}, msg={result.get('msg', result)}"

            file_key = result.get("data", {}).get("file_key", "")

            # Step 2: Send (if webhook, send via webhook; otherwise API)
            target_url = webhook_url.strip() or _WEBHOOK_URL
            if target_url:
                body = {
                    "msg_type": "file",
                    "content": {"file_key": file_key},
                }
                async with session.post(target_url, json=body) as resp2:
                    data2 = await resp2.json()
                code2 = data2.get("code", -1)
                if code2 != 0:
                    return f"[Error] File send failed: code={code2}, msg={data2.get('msg', data2)}"
                return "[OK] Sent"

            return f"[OK] File uploaded (file_key={file_key}). Use webhook to send."
    except FileNotFoundError:
        return f"[Error] File not found: {file_path}"
    except Exception as e:
        return f"[Error] File send failed: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
