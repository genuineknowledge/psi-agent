"""Feishu/Lark user authorization (OAuth authorization-code flow) for user_access_token.

Some Feishu APIs (e.g. document search) act on behalf of a USER and require a
user_access_token, which the bot's app credentials can't provide. These two
tools run the authorization-code flow (China/feishu.cn):
``feishu_auth_start`` returns a browser URL for the user to approve; the user
approves, is redirected to the app's redirect_uri with ``?code=...`` in the
address bar, and hands that code to ``feishu_auth_complete``, which exchanges it
for a token and caches it (in ``<workspace>/.psi/feishu/uat.json`` — plaintext,
local dev use; auto-refreshed later).

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``, a redirect URI
registered in the app's security settings (default ``http://localhost/``,
override with ``PSI_FEISHU_REDIRECT_URI``), and the requested OAuth scopes.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_auth_start(scopes: str = "") -> str:
    """Begin Feishu user authorization: return a browser URL for the user to approve.

    Tell the user to open ``authorize_url`` and approve. They'll be redirected to
    the app's redirect_uri with ``?code=...`` in the address bar — have them copy
    that code (or the whole redirected URL) and pass it to ``feishu_auth_complete``.

    Args:
        scopes: Space-separated OAuth scopes. Empty uses a default read scope set
            (docs/drive readonly + offline_access for refresh).
    """
    return _f.dumps_result(await _f.auth_start_impl(scopes))


async def feishu_auth_complete(code: str) -> str:
    """Finish Feishu user authorization: exchange the code for a token and cache it.

    Call this with the ``code`` the user copied from the redirect (or the whole
    redirected URL — the code is extracted automatically) after they approved in
    ``feishu_auth_start``.

    Args:
        code: The authorization code from the redirect URL, or the full redirect URL.
    """
    return _f.dumps_result(await _f.auth_complete_impl(code))
