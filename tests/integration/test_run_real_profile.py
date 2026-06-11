from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from psi_agent.run import run_once


def _require_env(*names: str) -> tuple[str, ...]:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")
    return tuple(os.environ[name] for name in names)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "systems").mkdir(parents=True)
    (workspace / "tools").mkdir()
    (workspace / "systems" / "system.py").write_text(
        textwrap.dedent(
            """\
            async def system_prompt_builder() -> str:
                return "Reply exactly OK for release health checks."
            """
        ),
        encoding="utf8",
    )
    return workspace


@pytest.mark.anyio
async def test_run_profile_real_openai_compatible_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api_key, base_url, model = _require_env(
        "PSI_TEST_OPENAI_API_KEY",
        "PSI_TEST_OPENAI_BASE_URL",
        "PSI_TEST_OPENAI_MODEL",
    )
    monkeypatch.setenv("PSI_REAL_PROFILE_API_KEY", api_key)

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""\
            default_profile = "release"

            [profiles.release]
            ai = "openai-completions"
            model = "{model}"
            base_url = "{base_url}"
            api_key_env = "PSI_REAL_PROFILE_API_KEY"
            """
        ),
        encoding="utf-8-sig",
    )

    result = await run_once(
        workspace=str(_make_workspace(tmp_path)),
        message="Reply exactly OK.",
        profile="release",
        config=str(config_path),
    )

    assert result.had_error is False
    assert result.text.strip() == "OK"
