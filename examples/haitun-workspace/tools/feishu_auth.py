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
callback channel is available (``auto_receive=True``) — the code arrives by itself and
the exchange finishes without the user copying anything. The code comes back either
through the Gateway's ``/oauth/callback`` relay (works when the user approves on a
phone) or through a one-shot ``127.0.0.1`` listener (same machine only); see
``_oauth_receiver``. Only when neither channel is available does the old manual path
apply: the user copies ``code=...`` out of the browser address bar and hands it to
``feishu_auth_complete``.

**Collecting it without freezing the conversation** — the code takes as long to come
back as the user takes to approve, so the only question is *who* waits. Never the turn:
a tool call runs inside the Session's turn lock, so a tool that waits for minutes makes
everything the user says queue behind it — that is what "the bot is dead" looks like.
``feishu_auth_collect`` therefore hands the waiting to a task detached from the turn and
returns immediately; when the code lands, that task exchanges it and DMs the user. Use it
on every path where the code is still outstanding — after a ``link_auto`` link, and in the
``<feishu_card_action>`` turn right after a card tap. ``feishu_auth_check`` looks once and
returns, for the turn where the user reports back. No tool waits inside the turn — that is
deliberate, not an omission.

**How to ask** — call ``feishu_auth_request`` and let it choose. There are three ways to
ask, in descending order of how little the user has to do, and it returns the first one
this deployment can actually deliver (reporting the chosen ``tier``):

1. ``card`` — an interactive card whose single button both opens the consent page and
   calls back, so the agent finishes its turn at once and waits only when the click
   arrives. Sent by ``feishu_auth_card``.
2. ``link_auto`` — the plain ``authorize_url``, with the code still returning by itself
   through the callback channel. No copying, but the user has to open the link.
3. ``link_manual`` — the same URL with **no** automatic channel behind it, so the user
   copies ``code=...`` out of the address bar into ``feishu_auth_complete``.

Tier 1 needs a private chat to send the card to; tiers 1 and 2 both need an automatic
callback channel. Falling back is reported, never silent — the first two promise "no
copying", so a deployment that cannot keep that promise must say so.

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
    ``auto_receive=True``, do NOT ask them for any code — finish your turn and either call
    ``feishu_auth_collect`` (it collects in the background, so the code lands without the
    user having to prompt you again) or ask them to say a word once approved and call
    ``feishu_auth_check`` in that later turn. Never make the sending turn wait for the
    code. Only if ``auto_receive=False`` fall back to the manual path
    (user copies ``code=`` from the browser address bar into ``feishu_auth_complete``).

    Pass the ``capabilities`` the current task actually needs — typically the
    ``need_capabilities`` list a tool just returned alongside ``need_auth``. The
    request is automatically widened to include everything this user already
    granted, so authorizing again never costs them an existing ability.

    Args:
        user_key: The message sender's open_id (from the injected ``<feishu_context>``
            ``sender_open_id``), so each user's authorization is isolated. Pass the
            same value to ``feishu_auth_collect`` / ``feishu_auth_complete`` /
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


async def feishu_auth_request(
    user_key: str,
    capabilities: str = "",
    reason: str = "",
    receive_id: str = "",
) -> str:
    """Ask a user to authorize — **start here**; it picks the best available method.

    One call handles the whole "I need this user's authorization" case. It tries the three
    ways in a fixed order and returns the first that works, so you don't have to know what
    this deployment supports:

    1. ``tier="card"`` — a card whose single tap both opens the consent page and calls
       back. **Finish your turn now.** In the later ``<feishu_card_action>`` turn whose
       ``dispatch.handler`` is ``feishu_auth_collect``, call that with the ``user_key`` from
       the callback value — it collects in the background, so that turn ends at once too.
       Do not also send the link as text.
    2. ``tier="link_auto"`` — website authorization with **no code to copy**. Send the user
       ``authorize_url`` and **finish your turn**; call ``feishu_auth_collect`` to have the
       code picked up in the background, or ask them to say a word once approved and
       ``feishu_auth_check`` in that later turn. Never wait in this turn: the code keeps for
       ~10 minutes, so blocking buys nothing and costs the user everything they try to say
       meanwhile.
    3. ``tier="link_manual"`` — website authorization that **does** need a copy. Send
       ``authorize_url``, then ask the user for the ``code=`` in their address bar (the full
       URL is fine too) and pass it to ``feishu_auth_complete``.

    When it falls back, ``downgraded_from`` and ``downgrade_reason`` say why — tell the user
    plainly rather than implying the smoother path was used. Always read ``tier`` to decide
    your next move; ``next_step`` spells it out.

    Args:
        user_key: The message sender's open_id (from ``<feishu_context>`` ``sender_open_id``).
            This is whose authorization it is; pass the same value to ``feishu_auth_collect``.
        capabilities: Comma-separated capability keys the task needs — typically the
            ``need_capabilities`` a tool just returned with ``need_auth``. The request is
            automatically widened to what this user already granted, so re-authorizing never
            costs an existing ability. Empty asks for a general docs/drive set.
        reason: One line telling the user what the authorization is for, e.g.
            ``"要把周报建在你名下"``. Shown on the card; keep it concrete.
        receive_id: Where to send the card. Defaults to ``user_key`` (a DM), normally right.
            A non-``ou_`` value (e.g. a group chat) skips tier 1, because a card tapped in a
            group is routed to the tapper's own session, which cannot see the pending
            authorization recorded here.
    """
    return _f.dumps_result(await _f.auth_request_impl(user_key, capabilities, reason, receive_id))


