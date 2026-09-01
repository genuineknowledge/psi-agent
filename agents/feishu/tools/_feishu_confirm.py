"""One-time confirmation codes for irreversible Feishu calls.

The ``confirm`` token in a skill's ``rules`` block gates a call the model must not
make on its own — 解散群, 办离职, 删部门. But the token is a *constant written in the
skill file*, and the model reads that file to pick the endpoint in the first place.
So the first refusal hands back a token the model already knows: it can echo it in
the same turn and the gate becomes a speed bump it talks itself past. That is not a
human confirmation, and for a group dismissal (Feishu keeps no history — every
message and file is gone, unrecoverable by any tool) the difference matters.

This module replaces the shared secret with one the model cannot know: a 6-digit
code, generated per call, delivered to the **user** out of band as a private Feishu
message, and never returned in the tool result. The only way it reaches the model is
if the person reads it and types it back, which is exactly the round trip the gate
was supposed to force.

A code is bound to what it approves — session, method, uri and path parameters — so
a code issued for "解散 A 群" cannot dissolve B. It is single-use (redeeming unlinks
it) and expires, so an unused code left in a chat is not a standing authorization.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import time
import uuid
from typing import Any

import anyio

from psi_agent._appdata import resolve_appdata_root

_TTL_SECONDS = 15 * 60
_CODE_DIGITS = 6
_STORE_DIR = "feishu-confirm-codes"


def new_code() -> str:
    """A 6-digit code. Short enough to retype, random enough not to be guessed."""
    return f"{secrets.randbelow(10**_CODE_DIGITS):0{_CODE_DIGITS}d}"


def scope_key(session_id: str, method: str, uri: str, paths: dict[str, Any]) -> str:
    """Identity of the exact operation a code authorizes.

    ``paths`` is in the key because that is where the target lives: the endpoint for
    dissolving a group is the same string for every group, and only ``chat_id`` says
    which one. Without it a code obtained for a test group would approve dissolving
    any group in the tenant.
    """
    target = json.dumps(
        {
            "session": session_id.strip(),
            "method": method.strip().upper(),
            "uri": uri.strip(),
            "paths": {str(k): str(v) for k, v in sorted(paths.items())},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(target.encode("utf-8")).hexdigest()[:32]


async def _store_path(scope: str, appdata: str) -> anyio.Path:
    if not scope.isalnum():
        raise ValueError(f"Invalid confirmation scope: {scope!r}")
    root = await resolve_appdata_root(appdata)
    return anyio.Path(root) / _STORE_DIR / f"{scope}.json"


async def issue(scope: str, endpoint: str, appdata: str = "") -> str:
    """Mint and persist a code for *scope*, replacing any code already pending.

    Re-issuing on every unconfirmed attempt is deliberate: if the user never used
    the previous code, the previous code should stop working.
    """
    path = await _store_path(scope, appdata)
    await path.parent.mkdir(parents=True, exist_ok=True)
    await path.parent.chmod(0o700)
    code = new_code()
    payload = {
        "code": code,
        "endpoint": endpoint,
        "expires_at": time.time() + _TTL_SECONDS,
    }
    temporary = path.parent / f".{scope}.{uuid.uuid4().hex}.tmp"
    try:
        await temporary.touch(mode=0o600, exist_ok=False)
        await temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        await temporary.chmod(0o600)
        await temporary.replace(path)
        await path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            await temporary.unlink()
    return code


async def redeem(scope: str, code: str, appdata: str = "") -> bool:
    """Consume the code for *scope*; false if it is absent, wrong or expired.

    The file is unlinked before the answer is returned, so a code cannot approve two
    calls even if the model repeats it — a wrong guess also burns the pending code,
    which is the safe direction to fail.
    """
    candidate = (code or "").strip()
    if not candidate:
        return False
    path = await _store_path(scope, appdata)
    try:
        raw = await path.read_text(encoding="utf-8")
    except FileNotFoundError, NotADirectoryError:
        return False
    with contextlib.suppress(FileNotFoundError):
        await path.unlink()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, int | float) or time.time() > expires_at:
        return False
    stored = payload.get("code")
    return isinstance(stored, str) and secrets.compare_digest(stored, candidate)


def ttl_minutes() -> int:
    """TTL in whole minutes, for the message shown to the user."""
    return _TTL_SECONDS // 60
