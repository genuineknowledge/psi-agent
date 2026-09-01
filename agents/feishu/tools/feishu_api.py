"""Feishu/Lark generic API call — one tool for any Open Platform endpoint.

The dedicated ``feishu_*`` tools cover the endpoints whose *shape* is easy to get
wrong: two-step binary uploads, sheet coordinates that silently drop data, reaction
ids that must be resolved before removal. Those stay as tools because the tool is
what enforces the invariant.

Everything else is just an URI and a JSON body, and describing all of them as
separate tools costs more context than it buys. This tool sends any request through
the same authenticated client, tenant/user token strategy, rate-limit retry, and
error-code hints the dedicated tools use — the endpoint knowledge lives in the
``feishu-api`` skill instead of in a schema.

Read the ``feishu-api`` skill before calling this: it carries the endpoint tables and,
more importantly, the rule to prefer a dedicated tool when one exists.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_api_impl as _api


async def feishu_api(
    method: str,
    uri: str,
    body_json: str = "",
    query_json: str = "",
    paths_json: str = "",
    prefer: str = "tenant",
    identity: str = "",
    user_key: str = "",
    confirm: str = "",
) -> str:
    """Call any Feishu/Lark Open Platform endpoint and return the raw JSON result.

    Use this for endpoints without a dedicated tool. **Check for a dedicated tool
    first** — see the ``feishu-api`` skill: they exist precisely where a hand-built
    request goes wrong, and this tool cannot express a file upload at all.

    Binary uploads are rejected with a pointer to the right tool: an image or file
    body has to be a real file handle, which a JSON string cannot carry.

    Args:
        method: HTTP verb — ``GET`` / ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE``.
        uri: Endpoint path starting with ``/open-apis/``, e.g.
            ``/open-apis/contact/v3/users/:user_id``. Keep ``:name`` placeholders in
            the path and supply their values via *paths_json* — do not interpolate
            them yourself, so the SDK escapes them.
        body_json: JSON object for the request body, e.g. ``'{"name":"x"}'``. Empty
            for GET/DELETE.
        query_json: JSON object of query-string params, e.g.
            ``'{"page_size":50,"user_id_type":"open_id"}'``. Values are stringified;
            a list value repeats the key.
        paths_json: JSON object filling the ``:name`` placeholders in *uri*, e.g.
            ``'{"user_id":"ou_abc"}'``.
        prefer: Token strategy. ``"tenant"`` (default) uses the bot's token and falls
            back to the caller's user token only when tenant is genuinely denied;
            ``"user"`` requires the caller's authorization up front — use it for reads
            of a person's own data and for writes that should be owned by them.
        identity: With ``prefer="user"``, ``"user"`` acts as the person and ``"bot"``
            stays the bot. Leave empty unless the endpoint creates owned content.
        user_key: The caller's open_id from ``<feishu_context>``. Required whenever a
            user token may be needed — without it there is no token to fall back to.
        confirm: The 6-digit code the **user** was sent for an irreversible endpoint.
            Leave empty on the first call: if the endpoint is guarded, nothing is sent
            to Feishu — instead the person identified by *user_key* gets the code as a
            private message and the result comes back ``need_confirmation``. Tell them
            plainly what is about to happen, then repeat the call with the digits they
            give you. You cannot derive the code yourself, and no code means the user
            has not approved it — don't work around that. Each code covers one target
            only, expires in 15 minutes, and works once. Guarded today: 解散群,
            resigning a user, deleting a department, deleting a user group, deleting
            Bitable tables.
    """
    return _api.dumps_result(
        await _api.call_api_impl(
            method=method,
            uri=uri,
            body_json=body_json,
            query_json=query_json,
            paths_json=paths_json,
            prefer=prefer,
            identity=identity,
            user_key=user_key,
            confirm=confirm,
        )
    )
