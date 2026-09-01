"""SPA index.html shell: inject Gateway ``app_name`` into the built page title."""

from __future__ import annotations

import html

import anyio

DEFAULT_APP_NAME = "Haitun Agent"
APP_NAME_PLACEHOLDER = "__GATEWAY_APP_NAME__"


def inject_app_name(html_text: str, app_name: str) -> str:
    """Replace the title placeholder with an HTML-escaped application name."""
    safe = html.escape(app_name, quote=True)
    if APP_NAME_PLACEHOLDER in html_text:
        return html_text.replace(APP_NAME_PLACEHOLDER, safe)
    # Fallback for older dist builds that still ship a fixed <title>.
    return html_text.replace("<title>控制台</title>", f"<title>{safe}</title>", 1)


async def read_spa_index_template() -> str | None:
    """Return spa/dist/index.html, or spa/index.html when dist is missing (dev)."""
    base = anyio.Path(__file__).parent / "spa"
    for rel in ("dist/index.html", "index.html"):
        path = base / rel
        if await path.is_file():
            return await path.read_text(encoding="utf-8")
    return None
