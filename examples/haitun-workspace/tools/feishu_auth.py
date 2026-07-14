"""Feishu/Lark user authorization (OAuth device flow) for user_access_token.

Some Feishu APIs (e.g. document search) act on behalf of a USER and require a
user_access_token, which the bot's app credentials can't provide. These two
tools run the device-flow OAuth: ``feishu_auth_start`` returns a URL + code for
the user to approve in their browser, then ``feishu_auth_complete`` waits for
the approval and caches the token (in ``<workspace>/.psi/feishu/uat.json`` —
plaintext, local dev use). The cached token is auto-refreshed later.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET`` and the app to have
the requested OAuth scopes enabled.
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
    """Begin Feishu user authorization (device flow) for a user_access_token.

    Returns ``verification_url`` + ``user_code``: tell the user to open the URL,
    enter the code, and approve. Then call ``feishu_auth_complete``.

    Args:
        scopes: Space-separated OAuth scopes. Empty uses a default read scope set
            (docs/drive readonly + offline_access for refresh).
    """
    return _f.dumps_result(await _f.auth_start_impl(scopes))


async def feishu_auth_complete(timeout_seconds: int = 60) -> str:
    """Finish Feishu user authorization: wait for the user to approve, cache the token.

    Call this after ``feishu_auth_start`` once the user has approved in their
    browser. Polls up to ``timeout_seconds``; if it times out before approval,
    just call it again.

    Args:
        timeout_seconds: How long to wait for the approval (default 60).
    """
    return _f.dumps_result(await _f.auth_complete_impl(timeout_seconds))
