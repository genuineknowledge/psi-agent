"""Tests for the Haitun workspace ``browser`` tool and its MCP prefix plumbing.

These tests never launch a real browser or npx process. They exercise:

- ``_mcp.mcp`` prefix behaviour — ``prefix=""`` yields un-prefixed tool names (so
  Playwright's ``browser_navigate`` does not become ``browser_browser_navigate``),
  while the default keeps the historical ``<func>_`` prefix used by ``serper``.
- ``_browser_impl`` command construction and its clear error when npx is absent.
"""

from __future__ import annotations

import importlib
import inspect
import socket
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_mcp: Any = importlib.import_module("_mcp")
_browser_impl: Any = importlib.import_module("_browser_impl")


# ── _mcp prefix behaviour ────────────────────────────────────────────────────


@pytest.fixture
def _bypass_schema_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force live discovery, and never touch the committed cache file.

    ``mcp()`` prefers ``.mcp_cache/<name>.json`` and only falls back to ``_discover``
    on a miss. Once ``browser.json`` was committed, every test that patches
    ``_discover`` was silently bypassed: the decorator loaded the real 41 cached
    ``browser_*`` schemas, so the tests asserted against production data instead of
    their fakes (and the ones checking discovery-failure containment never exercised
    the failure path at all).

    ``_save_cached_schemas`` is stubbed out too — with the cache forced to miss, a test
    run would otherwise *overwrite* the committed cache with canned fixture schemas.
    """
    monkeypatch.setattr(_mcp, "_load_cached_schemas", lambda _name: (None, None))
    monkeypatch.setattr(_mcp, "_save_cached_schemas", lambda _name, _prefix, _schemas: None)


@pytest.fixture
def _fake_discover(monkeypatch: pytest.MonkeyPatch, _bypass_schema_cache: None) -> None:
    """Make ``mcp()`` skip the network: return two canned tool schemas."""

    def _discover(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "browser_navigate": {
                "name": "browser_navigate",
                "description": "Navigate to a URL.",
                "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            },
            "search": {
                "name": "search",
                "description": "Search the web.",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            },
        }

    monkeypatch.setattr(_mcp, "_discover", _discover)


def test_empty_prefix_keeps_native_names(_fake_discover: None) -> None:
    """``prefix=""`` must not double up the tool name."""
    ns: dict[str, Any] = {}
    # Run the decorator inside an exec'd module namespace so ``mcp()`` (which writes
    # generated tools into its caller's frame globals) has a namespace we can inspect.
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    # Empty prefix means each MCP tool name is used verbatim.
    assert "browser_navigate" in ns
    assert "search" in ns
    assert "browser_browser_navigate" not in ns


def test_default_prefix_uses_function_name(_fake_discover: None) -> None:
    """No ``prefix`` key -> historical ``<func>_`` prefix (serper_* behaviour)."""
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def serper():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp'}\n",
        ns,
    )
    assert "serper_search" in ns
    assert "serper_browser_navigate" in ns
    assert "search" not in ns


def test_generated_tool_is_async_with_signature(_fake_discover: None) -> None:
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    fn = ns["browser_navigate"]
    assert inspect.iscoroutinefunction(fn)
    assert "url" in inspect.signature(fn).parameters


# ── discovery-failure containment (regression: crashed the gateway) ───────────
#
# When the MCP server is unreachable or errors during discovery (e.g. Playwright MCP
# returning HTTP 502), the failure used to propagate — sometimes as a
# ``BaseExceptionGroup`` from the anyio/httpx teardown — past the tool loader's
# ``except Exception`` and take the whole gateway process down. The ``@mcp`` decorator
# must instead log and register no tools, so the rest of the workspace keeps loading.


def test_discovery_failure_is_contained(monkeypatch: pytest.MonkeyPatch, _bypass_schema_cache: None) -> None:
    """A plain Exception during discovery must not escape ``@mcp``; no tools registered."""

    def _boom(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise RuntimeError("Server error '502 Bad Gateway'")

    monkeypatch.setattr(_mcp, "_discover", _boom)
    ns: dict[str, Any] = {}
    # Must not raise.
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    assert "browser_navigate" not in ns
    assert "browser" in ns  # the decorated declaration itself is still returned


def test_discovery_base_exception_group_is_contained(
    monkeypatch: pytest.MonkeyPatch, _bypass_schema_cache: None
) -> None:
    """The real failure mode: a ``BaseExceptionGroup`` (not an ``Exception``) must be caught.

    ``streamable_http_client`` teardown raised ``BaseExceptionGroup`` /
    ``RuntimeError('Attempted to exit cancel scope in a different task')`` which a plain
    ``except Exception`` cannot catch. ``@mcp`` must still contain it.
    """

    def _boom(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise BaseExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")],
        )

    monkeypatch.setattr(_mcp, "_discover", _boom)
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    assert "browser_navigate" not in ns


def test_fatal_signals_still_propagate(monkeypatch: pytest.MonkeyPatch, _bypass_schema_cache: None) -> None:
    """KeyboardInterrupt / SystemExit must never be swallowed by the containment."""

    def _interrupt(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise KeyboardInterrupt

    monkeypatch.setattr(_mcp, "_discover", _interrupt)
    ns: dict[str, Any] = {}
    with pytest.raises(KeyboardInterrupt):
        exec(
            "from _mcp import mcp\n"
            "@mcp\n"
            "def browser():\n"
            "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
            ns,
        )


def test_is_fatal_classification() -> None:
    assert _mcp._is_fatal(KeyboardInterrupt())
    assert _mcp._is_fatal(SystemExit())
    assert not _mcp._is_fatal(RuntimeError("cancel scope"))
    assert not _mcp._is_fatal(BaseExceptionGroup("g", [RuntimeError("x")]))
    # A group carrying a fatal leaf is fatal.
    assert _mcp._is_fatal(BaseExceptionGroup("g", [KeyboardInterrupt()]))


def test_failed_tool_call_returns_error_string(_fake_discover: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed per-call invocation returns an ``Error:`` string, not a raised exception."""
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    fn = ns["browser_navigate"]

    def _boom(_config: dict[str, Any]) -> Any:
        # Mimic the transport teardown surfacing a BaseExceptionGroup (not an Exception).
        raise BaseExceptionGroup("teardown", [RuntimeError("peer closed connection")])

    monkeypatch.setattr(_mcp, "_connect", _boom)

    async def _call() -> str:
        return await fn(url="http://example.com")

    result = anyio.run(_call)
    assert result.startswith("Error:")
    assert "browser_navigate" in result


