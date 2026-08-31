"""Environment configuration for the BFF.

Everything comes from the process starter, mirroring the agent-side rule:
the BFF never mints credentials and the browser never sees them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_GATEWAY = "http://127.0.0.1:8766"
DEFAULT_LISTEN = "127.0.0.1:8780"
DEFAULT_RATE_LIMIT_PER_MINUTE = 20
SESSION_MAX_AGE_SECONDS = 12 * 3600


class BffConfigError(Exception):
    """A configuration failure whose message never contains a credential."""


@dataclass(frozen=True)
class BffConfig:
    gateway_base_url: str
    session_secret: str = field(repr=False)
    listen_host: str
    listen_port: int
    # Single-token demo mode: one MCP token shared by every identity (the
    # current A-line demo state). Q3's per-user token map supersedes this.
    mcp_token: str = field(repr=False, default="")
    token_map_file: str = ""
    workspace_root: str = ""
    # Reserved: set to send X-Gateway-Secret on forwarded requests. The
    # Gateway side of the check is deployed before any public exposure (B5).
    gateway_shared_secret: str = field(repr=False, default="")
    # Dev-stage login: one shared demo account. Registration (trial stage,
    # B5) replaces this with per-user accounts.
    dev_username: str = "demo"
    dev_password: str = field(repr=False, default="demo")
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    # The取数 service the BFF itself talks to for deterministic artifacts
    # (P1-1 weekly summary). Defaults to the local mock MCP.
    mcp_url: str = "http://127.0.0.1:18901/mcp"


def _require(name: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise BffConfigError(f"{name} is required; the process starter owns it")
    return value


def _token_map_ok(path: str) -> str:
    """A token map must live outside the source tree, be a file, and be 0600."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        raise BffConfigError(f"GUOSHU_WEEKLY_TOKEN_MAP_FILE is not a file: {path}")
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        raise BffConfigError(f"token map must be mode 0600, got {oct(mode)}: {path}")
    return path


def load_config() -> BffConfig:
    env = os.environ
    gateway = env.get("PSI_GATEWAY_BASE_URL", DEFAULT_GATEWAY).rstrip("/")
    listen = env.get("BFF_LISTEN", DEFAULT_LISTEN)
    host, _, port = listen.rpartition(":")
    try:
        port_int = int(port)
    except ValueError as exc:
        raise BffConfigError(f"BFF_LISTEN must be host:port, got {listen!r}") from exc

    return BffConfig(
        gateway_base_url=gateway,
        mcp_url=env.get("GUOSHU_WEEKLY_MCP_URL", "http://127.0.0.1:18901/mcp").strip(),
        session_secret=_require("BFF_SESSION_SECRET", env.get("BFF_SESSION_SECRET", "")),
        listen_host=host,
        listen_port=port_int,
        mcp_token=env.get("GUOSHU_WEEKLY_MCP_TOKEN", "").strip(),
        token_map_file=_token_map_ok(env.get("GUOSHU_WEEKLY_TOKEN_MAP_FILE", "").strip()),
        workspace_root=env.get("BFF_WORKSPACE_ROOT", "").strip(),
        gateway_shared_secret=env.get("PSI_GATEWAY_SHARED_SECRET", "").strip(),
        dev_username=env.get("BFF_DEV_USERNAME", "demo").strip(),
        dev_password=env.get("BFF_DEV_PASSWORD", "demo"),
        rate_limit_per_minute=_int_or(env.get("BFF_RATE_LIMIT_PER_MINUTE", ""), DEFAULT_RATE_LIMIT_PER_MINUTE),
    )


def _int_or(raw: str | None, default: int) -> int:
    try:
        value = int(raw or "")
    except ValueError:
        return default
    return max(1, min(value, 1000))


def load_token_map(path: str) -> dict[str, dict[str, str]]:
    """Read the per-user token map (plan appendix C):

    {"<identity>": {"token": "...", "workspace_id": "..."}}

    One token must not be shared across identities — enforced here on load.
    """
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BffConfigError("token map must be a JSON object")
    seen: dict[str, str] = {}
    result: dict[str, dict[str, str]] = {}
    for identity, entry in data.items():
        if not isinstance(entry, dict) or not entry.get("token"):
            raise BffConfigError(f"token map entry for {identity!r} needs a token")
        token = entry["token"]
        owner = seen.get(token)
        if owner is not None and owner != identity:
            raise BffConfigError("one token must not be assigned to multiple identities")
        seen[token] = identity
        result[str(identity)] = {"token": token, "workspace_id": str(entry.get("workspace_id", ""))}
    return result
