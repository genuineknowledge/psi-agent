from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from psi_agent.errors import UserFacingError
from psi_agent.run.config import load_run_profile_config


def test_run_profile_config_accepts_utf8_bom(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            base_url = "https://example.test/v1"
            api_key = "sk-test"
            """
        ),
        encoding="utf-8-sig",
    )

    config = load_run_profile_config(config_path=str(config_path))

    assert config.ai == "openai-completions"
    assert config.model == "profile-model"
    assert config.base_url == "https://example.test/v1"
    assert config.api_key == "sk-test"


def test_run_profile_config_reports_missing_api_key_env(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            base_url = "https://example.test/v1"
            api_key_env = "PSI_TEST_MISSING_KEY"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserFacingError, match="Environment variable is not set"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_preserves_api_key_env_when_not_required(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            api_key_env = "PSI_TEST_MISSING_KEY"
            """
        ),
        encoding="utf-8",
    )

    config = load_run_profile_config(config_path=str(config_path), require_api_key_env=False)

    assert config.api_key_env == "PSI_TEST_MISSING_KEY"
    assert config.api_key == ""


def test_run_profile_config_reports_invalid_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text('default_profile = "fusion"\n[profiles.fusion\n', encoding="utf-8")

    with pytest.raises(UserFacingError, match="not valid TOML"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_reports_non_utf8_config(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(UserFacingError, match="not UTF-8 text"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_reports_non_string_field(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = 123
            api_key = "sk-test"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserFacingError, match="field must be a string: model"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_reports_invalid_ai(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "bad-backend"
            api_key = "sk-test"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserFacingError, match='field "ai" must be one of'):
        load_run_profile_config(config_path=str(config_path))

