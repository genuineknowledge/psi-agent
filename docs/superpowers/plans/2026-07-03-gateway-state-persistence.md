# Gateway State Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Gateway's AI/Session/Title state to `state/latest.json` on every mutation, and automatically restore on startup.

**Architecture:** New `GatewayState` dataclass handles JSON file I/O. Each manager gains a `_persist` callback — a closure injected by `Gateway.run()` that snapshots all three managers and writes to disk. On startup, load state first, restore entities, then inject the callback.

**Tech Stack:** `anyio.Path` (async file I/O), `json` (stdlib), existing patterns (dataclass + closure injection)

---

### Task 1: GatewayState — new module

**Files:**
- Create: `src/psi_agent/gateway/_state.py`
- Create: `tests/psi_agent/gateway/test_state.py`

- [ ] **Step 1: Write failing tests for GatewayState**

Write `tests/psi_agent/gateway/test_state.py`:

```python
from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._state import GatewayState


@pytest.mark.anyio
async def test_state_save_and_load_roundtrip(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "state" / "latest.json")

    await state.save(
        ais=[
            {"id": "a1", "provider": "openai", "model": "gpt-4o", "api_key": "sk-abc", "base_url": "https://api.oai.com"},
            {"id": "a2", "provider": "anthropic", "model": "claude-3", "api_key": "sk-xyz", "base_url": ""},
        ],
        sessions=[
            {"id": "s1", "ai_id": "a1", "workspace": "/tmp/ws"},
        ],
        titles={"s1": "Hello Chat"},
    )

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 2
    assert snapshot["ais"]["a1"]["provider"] == "openai"
    assert snapshot["ais"]["a1"]["api_key"] == "sk-abc"
    assert snapshot["ais"]["a2"]["base_url"] == ""
    assert len(snapshot["sessions"]) == 1
    assert snapshot["sessions"]["s1"]["ai_id"] == "a1"
    assert snapshot["sessions"]["s1"]["workspace"] == "/tmp/ws"
    assert snapshot["titles"] == {"s1": "Hello Chat"}


@pytest.mark.anyio
async def test_state_load_missing_file_returns_empty(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "nonexistent" / "latest.json")
    snapshot = await state.load()
    assert snapshot == {"ais": {}, "sessions": {}, "titles": {}}


@pytest.mark.anyio
async def test_state_overwrite_on_save(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "state" / "latest.json")

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k1", "base_url": ""}],
        sessions=[],
        titles={},
    )
    await state.save(
        ais=[{"id": "a2", "provider": "x", "model": "y", "api_key": "k2", "base_url": ""}],
        sessions=[],
        titles={},
    )

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 1
    assert "a2" in snapshot["ais"]
    assert "a1" not in snapshot["ais"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/psi_agent/gateway/test_state.py -v`
Expected: 3 FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement `GatewayState`**

Write `src/psi_agent/gateway/_state.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anyio
from loguru import logger


@dataclass
class GatewayState:
    _path: anyio.Path

    async def load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = await self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"State file {self._path!s} not found, starting fresh")
            return {"ais": {}, "sessions": {}, "titles": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"State file {self._path!s} is corrupt, starting fresh")
            return {"ais": {}, "sessions": {}, "titles": {}}
        if not isinstance(data, dict):
            logger.warning(f"State file {self._path!s} is not a dict, starting fresh")
            return {"ais": {}, "sessions": {}, "titles": {}}
        return {
            "ais": data.get("ais", {}),
            "sessions": data.get("sessions", {}),
            "titles": data.get("titles", {}),
        }

    async def save(
        self,
        ais: list[dict[str, str]],
        sessions: list[dict[str, str]],
        titles: dict[str, str],
    ) -> None:
        data = {
            "ais": {a["id"]: {"provider": a["provider"], "model": a["model"], "api_key": a["api_key"], "base_url": a["base_url"]} for a in ais},
            "sessions": {s["id"]: {"ai_id": s["ai_id"], "workspace": s["workspace"]} for s in sessions},
            "titles": dict(titles),
        }
        try:
            await self._path.parent.mkdir(parents=True, exist_ok=True)
            await self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug(f"State saved to {self._path!s}")
        except Exception as e:
            logger.warning(f"Failed to save state to {self._path!s}: {e!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/psi_agent/gateway/test_state.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/gateway/_state.py tests/psi_agent/gateway/test_state.py
git commit -m "feat: add GatewayState for state/latest.json persistence"
```

---

### Task 2: AIManager — add api_key/base_url to entries, \_persist, relax get\_socket

**Files:**
- Modify: `src/psi_agent/gateway/_ai_manager.py`
- Modify: `tests/psi_agent/gateway/test_manager.py`

- [ ] **Step 1: Add api_key and base_url to \_AiEntry and AiInfo**

