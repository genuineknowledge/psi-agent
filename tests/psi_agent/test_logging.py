from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

import psi_agent._logging as _logging
from psi_agent._logging import debug_log_path, debug_modules, setup_logging


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Clear both one-shot guards and the env vars around every test."""
    monkeypatch.delenv("PSI_DEBUG_MODULES", raising=False)
    monkeypatch.delenv("PSI_DEBUG_LOG_PATH", raising=False)
    monkeypatch.delenv("PSI_APPDATA", raising=False)
    _logging._handler_id = None
    _logging._file_handler_id = None
    yield
    logger.remove()
    _logging._handler_id = None
    _logging._file_handler_id = None


def _read_debug_log(root: Path) -> str:
    """Concatenate the per-PID debug logs under *root*.

    The filename carries the writer's PID, so the exact name is only known at
    runtime — glob rather than name it.
    """
    files = sorted((root / "logs").glob("psi-debug-*.log"))
    assert files, f"no debug log written under {root / 'logs'}"
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_setup_logging_default_info() -> None:
    handler_id = setup_logging(verbose=False)
    assert isinstance(handler_id, int)
    logger.remove(handler_id)


def test_setup_logging_verbose_debug() -> None:
    handler_id = setup_logging(verbose=True)
    assert isinstance(handler_id, int)
    logger.remove(handler_id)


def test_no_file_sink_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V1: unset ``PSI_DEBUG_MODULES`` must add nothing and create nothing.

    "Default off" is the *absence* of a sink, not a configured value.
    """
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    added: list[Any] = []
    real_add = logger.add
    monkeypatch.setattr(logger, "add", lambda *a, **kw: (added.append((a, kw)), real_add(*a, **kw))[1])

    setup_logging(verbose=False)

    assert len(added) == 1, "only the stderr sink may be installed"
    assert _logging._file_handler_id is None
    assert not (tmp_path / "logs").exists()


def test_file_sink_takes_only_listed_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V2: listed modules land in the file; unlisted DEBUG does not."""
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    monkeypatch.setenv("PSI_DEBUG_MODULES", "tests.psi_agent.test_logging")

    setup_logging(verbose=False)
    assert _logging._file_handler_id is not None

    # A bare ``logger.debug`` here records ``name`` as this test module.
    logger.debug("from-a-listed-module")
    # ``patch`` rewrites ``name`` so one test can stand in for another module.
    unlisted = logger.patch(lambda record: record.update(name="psi_agent.session.agent"))
    unlisted.debug("from-an-unlisted-module")

    # ``enqueue=True`` hands records to a worker; remove() flushes and joins it.
    logger.remove()
    text = _read_debug_log(tmp_path)
    assert "from-a-listed-module" in text
    assert "from-an-unlisted-module" not in text


def test_debug_modules_does_not_change_stderr_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V3: the stderr sink keeps its own level — docker logs must not grow."""
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    monkeypatch.setenv("PSI_DEBUG_MODULES", "psi_agent.ai.server")
    levels: list[Any] = []
    real_add = logger.add

    def spy(sink: Any, **kw: Any) -> int:
        levels.append((sink, kw.get("level")))
        return real_add(sink, **kw)

    monkeypatch.setattr(logger, "add", spy)
    setup_logging(verbose=False)

    stderr_levels = [lvl for sink, lvl in levels if not isinstance(sink, str)]
    assert stderr_levels == ["INFO"]


def test_file_sink_is_one_shot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V4: repeated calls must not stack file sinks (batch mode calls it a lot)."""
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    monkeypatch.setenv("PSI_DEBUG_MODULES", "psi_agent.ai.server")

    first = setup_logging(verbose=False)
    file_id = _logging._file_handler_id
    second = setup_logging(verbose=True)

    assert first == second, "stderr sink stays one-shot: first caller wins"
    assert _logging._file_handler_id == file_id


def test_file_sink_rotation_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V5: rotation and retention are the reason DEBUG is safe to enable."""
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    monkeypatch.setenv("PSI_DEBUG_MODULES", "psi_agent.ai.server")
    captured: dict[str, Any] = {}
    real_add = logger.add

    def spy(sink: Any, **kw: Any) -> int:
        if isinstance(sink, str):
            captured.update(kw)
        return real_add(sink, **kw)

    monkeypatch.setattr(logger, "add", spy)
    setup_logging(verbose=False)

    assert captured["rotation"] == "20 MB"
    assert captured["retention"] == 10
    assert captured["compression"] == "gz"
    assert captured["level"] == "DEBUG"


def test_stderr_removal_does_not_wipe_the_file_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ``setup_logging``'s bare ``logger.remove()`` must run *first*.

    Installing the file sink before it dropped that sink again while leaving
    ``_file_handler_id`` set, so the one-shot guard blocked any retry — the
    process ended up with no DEBUG file at all, silently.
    """
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    monkeypatch.setenv("PSI_DEBUG_MODULES", "tests.psi_agent.test_logging")

    setup_logging(verbose=False)
    logger.debug("survives-setup")
    logger.remove()

    assert "survives-setup" in _read_debug_log(tmp_path)


def test_log_filename_is_per_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two processes in one container must not share a log file.

    Production's ``launch-gateway.sh`` runs ``psi-agent gateway`` and
    ``psi-agent channel feishu`` side by side, and the two modules worth
    observing sit in different processes. Sharing a path drops lines —
    ``enqueue=True`` only serialises writers within a process, and after
    rotation the losers write on into a renamed inode. Measured: 586 of 600
    lines survived with two processes and no rotation at all.
    """
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    monkeypatch.setenv("PSI_DEBUG_MODULES", "tests.psi_agent.test_logging")

    assert debug_log_path().endswith(f"psi-debug-{os.getpid()}.log")

    setup_logging(verbose=False)
    logger.debug("pid-scoped")
    logger.remove()

    written = list((tmp_path / "logs").glob("psi-debug-*.log"))
    assert len(written) == 1
    assert str(os.getpid()) in written[0].name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("   ", []),
        ("psi_agent.ai.server", ["psi_agent.ai.server"]),
        (" a , b ", ["a", "b"]),
        ("a;b", ["a", "b"]),
        ("a,a", ["a"]),
    ],
)
def test_debug_modules_parsing(raw: str, expected: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_DEBUG_MODULES", raw)
    assert debug_modules() == expected


def test_debug_log_path_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit path wins over AppData; AppData wins over platformdirs."""
    pid = os.getpid()
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path))
    assert debug_log_path() == os.path.join(str(tmp_path), "logs", f"psi-debug-{pid}.log")

    monkeypatch.setenv("PSI_DEBUG_LOG_PATH", "/var/log/psi/explicit.log")
    assert debug_log_path() == "/var/log/psi/explicit.log"

    monkeypatch.setenv("PSI_DEBUG_LOG_PATH", "/var/log/psi/psi-{pid}.log")
    assert debug_log_path() == f"/var/log/psi/psi-{pid}.log"

    monkeypatch.delenv("PSI_DEBUG_LOG_PATH")
    monkeypatch.delenv("PSI_APPDATA")
    fallback = debug_log_path()
    assert fallback.endswith(os.path.join("logs", f"psi-debug-{pid}.log"))
    assert "Haitun" in fallback