# ── _browser_impl command + error handling ───────────────────────────────────


def test_build_command_defaults() -> None:
    cmd = _browser_impl._build_command("npx", 12345)
    assert cmd[:3] == ["npx", "-y", _browser_impl._MCP_PACKAGE]
    assert "--port" in cmd and "12345" in cmd
    assert "--browser" in cmd
    assert "--shared-browser-context" in cmd
    # caps default is vision,devtools
    assert "--caps" in cmd
    # The profile is pinned so a stale lock can be cleared before launch.
    assert "--user-data-dir" in cmd


def test_build_command_headed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (env unset) is headed so the user can watch the agent drive the browser."""
    monkeypatch.delenv("BROWSER_HEADLESS", raising=False)
    assert "--headless" not in _browser_impl._build_command("npx", 1)


def test_build_command_headless_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Opt in to headless for displayless hosts / CI.
    monkeypatch.setenv("BROWSER_HEADLESS", "1")
    assert "--headless" in _browser_impl._build_command("npx", 1)
    # Anything else stays headed.
    monkeypatch.setenv("BROWSER_HEADLESS", "0")
    assert "--headless" not in _browser_impl._build_command("npx", 1)


def test_find_npx_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_browser_impl.shutil, "which", lambda _name: None)
    with pytest.raises(_browser_impl.BrowserServerError, match="npx"):
        _browser_impl._find_npx()


def test_ensure_server_propagates_missing_npx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_browser_impl.shutil, "which", lambda _name: None)
    # ensure no stale state short-circuits the check
    monkeypatch.setattr(_browser_impl, "_proc", None)
    monkeypatch.setattr(_browser_impl, "_endpoint", None)
    with pytest.raises(_browser_impl.BrowserServerError):
        _browser_impl.ensure_server()


# ── self-healing: a broken server must not be reused forever ─────────────────


class _FakeProc:
    """Stand-in for the server process; ``code`` is what ``poll()`` reports."""

    def __init__(self, code: int | None = None) -> None:
        self.pid = 4242
        self._code = code
        self.killed = False

    def poll(self) -> int | None:
        return self._code


def test_config_provider_reruns_declaration_every_call(_fake_discover: None) -> None:
    """Regression: the resolved config must NOT be memoized for the process lifetime.

    The declaration body is where a supervised server is (re)started — ``browser`` calls
    ``ensure_server()``. Caching the first result removed the only code path that could
    notice a dead server, so the browser tools stayed broken until a gateway restart.
    """
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "calls = []\n"
        "@mcp\n"
        "def browser():\n"
        "    calls.append(1)\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    # Import-time discovery is faked, so the declaration body has not run for a call yet.
    ns["calls"].clear()

    captured: list[dict[str, Any]] = []

    class _Sess:
        async def __aenter__(self) -> Any:
            raise RuntimeError("connect refused")

        async def __aexit__(self, *_a: object) -> None:
            return None

    def _connect(config: dict[str, Any]) -> Any:
        captured.append(config)
        return _Sess()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mcp, "_connect", _connect)

        async def _call() -> str:
            fn = ns["browser_navigate"]
            return await fn(url="http://example.com") + await fn(url="http://example.com")

        anyio.run(_call)

    assert len(ns["calls"]) == 2, "declaration body must re-run on every tool call"
    assert len(captured) == 2


def test_ensure_server_replaces_unhealthy_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live-but-unhealthy server is torn down and replaced, not handed back."""
    stale = _FakeProc(code=None)  # process alive...
    monkeypatch.setattr(_browser_impl, "_proc", stale)
    monkeypatch.setattr(_browser_impl, "_endpoint", "http://localhost:1/mcp")
    # ...but its HTTP listener no longer answers (the "half-dead" server).
    monkeypatch.setattr(_browser_impl, "_is_endpoint_alive", lambda _ep: False)

    terminated: list[Any] = []
    monkeypatch.setattr(_browser_impl, "_terminate_tree", lambda proc: terminated.append(proc))
    # Stop before the real spawn: missing npx is the cheapest way to end the call.
    monkeypatch.setattr(_browser_impl.shutil, "which", lambda _name: None)

    with pytest.raises(_browser_impl.BrowserServerError):
        _browser_impl.ensure_server()

    assert terminated == [stale], "the unhealthy server must be terminated"
    assert _browser_impl._proc is None and _browser_impl._endpoint is None


