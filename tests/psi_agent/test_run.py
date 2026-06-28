from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent import _run
from psi_agent._run import _build, _run_config


class _Dummy:
    def __init__(self, x: int) -> None:
        self.x = x


def test_build_success() -> None:
    obj = _build(_Dummy, {"x": 1})
    assert isinstance(obj, _Dummy)
    assert obj.x == 1


def test_build_failure_reraises() -> None:
    with pytest.raises(TypeError):
        _build(_Dummy, {"y": 2})


@pytest.mark.anyio
async def test_config_not_a_list_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text("foo: bar\n")
    with pytest.raises(ValueError, match="must be a list"):
        await _run_config(cfg)


@pytest.mark.anyio
async def test_empty_config_returns(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text("[]\n")
    await _run_config(cfg)


@pytest.mark.anyio
async def test_missing_type_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text("- session_socket: ./x.sock\n")
    with pytest.raises(KeyError):
        await _run_config(cfg)


@pytest.mark.anyio
async def test_unknown_type_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text("- type: bogus\n")
    with pytest.raises(ValueError, match="Unknown component type"):
        await _run_config(cfg)


@pytest.mark.anyio
async def test_channel_missing_name_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text("- type: channel\n  session_socket: ./x.sock\n")
    with pytest.raises(KeyError):
        await _run_config(cfg)


@pytest.mark.anyio
async def test_unknown_channel_name_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text("- type: channel\n  name: bogus\n")
    with pytest.raises(ValueError, match="Unknown channel name"):
        await _run_config(cfg)


@pytest.mark.anyio
async def test_dispatch_constructs_and_runs_components(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    instances: list = []

    class _FakeComponent:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.ran = False
            instances.append(self)

        async def run(self) -> None:
            self.ran = True

    monkeypatch.setattr(_run, "Ai", _FakeComponent)
    monkeypatch.setattr(_run, "Session", _FakeComponent)
    monkeypatch.setattr(_run, "ChannelRepl", _FakeComponent)

    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "- type: ai\n"
        "  session_socket: ./ai.sock\n"
        "- type: session\n"
        "  ai_socket: ./ai.sock\n"
        "- type: channel\n"
        "  name: repl\n"
        "  session_socket: ./ch.sock\n"
    )
    await _run_config(cfg)
    assert len(instances) == 3
    assert all(c.ran for c in instances)