Add `api_key` and `base_url` to `_AiEntry` dataclass:
```python
@dataclass
class _AiEntry:
    scope: anyio.CancelScope
    socket: str
    provider: str
    model: str
    api_key: str
    base_url: str
```

Add `api_key` and `base_url` to `AiInfo` dataclass:
```python
@dataclass
class AiInfo:
    id: str
    socket: str
    provider: str
    model: str
    api_key: str
    base_url: str
```

In `create()`, update the `_AiEntry(...)` construction to include `api_key` and `base_url`:
```python
            self._entries[ai_id] = _AiEntry(scope=scope, socket=socket, provider=provider, model=model, api_key=api_key, base_url=base_url)
```

In `create()`, update the `AiInfo(...)` return to include `api_key` and `base_url`:
```python
        return AiInfo(id=ai_id, socket=socket, provider=provider, model=model, api_key=api_key, base_url=base_url)
```

In `list_all()`, update list comprehension:
```python
            AiInfo(id=aid, socket=e.socket, provider=e.provider, model=e.model, api_key=e.api_key, base_url=e.base_url)
```

Update existing tests that construct `AiInfo` or `_AiEntry` to include the two new fields.

- [ ] **Step 2: Add \_persist field and call it in create/delete/crash**

In `src/psi_agent/gateway/_ai_manager.py`, add import:

Add to imports (after `from loguru import logger`):
```python
from collections.abc import Awaitable, Callable
```

Modify `AIManager` dataclass fields — add after `_lock`:
```python
    _persist: Callable[[], Awaitable[None]] | None = None
```

In `create()`, before `return AiInfo(...)`:
```python
        if self._persist is not None:
            await self._persist()
        logger.info(f"AI {ai_id!r} created on {socket}")
```

In `create()`, in the rollback `except` block, after `await _remove_socket(socket)`:
```python
                if self._persist is not None:
                    await self._persist()
```

In `delete()`, before `logger.info(...)`:
```python
            if self._persist is not None:
                await self._persist()
            logger.info(f"AI {ai_id!r} deleted")
```

In `_run_ai` crash handler, after `self._entries.pop(ai_id, None)`:
```python
                        if self._persist is not None:
                            await self._persist()
```

- [ ] **Step 3: Change `get_socket` to compute path when AI not in entries**

Replace `get_socket`:
```python
    def get_socket(self, ai_id: str) -> str:
        if ai_id in self._entries:
            return self._entries[ai_id].socket
        return _socket_path(self._prefix, "ais", ai_id)
```

- [ ] **Step 4: Update tests — get\_socket no longer raises LookupError**

In `tests/psi_agent/gateway/test_manager.py`, in `test_aimanager_has_and_get_socket`, replace the `with pytest.raises(LookupError):` block with:
```python
        socket = mgr.get_socket("nonexistent")
        assert socket.endswith(".sock")
```

- [ ] **Step 5: Add test for \_persist callback being called**

Add to `tests/psi_agent/gateway/test_manager.py`:

```python
@pytest.mark.anyio
async def test_aimanager_persist_called_on_create_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    call_count = 0

    async def fake_persist() -> None:
        nonlocal call_count
        call_count += 1

    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg, _persist=fake_persist)
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b")
        assert call_count == 1
        await mgr.delete(info.id)
        assert call_count == 2
    finally:
        await tg.__aexit__(None, None, None)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/psi_agent/gateway/test_manager.py -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/psi_agent/gateway/_ai_manager.py tests/psi_agent/gateway/test_manager.py
git commit -m "feat: add api_key/base_url to AiEntry/AiInfo, _persist callback to AIManager, relax get_socket"
```

---

### Task 3: SessionManager — add \_persist callback, remove AI existence check

**Files:**
- Modify: `src/psi_agent/gateway/_session_manager.py`
- Modify: `tests/psi_agent/gateway/test_manager.py`

- [ ] **Step 1: Add \_persist field and call it in create/delete/crash**

In `src/psi_agent/gateway/_session_manager.py`, add import:
```python
from collections.abc import Awaitable, Callable
```

Add field after `_lock` in `SessionManager`:
```python
    _persist: Callable[[], Awaitable[None]] | None = None
```

In `create()`, remove the `if not self._aim.has(ai_id): raise LookupError(...)` block.

In `create()`, before `return SessionInfo(...)`:
```python
        if self._persist is not None:
            await self._persist()
        logger.info(f"Session {session_id!r} created on {channel_socket} -> AI '{ai_id}'")
```

In `create()`, in the rollback `except` block, after `await _remove_socket(channel_socket)`:
```python
                if self._persist is not None:
                    await self._persist()
```

In `delete()`, before `logger.info(...)`:
```python
            if self._persist is not None:
                await self._persist()
            logger.info(f"Session {session_id!r} deleted")
```