def test_ensure_server_reuses_healthy_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path still reuses the running server (one browser, not one per call)."""
    proc = _FakeProc(code=None)
    monkeypatch.setattr(_browser_impl, "_proc", proc)
    monkeypatch.setattr(_browser_impl, "_endpoint", "http://localhost:7/mcp")
    monkeypatch.setattr(_browser_impl, "_is_endpoint_alive", lambda _ep: True)

    def _boom(_name: str) -> None:
        raise AssertionError("must not try to start a second server")

    monkeypatch.setattr(_browser_impl.shutil, "which", _boom)
    assert _browser_impl.ensure_server() == "http://localhost:7/mcp"


def test_ensure_server_replaces_exited_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that exited is replaced even though the endpoint string is still set."""
    dead = _FakeProc(code=1)
    monkeypatch.setattr(_browser_impl, "_proc", dead)
    monkeypatch.setattr(_browser_impl, "_endpoint", "http://localhost:1/mcp")
    # Health must not even be consulted for an exited process.
    monkeypatch.setattr(
        _browser_impl, "_is_endpoint_alive", lambda _ep: pytest.fail("should not probe an exited server")
    )
    monkeypatch.setattr(_browser_impl, "_terminate_tree", lambda _proc: None)
    monkeypatch.setattr(_browser_impl.shutil, "which", lambda _name: None)

    with pytest.raises(_browser_impl.BrowserServerError):
        _browser_impl.ensure_server()


