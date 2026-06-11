from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.errors import UserFacingError
from psi_agent.init import Init
from psi_agent.run.config import load_run_profile_config


@pytest.mark.anyio
async def test_init_creates_config_and_default_workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"

    await Init(config=str(config_path), workspace=str(workspace), api_key_env="PSI_TEST_KEY").run()

    profile = load_run_profile_config(
        config_path=str(config_path),
        require_api_key_env=False,
    )
    assert profile.ai == "openai-completions"
    assert profile.model == "gpt-4o-mini"
    assert profile.base_url == "https://api.openai.com/v1"
    assert profile.workspace == str(workspace.resolve())
    assert (workspace / "systems" / "system.py").exists()
    assert (workspace / "tools" / ".gitkeep").exists()
    assert (workspace / "skills" / ".gitkeep").exists()

    output = capsys.readouterr().out
    assert "Created starter config and workspace." in output
    assert "Next step: set PSI_TEST_KEY" in output


@pytest.mark.anyio
async def test_init_keeps_existing_config_without_force(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    config_path.write_text("existing = true\n", encoding="utf-8")

    await Init(config=str(config_path), workspace=str(workspace)).run()

    assert config_path.read_text(encoding="utf-8") == "existing = true\n"
    assert (workspace / "systems" / "system.py").exists()


@pytest.mark.anyio
async def test_init_rejects_invalid_profile_name(tmp_path: Path) -> None:
    with pytest.raises(UserFacingError, match="Invalid profile name"):
        await Init(config=str(tmp_path / "config.toml"), profile="bad profile").run()


@pytest.mark.anyio
async def test_init_supports_anthropic_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"

    await Init(config=str(config_path), workspace=str(workspace), ai="anthropic-messages").run()

    profile = load_run_profile_config(config_path=str(config_path), require_api_key_env=False)
    assert profile.ai == "anthropic-messages"
    assert profile.model == "claude-sonnet-4-5"
    assert profile.base_url == "https://api.anthropic.com/v1"


@pytest.mark.anyio
async def test_init_rejects_config_directory(tmp_path: Path) -> None:
    config_dir = tmp_path / "config-dir"
    config_dir.mkdir()

    with pytest.raises(UserFacingError, match="Config path is a directory"):
        await Init(config=str(config_dir), workspace=str(tmp_path / "workspace"), force=True).run()


@pytest.mark.anyio
async def test_init_rejects_workspace_file(tmp_path: Path) -> None:
    workspace_file = tmp_path / "workspace-file"
    workspace_file.write_text("not a directory", encoding="utf-8")

    with pytest.raises(UserFacingError, match="Workspace path is not a directory"):
        await Init(config=str(tmp_path / "config.toml"), workspace=str(workspace_file)).run()


@pytest.mark.anyio
async def test_init_rejects_generated_file_path_that_is_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "systems" / "system.py").mkdir(parents=True)

    with pytest.raises(UserFacingError, match="Cannot write file"):
        await Init(config=str(tmp_path / "config.toml"), workspace=str(workspace), force=True).run()
