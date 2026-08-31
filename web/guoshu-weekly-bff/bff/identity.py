"""Session signing and identity -> token/workspace mapping.

The identity chain this module owns, per plan 5.3 / 6.3:

    browser cookie (signed session) -> username
        -> workspace (per-user directory, never shared across users)
        -> MCP token (single demo token today; per-user map once Q3 lands)

Identity is decided by the signed session only — never by anything the
model says, never by a request header the browser can forge.
"""

from __future__ import annotations

import hashlib
import hmac
import tempfile
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import BffConfig, SESSION_MAX_AGE_SECONDS, load_token_map


class SessionSigner:
    """Sign and verify session cookies with BFF_SESSION_SECRET."""

    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="guoshu-weekly-bff-session")

    def issue(self, username: str) -> str:
        return self._serializer.dumps({"u": username})

    def verify(self, token: str) -> str | None:
        try:
            payload = self._serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        username = payload.get("u") if isinstance(payload, dict) else None
        return username if isinstance(username, str) and username else None


def compare_digest(a: str, b: str) -> bool:
    """Constant-time string comparison for the dev-stage password check."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def workspace_for(username: str, config: BffConfig) -> str:
    """Per-user workspace: <BFF_WORKSPACE_ROOT>/<username>.

    Sharing one workspace across users would share one file root — reports
    and chart artifacts would be visible to everyone (plan chapter 4).
    The fallback lives under the system temp directory, which every
    account can write to (C:/Users/Public requires no such guarantee).
    """
    username = "".join(ch for ch in username if ch.isalnum() or ch in "-_")
    root = config.workspace_root
    if not root:
        return str(Path(tempfile.gettempdir()) / "guoshu-weekly-workspaces" / username)
    return str(Path(root) / username)


class IdentityStore:
    """Resolve an identity to its MCP token (single-token demo or token map)."""

    def __init__(self, config: BffConfig) -> None:
        self._single_token = config.mcp_token
        self._map = load_token_map(config.token_map_file)

    def mcp_token_for(self, username: str) -> str:
        if self._map:
            entry = self._map.get(username)
            return entry["token"] if entry else ""
        return self._single_token

    @property
    def per_user(self) -> bool:
        return bool(self._map)