def test_is_endpoint_alive_detects_closed_port() -> None:
    """A refused connection is 'dead'; a bound port that answers HTTP is 'alive'."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert _browser_impl._is_endpoint_alive(f"http://localhost:{free_port}/mcp") is False
    # A malformed endpoint is dead rather than an exception.
    assert _browser_impl._is_endpoint_alive("not-a-url") is False


# ── browser profile lock handling ────────────────────────────────────────────


def test_reclaim_profile_clears_stale_lock(tmp_path: Path) -> None:
    """A leftover lock from a dead browser is removed so the profile can be reused."""
    lock = _browser_impl._lock_path(str(tmp_path))
    lock.write_bytes(b"")
    assert _browser_impl._reclaim_profile(str(tmp_path)) is True
    assert not lock.exists()
    # No lock at all is also fine.
    assert _browser_impl._reclaim_profile(str(tmp_path)) is True


def test_launch_profile_falls_back_when_profile_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a live browser still holds the profile, launch on a sibling instead of failing.

    Regression for "Browser is already in use for <dir>" failing every tool call: the
    orphaned browser is left running (it may be showing the user a login/QR page), and the
    new server gets a usable profile of its own.
    """
    primary = tmp_path / "browser-profile"
    monkeypatch.setattr(_browser_impl, "_PROFILE_DIR", str(primary))
    # Simulate "lock is held by a live browser": removal fails.
    monkeypatch.setattr(_browser_impl, "_reclaim_profile", lambda directory: str(directory) != str(primary))

    chosen = _browser_impl._launch_profile_dir()
    assert chosen != str(primary)
    assert chosen.startswith(str(primary))


def test_launch_profile_prefers_stable_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no contention the stable profile wins, so logins/cookies persist."""
    primary = tmp_path / "browser-profile"
    monkeypatch.setattr(_browser_impl, "_PROFILE_DIR", str(primary))
    assert _browser_impl._launch_profile_dir() == str(primary)


# ── error reporting ──────────────────────────────────────────────────────────


def test_describe_unwraps_exception_group() -> None:
    """A TaskGroup wrapper must not hide the real cause.

    The MCP HTTP client runs its transport in an anyio task group, so 'connection refused'
    reached the agent as the useless "unhandled errors in a TaskGroup (1 sub-exception)".
    """
    inner = ConnectionRefusedError("[WinError 10061] connection refused")
    text = _mcp._describe(BaseExceptionGroup("eg", [BaseExceptionGroup("inner", [inner])]))
    assert "ConnectionRefusedError" in text
    assert "10061" in text
    assert "TaskGroup" not in text
    # Duplicate leaves collapse instead of repeating.
    dup = _mcp._describe(BaseExceptionGroup("eg", [ValueError("same"), ValueError("same")]))
    assert dup == "ValueError: same"
    # A plain exception is described as-is.
    assert _mcp._describe(RuntimeError("boom")) == "RuntimeError: boom"


def test_failed_call_error_names_real_cause(_fake_discover: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The string returned to the agent carries the underlying error, not the group text."""
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )

    def _boom(_config: dict[str, Any]) -> Any:
        raise BaseExceptionGroup("teardown", [ConnectionRefusedError("connection refused")])

    monkeypatch.setattr(_mcp, "_connect", _boom)

    async def _call() -> str:
        return await ns["browser_navigate"](url="http://example.com")

    result = anyio.run(_call)
    assert "ConnectionRefusedError" in result
    assert "TaskGroup" not in result
