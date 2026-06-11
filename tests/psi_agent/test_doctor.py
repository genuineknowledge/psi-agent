from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from psi_agent.doctor import Doctor


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "systems").mkdir(parents=True)
    (workspace / "tools").mkdir()
    (workspace / "skills" / "fusion-flow").mkdir(parents=True)
    (workspace / "systems" / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )
    (workspace / "tools" / "echo.py").write_text(
        "async def echo(message: str) -> str:\n    return message\n",
        encoding="utf-8",
    )
    (workspace / "skills" / "fusion-flow" / "SKILL.md").write_text("# Fusion Flow\n", encoding="utf-8")
    return workspace


@pytest.mark.anyio
async def test_doctor_reports_ready_workspace_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _make_workspace(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "test-model"
            base_url = "https://example.test/v1"
            api_key_env = "PSI_TEST_DOCTOR_KEY"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PSI_TEST_DOCTOR_KEY", "sk-test")

    await Doctor(workspace=str(workspace), config=str(config_path)).run()

    output = capsys.readouterr().out
    assert "psi-agent doctor" in output
    assert "[OK] Workspace" in output
    assert "[OK] API key: configured (hidden)" in output
    assert "Result: ready." in output


@pytest.mark.anyio
async def test_doctor_uses_default_workspace_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _make_workspace(tmp_path)
    config_path = tmp_path / "config.toml"
    workspace_toml = str(workspace).replace("\\", "\\\\")
    config_path.write_text(
        textwrap.dedent(
            f"""\
            default_profile = "fusion"
            default_workspace = "{workspace_toml}"

            [profiles.fusion]
            ai = "openai-completions"
            model = "test-model"
            base_url = "https://example.test/v1"
            api_key_env = "PSI_TEST_DOCTOR_KEY"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PSI_TEST_DOCTOR_KEY", "sk-test")

    await Doctor(config=str(config_path)).run()

    output = capsys.readouterr().out
    assert "[OK] Workspace" in output
    assert "not checked" not in output
    assert "Result: ready." in output
