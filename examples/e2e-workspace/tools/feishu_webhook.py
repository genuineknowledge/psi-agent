"""Send messages to Feishu groups via webhook bot.

Setup:
    1. Open any Feishu group → Settings → Group Bot → Custom Bot
    2. Copy the webhook URL
    3. Set env var: FEISHU_WEBHOOK_URL=<your-webhook-url>

Then the agent can send text to that group with feishu_send_text().
"""

from __future__ import annotations

import os

import aiohttp


async def feishu_send_text(content: str, webhook_url: str = "") -> str:
    """Send a plain text message to a Feishu group via webhook bot.

    Uses the webhook URL from FEISHU_WEBHOOK_URL env var, or an
    explicit webhook_url parameter.

    Args:
        content: Message text. Supports Feishu Markdown syntax:
                 **bold**, *italic*, <at id=open_id>@mention</at>, etc.
        webhook_url: Optional override. If empty, reads FEISHU_WEBHOOK_URL env var.

    Returns:
        "[OK] Sent" on success, or "[Error] ..." on failure.

    Example:
        feishu_send_text("Hello from psi-agent! **Task done.**")
        feishu_send_text("Sent to another group", webhook_url="https://...")
    """
    url = webhook_url.strip() or os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        return (
            "[Error] No webhook URL configured. "
            "Set FEISHU_WEBHOOK_URL env var, or pass webhook_url. "
            "Get one from: Feishu group → Settings → Group Bot → Custom Bot."
        )

    body = {"msg_type": "text", "content": {"text": content}}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body) as resp:
                data = await resp.json()
    except aiohttp.ClientError as e:
        return f"[Error] Feishu request failed: {e}"

    code = data.get("code", -1)
    if code != 0:
        return f"[Error] Feishu returned code {code}: {data.get('msg', data)}"

    return "[OK] Sent"