In `_run_session` crash handler, after `self._entries.pop(session_id, None)`:
```python
                        if self._persist is not None:
                            await self._persist()
```

- [ ] **Step 2: Update tests — remove test for missing AI, add persist test**

In `tests/psi_agent/gateway/test_manager.py`, remove `test_sessionmanager_missing_ai` entirely.

Add new tests:
```python
@pytest.mark.anyio
async def test_sessionmanager_create_without_ai(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)
        info = await sm.create(ai_id="no-such-ai", workspace=str(tmp_path), id="s1")
        assert info.ai_id == "no-such-ai"
        await sm.delete("s1")
    finally:
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_sessionmanager_persist_called_on_create_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    call_count = 0

    async def fake_persist() -> None:
        nonlocal call_count
        call_count += 1

    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg, _persist=fake_persist)
        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert call_count == 1
        await sm.delete(info.id)
        assert call_count == 2
    finally:
        await tg.__aexit__(None, None, None)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/psi_agent/gateway/test_manager.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/gateway/_session_manager.py tests/psi_agent/gateway/test_manager.py
git commit -m "feat: add _persist callback to SessionManager, remove AI existence check at create"
```

---

### Task 4: TitleManager — add \_persist callback, set() → async

**Files:**
- Modify: `src/psi_agent/gateway/_title_manager.py`
- Modify: `src/psi_agent/gateway/server.py`

- [ ] **Step 1: Add \_persist to TitleManager, make set() async**

In `src/psi_agent/gateway/_title_manager.py`, add import:
```python
from collections.abc import Awaitable, Callable
```

Modify `__init__`:
```python
    def __init__(self, _persist: Callable[[], Awaitable[None]] | None = None) -> None:
        self._titles: dict[str, str] = {}
        self._persist = _persist
```

Rename `set` to `async def set`:
```python
    async def set(self, session_id: str, title: str) -> None:
        self._titles[session_id] = title
        if self._persist is not None:
            await self._persist()
```

In `generate()`, after `self._titles[session_id] = title`:
```python
                if title:
                    self._titles[session_id] = title
                    if self._persist is not None:
                        await self._persist()
                    return title
```

- [ ] **Step 2: Update server.py — await tm.set()**

In `src/psi_agent/gateway/server.py`, in `_set_title`:
```python
        sid = body["id"]
        await tm.set(sid, body["title"])
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `uv run pytest tests/psi_agent/gateway/ -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/gateway/_title_manager.py src/psi_agent/gateway/server.py
git commit -m "feat: add _persist callback to TitleManager, make set() async"
```

---

### Task 5: Gateway.run() — orchestrate load/restore/inject

**Files:**
- Modify: `src/psi_agent/gateway/__init__.py`

- [ ] **Step 1: Rewrite Gateway.run() to include state persistence**

Replace the `run()` method:

```python
    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        state = GatewayState(_path=anyio.Path("state/latest.json"))
        snapshot = await state.load()

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

            for ai_id, cfg in snapshot.get("ais", {}).items():
                try:
                    await aim.create(
                        provider=cfg.get("provider", ""),
                        model=cfg.get("model", ""),
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        id=ai_id,
                    )
                    logger.info(f"Restored AI {ai_id!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore AI {ai_id!r}: {e!r}")

            for sess_id, cfg in snapshot.get("sessions", {}).items():
                try:
                    await sm.create(
                        ai_id=cfg.get("ai_id", ""),
                        workspace=cfg.get("workspace", ""),
                        id=sess_id,
                    )
                    logger.info(f"Restored Session {sess_id!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore Session {sess_id!r}: {e!r}")

            app = await create_app(aim, sm, favicon_path=self.tray)
            tm: TitleManager = app["tm"]

            async def _do_persist() -> None:
                ais_snapshot = [{"id": info.id, "provider": info.provider, "model": info.model, "api_key": info.api_key, "base_url": info.base_url} for info in await aim.list_all()]  # noqa: E501
                sessions_snapshot = [{"id": info.id, "ai_id": info.ai_id, "workspace": info.workspace} for info in await sm.list_all()]  # noqa: E501
                await state.save(
                    ais=ais_snapshot,
                    sessions=sessions_snapshot,
                    titles=tm.get_all(),
                )

            aim._persist = _do_persist
            sm._persist = _do_persist
            tm._persist = _do_persist

            for sid, title in snapshot.get("titles", {}).items():
                await tm.set(sid, title)

            await _do_persist()

            runner = web.AppRunner(app)
            try:
                try:
                    await runner.setup()
                    site = create_site(runner, addr)
                    await site.start()
                except Exception as e:
                    logger.error(f"Failed to start Gateway on {addr}: {e!r}")
                    raise

                logger.info(f"Gateway listening on {addr}")

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    tray = GatewayTray(addr, self.tray)
                    try:
                        tray.start()
                    except Exception as e:
                        logger.warning(f"Failed to start system tray: {e!r}")

                try:
                    if tray is not None and tray.is_running():
                        await anyio.to_thread.run_sync(tray.wait_stop, abandon_on_cancel=True)  # ty: ignore
                    else:
                        await anyio.sleep_forever()
                finally:
                    if tray is not None:
                        tray.stop()
            finally:
                logger.info("Shutting down Gateway")
                with anyio.CancelScope(shield=True):
                    await runner.cleanup()
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
```

Add import at top:
```python
from psi_agent.gateway._state import GatewayState
```

And import `TitleManager`:
```python
from psi_agent.gateway._title_manager import TitleManager
```

- [ ] **Step 2: Handle rollback persist in AIManager create()**

In `_ai_manager.py`, the `create()` method rollback block currently does:
```python
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(ai_id, None)
                    scope.cancel()
                    await _remove_socket(socket)
