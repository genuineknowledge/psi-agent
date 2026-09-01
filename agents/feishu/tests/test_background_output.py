"""Background processes must hand their output back to the agent.

Before this, ``background_start`` spawned with ``stdout=DEVNULL``, so the one
path that is not bound by the ``bash`` timeout was also the one path that could
not report anything. That made long work (API paging loops) effectively
unrunnable: the foreground call gets killed at the limit, and the background
call succeeds silently into nowhere.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_bg_start: Any = importlib.import_module("background_start")
_bg_stop: Any = importlib.import_module("background_stop")
_reg: Any = importlib.import_module("_background_process_registry")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway workspace. ``WORKSPACE_DIR`` is what ``_runtime_paths`` reads."""
    resolved = tmp_path.resolve()
    monkeypatch.setenv("WORKSPACE_DIR", str(resolved))
    return resolved


@pytest.fixture
async def started(workspace: Path) -> AsyncIterator[Callable[..., Awaitable[dict[str, Any]]]]:
    """Start processes, and stop whatever is still alive when the test ends."""
    ids: list[str] = []

    async def _launch(command: str, process_id: str = "") -> dict[str, Any]:
        result = json.loads(await _bg_start.background_start(command, process_id=process_id))
        if result.get("process_id"):
            ids.append(result["process_id"])
        return result

    yield _launch

    for bg_id in ids:
        with anyio.CancelScope(shield=True):
            await _bg_stop.background_stop(bg_id)


def _script(path: Path, body: str) -> str:
    """Write *body* to *path*; return a shell command that runs it."""
    path.write_text(body, encoding="utf-8")
    return f'python "{path.as_posix()}"'


async def _output(process_id: str, **kwargs: Any) -> dict[str, Any]:
    return json.loads(await _bg_stop.background_output(process_id, **kwargs))


async def _wait_until(check: Callable[[], Awaitable[bool]], *, seconds: float = 25.0) -> bool:
    """Poll *check* rather than guessing a sleep long enough for a subprocess.

    Polling and not an ``anyio.Event`` because there is nothing to signal it:
    the writer is a detached OS process appending to a file, and the filesystem
    does not notify us. Same class of exception as the thread polling in
    ``_fusion_memory_mcp.py``.
    """
    with anyio.move_on_after(seconds):
        while not await check():  # noqa: ASYNC110 - detached process writing a file; nothing to await on
            await anyio.sleep(0.2)
        return True
    return False


def _contains(process_id: str, needle: str, **kwargs: Any) -> Callable[[], Awaitable[bool]]:
    async def _check() -> bool:
        snapshot = await _output(process_id, **kwargs)
        return needle in (snapshot.get("output") or "")

    return _check


# ── output is captured at all ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_reports_where_the_output_goes(workspace: Path, started: Any) -> None:
    result = await started(_script(workspace / "s.py", "print('hi')\n"), "p1")

    assert result["ok"] is True
    assert result["log_path"]
    assert "background_output" in result["message"]


@pytest.mark.asyncio
async def test_output_is_readable_while_still_running(workspace: Path, started: Any) -> None:
    body = "import time\nfor i in range(20):\n    print('page', i, flush=True)\n    time.sleep(0.5)\n"
    await started(_script(workspace / "s.py", body), "run1")

    assert await _wait_until(_contains("run1", "page 0")), "nothing readable while the process ran"

    snapshot = await _output("run1")
    assert snapshot["ok"] is True
    assert snapshot["alive"] is True


@pytest.mark.asyncio
async def test_output_survives_the_process_and_its_registry_record(workspace: Path, started: Any) -> None:
    """A finished process is pruned from the registry; its log must remain."""
    await started(_script(workspace / "s.py", "print('done-marker')\n"), "fin1")

    assert await _wait_until(_contains("fin1", "done-marker"))

    # Force a prune, then confirm the output is still retrievable.
    await _bg_stop.background_list()
    snapshot = await _output("fin1")

    assert snapshot["ok"] is True
    assert snapshot["alive"] is False
    assert "done-marker" in snapshot["output"]


@pytest.mark.asyncio
async def test_stderr_is_captured_too(workspace: Path, started: Any) -> None:
    body = "import sys\nprint('OUT')\nprint('ERR', file=sys.stderr)\n"
    await started(_script(workspace / "s.py", body), "err1")

    assert await _wait_until(_contains("err1", "OUT"))
    assert await _wait_until(_contains("err1", "ERR")), "stderr was not merged into the log"


