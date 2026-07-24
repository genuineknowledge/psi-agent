"""Feishu/Lark user authorization (OAuth authorization-code flow) for user_access_token.

Some Feishu APIs (e.g. document search) act on behalf of a USER and require a
user_access_token, which the bot's app credentials can't provide. These two
tools run the authorization-code flow (China/feishu.cn):
``feishu_auth_start`` returns a browser URL for the user to approve; the user
approves, is redirected to the app's redirect_uri with ``?code=...`` in the
address bar, and hands that code to ``feishu_auth_complete``, which exchanges it
for a token and caches it (in ``<workspace>/.psi/feishu/uat.json`` — plaintext,
local dev use; auto-refreshed later). Tokens are keyed per user via ``user_key``
(the sender's open_id), so multiple people can authorize independently without
overwriting each other; empty ``user_key`` shares a single ``default`` slot.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET`` and a redirect URI
registered in the app's security settings (default ``http://localhost/``,
override with ``PSI_FEISHU_REDIRECT_URI``). The OAuth scopes are fixed to a
read-only docs/drive set inside the tool — callers (and the LLM) cannot choose
them, since an invalid scope makes Feishu reject the authorize page (error 20043).
The app must have those scopes enabled in its Feishu console permissions.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_auth_start(user_key: str = "") -> str:
    """Begin Feishu user authorization: return a browser URL for the user to approve.

    Tell the user to open ``authorize_url`` and approve. They'll be redirected to
    the app's redirect_uri with ``?code=...`` in the address bar — have them copy
    that code (or the whole redirected URL) and pass it to ``feishu_auth_complete``.

    The OAuth scopes are fixed by the tool (a read-only docs/drive set); do NOT
    try to choose or pass scopes — an invalid scope makes Feishu reject the whole
    authorize page (error 20043).

    Args:
        user_key: The message sender's open_id (from the injected ``<feishu_context>``
            ``sender_open_id``), so each user's authorization is isolated. Pass the
            same value to ``feishu_auth_complete`` / ``feishu_docs_search``. Empty
            shares a single ``default`` slot (single-user / local dev).
    """
    return _f.dumps_result(await _f.auth_start_impl("", user_key))


async def feishu_auth_complete(code: str, user_key: str = "") -> str:
    """Finish Feishu user authorization: exchange the code for a token and cache it.

    Call this with the ``code`` the user copied from the redirect (or the whole
    redirected URL — the code is extracted automatically) after they approved in
    ``feishu_auth_start``.

    Args:
        code: The authorization code from the redirect URL, or the full redirect URL.
        user_key: The same open_id passed to ``feishu_auth_start`` — the token is
            cached under this key. Empty shares the ``default`` slot.
    """
    return _f.dumps_result(await _f.auth_complete_impl(code, user_key))