```

Add `if self._persist is not None: await self._persist()` after `await _remove_socket(socket)`:
```python
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    self._entries.pop(ai_id, None)
                    scope.cancel()
                    await _remove_socket(socket)
                    if self._persist is not None:
                        await self._persist()
```

- [ ] **Step 3: Handle rollback persist in SessionManager create()**

Same change in `_session_manager.py` rollback block — after `await _remove_socket(channel_socket)`:
```python
                    if self._persist is not None:
                        await self._persist()
```

- [ ] **Step 4: Run lint + typecheck**

Run: `uv run ruff check src/psi_agent/gateway/ && uv run ty check src/psi_agent/gateway/`
Expected: no errors

- [ ] **Step 5: Run all gateway tests**

Run: `uv run pytest tests/psi_agent/gateway/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/gateway/__init__.py src/psi_agent/gateway/_ai_manager.py src/psi_agent/gateway/_session_manager.py
git commit -m "feat: orchestrate Gateway state load/restore/persist in run()"
```

---

### Task 6: Integration test — full lifecycle with persistence

**Files:**
- Create: `tests/integration/test_gateway_persistence.py`
- Check: `tests/integration/conftest.py` for patterns

- [ ] **Step 1: Write integration test**

Write `tests/integration/test_gateway_persistence.py`:

```python
from __future__ import annotations

import json

import aiohttp
import anyio
import pytest

from psi_agent.gateway._manager import _socket_path
from psi_agent.gateway._state import GatewayState


@pytest.mark.anyio
async def test_gateway_state_persistence_roundtrip(tmp_path: str) -> None:
    """Create AI/Session via managers, verify state file, then verify load returns same data."""
    state_path = anyio.Path(tmp_path) / "state" / "latest.json"
    state = GatewayState(_path=state_path)

    ais_data = {
        "a1": {"id": "a1", "provider": "openai", "model": "gpt-4o", "api_key": "sk-abc", "base_url": "https://api.oai.com"},
    }
    sessions_data = {
        "s1": {"id": "s1", "ai_id": "a1", "workspace": str(tmp_path)},
    }
    titles_data = {"s1": "Test Chat"}

    await state.save(
        ais=list(ais_data.values()),
        sessions=list(sessions_data.values()),
        titles=titles_data,
    )

    raw = await state_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["ais"]["a1"]["provider"] == "openai"
    assert parsed["sessions"]["s1"]["ai_id"] == "a1"
    assert parsed["titles"]["s1"] == "Test Chat"

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 1
    assert len(snapshot["sessions"]) == 1
    assert len(snapshot["titles"]) == 1


@pytest.mark.anyio
async def test_gateway_state_corrupt_json_falls_back(tmp_path: str) -> None:
    state_path = anyio.Path(tmp_path) / "state" / "latest.json"
    await state_path.parent.mkdir(parents=True)
    await state_path.write_text("{invalid json", encoding="utf-8")

    state = GatewayState(_path=state_path)
    snapshot = await state.load()
    assert snapshot == {"ais": {}, "sessions": {}, "titles": {}}


@pytest.mark.anyio
async def test_aim_get_socket_computes_for_unknown_id() -> None:
    socket = _socket_path("test", "ais", "unknown-id")
    assert socket.endswith(".sock")
    assert "unknown-id" in socket
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/integration/test_gateway_persistence.py -v`
Expected: 3 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_gateway_persistence.py
git commit -m "test: add Gateway state persistence integration tests"
```

---

### Task 7: Final verification

- [ ] **Step 1: Full lint check**

Run: `uv run ruff check .`
Expected: no errors

- [ ] **Step 2: Format check**

Run: `uv run ruff format --check .`
Expected: no issues

- [ ] **Step 3: Type check**

Run: `uv run ty check`
Expected: no new errors

- [ ] **Step 4: Full test suite**

Run: `uv run pytest -v`
Expected: all PASS
