"""Feishu/Lark user authorization and write-ownership identity.

Three things, three sets of tools:

**Which permissions** — some Feishu APIs act on behalf of a USER and need a
user_access_token the bot's app credentials can't provide. Authorization asks only
for the CAPABILITIES the task needs, and each grant is the union of those and
everything already granted, so old abilities are never lost. Which capabilities a
user already has is remembered, so a second task needing the same ones never
re-prompts.

**Getting the code back** — the happy path asks the user for **no copy-pasting**:
``feishu_auth_start`` returns a browser URL to approve, and — when an automatic
callback channel is available (``auto_receive=True``) — ``feishu_auth_wait``
receives the authorization code by itself and finishes the exchange. The code comes
back either through the Gateway's ``/oauth/callback`` relay (works when the user
approves on a phone) or through a one-shot ``127.0.0.1`` listener (same machine
only); see ``_oauth_receiver``. Only when neither channel is available does the old
manual path apply: the user copies ``code=...`` out of the browser address bar and
hands it to ``feishu_auth_complete``.

**Who owns the output** — a created document/table/task belongs to whoever created
it. ``feishu_identity_set`` records whether this user wants writes done under their
own Feishu identity (output owned by them, needs authorization) or under the bot's
(output owned by the bot). Write tools return ``need_identity_choice`` until that is
answered, rather than guessing.

Tokens are cached in ``<workspace>/.psi/feishu/uat.json`` (plaintext, local dev use;
auto-refreshed later). Tokens and choices are keyed per user via ``user_key`` (the
sender's open_id), so multiple people stay independent; empty ``user_key`` shares a
single ``default`` slot.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET`` and a redirect URI
registered in the app's security settings. The flow uses PKCE (S256). The app must
have the corresponding scopes enabled in its Feishu console permissions.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_auth_start(user_key: str = "", capabilities: str = "") -> str:
    """Begin Feishu user authorization for ONLY the permissions the task needs.

    Send ``authorize_url`` to the user and have them approve. If the result says
    ``auto_receive=True``, do NOT ask them for any code — call ``feishu_auth_wait``
    with the same ``user_key`` and the authorization completes on its own. Only if
    ``auto_receive=False`` fall back to the manual path (user copies ``code=...``
    from the browser address bar into ``feishu_auth_complete``).

    Pass the ``capabilities`` the current task actually needs — typically the
    ``need_capabilities`` list a tool just returned alongside ``need_auth``. The
    request is automatically widened to include everything this user already
    granted, so authorizing again never costs them an existing ability.

    Args:
        user_key: The message sender's open_id (from the injected ``<feishu_context>``
            ``sender_open_id``), so each user's authorization is isolated. Pass the
            same value to ``feishu_auth_wait`` / ``feishu_auth_complete`` /
            ``feishu_docs_search``. Empty shares a single ``default`` slot
            (single-user / local dev).
        capabilities: Comma-separated capability keys to request, e.g.
            ``"docx_write,wiki_write"``. Valid keys: ``docs_read``, ``drive_read``,
            ``drive_write`` (includes spreadsheets), ``docx_write``, ``wiki_write``,
            ``bitable_write``, ``task_write``, ``calendar_write``, ``contact_read``,
            ``contact_phone_email_read``. Empty asks for a general docs/drive set.
            Do NOT pass raw Feishu scope strings — an invalid scope makes Feishu
            reject the whole authorize page (error 20043), so unknown keys are refused
            here instead.
    """
    return _f.dumps_result(await _f.auth_start_impl(capabilities, user_key))


async def feishu_auth_wait(user_key: str = "", timeout_seconds: int = 180) -> str:
    """Wait for the authorization code to arrive by itself, then finish authorizing.

    Call this right after sending the user ``authorize_url`` from
    ``feishu_auth_start`` (when it reported ``auto_receive=True``). It blocks until
    the user approves in the browser, receives the code through the callback
    channel, exchanges it for a token, and caches it — the user copies nothing.

    On ``timed_out=True`` you may simply call this again to keep waiting. On
    ``manual_required=True`` the environment has no automatic channel: fall back to
    ``feishu_auth_complete`` with a code the user copies from the address bar.

    Args:
        user_key: The same open_id passed to ``feishu_auth_start``.
        timeout_seconds: How long to wait for the user to approve (10-600, default 180).
    """
    return _f.dumps_result(await _f.auth_wait_impl(user_key, timeout_seconds))


async def feishu_auth_complete(code: str, user_key: str = "") -> str:
    """Finish Feishu user authorization manually: exchange the code for a token.

    Only needed when automatic receiving is unavailable (``auto_receive=False`` from
    ``feishu_auth_start``, or ``manual_required=True`` from ``feishu_auth_wait``).
    Call it with the ``code`` the user copied from the redirect.

    The capabilities just granted are recorded, so later tasks needing the same
    ones will not ask again.

    Args:
        code: The authorization code from the redirect URL, or the full redirect URL.
        user_key: The same open_id passed to ``feishu_auth_start`` — the token is
            cached under this key. Empty shares the ``default`` slot.
    """
    return _f.dumps_result(await _f.auth_complete_impl(code, user_key))


async def feishu_identity_set(user_key: str, identity: str) -> str:
    """Record whether this user's Feishu writes are done as them, or as the bot.

    Call this after asking the user — typically because a write tool returned
    ``need_identity_choice=True``. The choice decides who owns what gets created and
    is remembered, so the user is asked once, not per document. Call it again to
    change the answer (e.g. the user says "this one should be the bot's").

    Args:
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` — writes act as this user, so documents/tables they
            create are owned by them (requires their authorization); or ``"bot"`` —
            writes use the bot's own permissions and the output is owned by the bot.
    """
    return _f.dumps_result(await _f.identity_set_impl(user_key, identity))


async def feishu_identity_get(user_key: str = "") -> str:
    """Check this user's recorded write-ownership choice and granted permissions.

    Returns ``identity`` (``"user"``, ``"bot"``, or empty when they've never been
    asked) plus ``capabilities`` — what they have already authorized. Use it to
    avoid re-asking something already settled.

    Args:
        user_key: The sender's open_id (from ``<feishu_context>``).
    """
    return _f.dumps_result(await _f.identity_get_impl(user_key))