@pytest.mark.asyncio
async def test_immediate_failure_still_explains_itself(workspace: Path, started: Any) -> None:
    """A command that dies on startup used to lose its reason to DEVNULL."""
    await started('python -c "print(1/0)"', "boom1")

    assert await _wait_until(_contains("boom1", "ZeroDivisionError"))


@pytest.mark.asyncio
async def test_large_output_does_not_stall_the_child(workspace: Path, started: Any) -> None:
    """The sink is a file, not a pipe.

    A pipe nobody drains fills at ~64KB and blocks the writer, which for a long
    job looks exactly like a hang partway through.
    """
    body = "for i in range(4000):\n    print('x' * 60)\nprint('TAIL-MARKER')\n"
    await started(_script(workspace / "s.py", body), "big1")

    reached_end = await _wait_until(_contains("big1", "TAIL-MARKER", tail_lines=3), seconds=45)
    assert reached_end, "output stopped early — the child was probably blocked on a full pipe"

    snapshot = await _output("big1", tail_lines=0, max_chars=0)
    assert snapshot["total_lines"] > 4000


# ── truncation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tail_lines_keeps_the_end_and_reports_what_it_dropped(workspace: Path, started: Any) -> None:
    await started(_script(workspace / "s.py", "for i in range(50):\n    print('line', i)\n"), "tail1")

    assert await _wait_until(_contains("tail1", "line 49", tail_lines=0))

    snapshot = await _output("tail1", tail_lines=5)
    assert snapshot["total_lines"] == 50
    assert snapshot["omitted_leading_lines"] == 45
    assert "line 49" in snapshot["output"]
    assert "line 0\n" not in snapshot["output"]


@pytest.mark.asyncio
async def test_max_chars_caps_the_returned_text(workspace: Path, started: Any) -> None:
    await started(_script(workspace / "s.py", "for i in range(200):\n    print('y' * 50)\n"), "cap1")

    async def complete() -> bool:
        return (await _output("cap1", tail_lines=0)).get("total_lines", 0) >= 200

    assert await _wait_until(complete)

    snapshot = await _output("cap1", tail_lines=0, max_chars=500)
    assert len(snapshot["output"]) == 500
    assert snapshot["omitted_leading_chars"] > 0


# ── ids and error paths ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b", "..", "with space"])
@pytest.mark.asyncio
async def test_unsafe_process_ids_are_refused(workspace: Path, bad_id: str) -> None:
    """The id becomes a filename, so it is validated rather than rewritten."""
    result = json.loads(await _bg_start.background_start("echo x", process_id=bad_id))

    assert result["ok"] is False
    assert "process_id" in result["message"]


@pytest.mark.asyncio
async def test_unknown_process_id_says_so(workspace: Path) -> None:
    snapshot = await _output("no-such-id")

    assert snapshot["ok"] is False
    assert "no output log" in snapshot["message"]


@pytest.mark.asyncio
async def test_empty_process_id_is_refused(workspace: Path) -> None:
    snapshot = await _output("   ")

    assert snapshot["ok"] is False
    assert "must not be empty" in snapshot["message"]


@pytest.mark.asyncio
async def test_list_exposes_the_log_path(workspace: Path, started: Any) -> None:
    await started(_script(workspace / "s.py", "import time\ntime.sleep(10)\n"), "list1")

    listing = json.loads(await _bg_stop.background_list())
    rows = [row for row in listing["processes"] if row["process_id"] == "list1"]

    assert rows, "started process missing from background_list"
    assert rows[0]["log_path"]


@pytest.mark.asyncio
async def test_reading_output_does_not_prune_the_registry(workspace: Path, started: Any) -> None:
    """Reading is not a mutation: polling output must not evict records."""
    await started(_script(workspace / "s.py", "print('x')\n"), "keep1")

    async def finished() -> bool:
        return (await _output("keep1")).get("alive") is False

    assert await _wait_until(finished)

    registry = json.loads((workspace / ".psi" / "background" / "registry.json").read_text(encoding="utf-8"))
    assert "keep1" in registry["processes"], "read_output pruned the record it only meant to read"


# ── unit-level helpers ────────────────────────────────────────────────────────


def test_invalid_process_id_accepts_generated_ids() -> None:
    assert _reg.invalid_process_id("bg-0123456789abcdef") == ""
    assert _reg.invalid_process_id("job.1_final-2") == ""


def test_invalid_process_id_rejects_path_traversal() -> None:
    assert _reg.invalid_process_id("..") != ""
    assert _reg.invalid_process_id("../x") != ""
    assert _reg.invalid_process_id("a/b") != ""