async def feishu_auth_card(
    user_key: str,
    capabilities: str = "",
    reason: str = "",
    receive_id: str = "",
) -> str:
    """Send the one-click authorization card specifically (tier 1 only).

    Prefer ``feishu_auth_request``, which tries this first and falls back on its own. Use
    this directly only when you want the card and nothing else — it does not downgrade.

    Preferred over sending ``feishu_auth_start``'s ``authorize_url`` as text: the card's
    button opens the Feishu consent page *and* calls back to you in one tap, so you learn
    the user acted instead of guessing when to start waiting. Use it whenever a tool
    returns ``need_auth=True`` and you have the user's open_id.

    **Finish your turn right after this returns.** Do not wait for anything in this turn and
    do not also send the link as text. When the user taps the button, Feishu delivers the
    click to you as a ``<feishu_card_action>`` turn whose ``dispatch.handler`` is
    ``feishu_auth_collect``; call that *then*, with the ``user_key`` carried in the callback
    value. It hands the waiting to a background task, so that turn ends immediately as well
    — the user has only just reached the consent page at that point, and holding the turn
    open until they finish would block everything else they say.

    The card is single-use. If the user taps it but never presses 「同意授权」 on the page,
    that card is spent: send a fresh one with this tool rather than asking them to tap again.

    Falls back with ``manual_required=True`` when the deployment has no automatic callback
    channel (no ``PSI_OAUTH_CALLBACK_BASE`` and no usable loopback) — a button would then
    still leave the user copying ``code=`` from the address bar, so use the manual
    ``feishu_auth_start`` / ``feishu_auth_complete`` path instead.

    Args:
        user_key: The message sender's open_id (from ``<feishu_context>`` ``sender_open_id``).
            This is whose authorization it is; pass the same value to ``feishu_auth_collect``.
        capabilities: Comma-separated capability keys the task needs — typically the
            ``need_capabilities`` a tool just returned. Same keys and same union-with-already-
            granted behaviour as ``feishu_auth_start``. Empty asks for a general docs/drive set.
        reason: One line telling the user what this authorization is for, e.g.
            ``"要把周报建在你名下"``. Shown on the card; keep it concrete.
        receive_id: Where to send the card. Defaults to ``user_key`` (a DM), which is
            normally right. Must be an ``ou_`` open_id: a card tapped in a group chat is
            routed to the tapper's own private session, which cannot see the pending
            authorization recorded here.
    """
    return _f.dumps_result(await _f.auth_card_impl(user_key, capabilities, reason, receive_id))


async def feishu_auth_collect(user_key: str = "", timeout_seconds: int = 600) -> str:
    """Collect the authorization code **in the background** — returns at once, never waits.

    This is the right tool whenever the code still has to come back: the
    ``<feishu_card_action>`` turn right after a card tap (its ``dispatch.handler``), and
    the turn that sends a ``link_auto`` ``authorize_url``. Waiting is unavoidable — users
    open the page, may log in first, and only then approve — but *who* waits is the whole
    problem. Waiting inside the tool call means waiting inside the
    Session's turn lock, so everything the user says for those minutes queues behind it
    and the bot looks dead. This hands the waiting to a task detached from the turn, so
    the turn ends immediately and the conversation stays responsive.

    **Finish your turn right after this returns.** When the code arrives the background
    task exchanges it for the token by itself and DMs the user that they can continue —
    you do not need to be waiting for that to happen. Call it again any time to read the
    progress (``status``: ``watching``/``granted``/``failed``/``timeout``); a second call
    never starts a second collector, because the relay hands the code out once and two
    collectors would race for it.

    On ``background=False`` the wait could not be detached: end the turn anyway and use
    ``feishu_auth_check`` in a later turn instead of blocking here.

    Args:
        user_key: The same open_id passed to ``feishu_auth_request`` / ``feishu_auth_card``
            — for a card tap, the ``user_key`` carried in the callback value.
        timeout_seconds: How long the background task keeps watching (10-600, default
            600). Capped at the Gateway relay's ~10-minute retention, past which there is
            nothing left to collect.
    """
    return _f.dumps_result(await _f.auth_collect_impl(user_key, timeout_seconds))


async def feishu_auth_check(user_key: str = "") -> str:
    """Check whether the authorization code has arrived — returns at once, never blocks.

    For the turn where the user reports back ("点好了"): same retrieval channel as
    ``feishu_auth_collect``, but it looks once and returns instead of leaving a background
    task behind. The code keeps in the Gateway relay for about 10 minutes, which is what
    makes checking later safe — nobody has to sit and wait for it.

    ``pending=True`` means the code is not there yet and is **not** a failure: finish your
    turn and either leave a ``feishu_auth_collect`` running (it picks the code up by itself)
    or ask the user to say a word once approved and check again then. Note that while a
    background collector is watching, it may take the code first — the authorization still
    completes, and the user gets told so; this then reports no pending authorization.

    Args:
        user_key: The same open_id passed to ``feishu_auth_request`` / ``feishu_auth_start``.
    """
    return _f.dumps_result(await _f.auth_check_impl(user_key))


async def feishu_auth_complete(code: str, user_key: str = "") -> str:
    """Finish Feishu user authorization manually: exchange the code for a token.

    Only needed when automatic receiving is unavailable (``auto_receive=False`` from
    ``feishu_auth_start``, or ``manual_required=True`` from ``feishu_auth_collect`` /
    ``feishu_auth_check``).
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
