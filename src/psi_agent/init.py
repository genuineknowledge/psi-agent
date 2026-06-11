from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from psi_agent._logging import setup_logging
from psi_agent.errors import UserFacingError

Provider = Literal["openai-completions", "anthropic-messages"]


@dataclass
class Init:
    """Create a starter config and default workspace."""

    config: str = ""
    """Config TOML path. Defaults to PSI_AGENT_CONFIG or ~/.psi-agent/config.toml."""

    workspace: str = ""
    """Workspace directory to create. Defaults to ~/.psi-agent/workspaces/default."""

    profile: str = "fusion"
    """Profile name to create."""

    ai: Provider = "openai-completions"
    """AI backend for the starter profile."""

    model: str = ""
    """Model name for the starter profile."""

    base_url: str = ""
    """Base URL for the starter profile."""

    api_key_env: str = ""
    """Environment variable name that stores the API key."""

    force: bool = False
    """Overwrite generated config and workspace files."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        config_path = _resolve_config_path(self.config)
        workspace_path = _resolve_workspace_path(self.workspace)
        profile = self.profile.strip() or "fusion"
        _validate_profile_name(profile)
        api_key_env = self.api_key_env.strip() or _default_api_key_env(self.ai)
        model = self.model.strip() or _default_model(self.ai)
        base_url = self.base_url.strip() or _default_base_url(self.ai)

        _write_workspace(workspace_path, force=self.force)
        config_written = _write_config(
            config_path=config_path,
            workspace_path=workspace_path,
            profile=profile,
            ai=self.ai,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
            force=self.force,
        )

        sys.stdout.write("psi-agent init\n\n")
        sys.stdout.write(f"Workspace: {workspace_path}\n")
        sys.stdout.write(f"Config: {config_path}\n")
        sys.stdout.write(f"Profile: {profile}\n")
        sys.stdout.write(f"API key env: {api_key_env}\n")
        if config_written:
            sys.stdout.write("\nCreated starter config and workspace.\n")
        else:
            sys.stdout.write("\nExisting config kept. Use --force to rewrite generated files.\n")
        sys.stdout.write(f"\nNext step: set {api_key_env}, then run psi-agent doctor.\n")


def _resolve_config_path(config: str) -> Path:
    raw = config or os.environ.get("PSI_AGENT_CONFIG", "")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".psi-agent" / "config.toml").resolve()


def _resolve_workspace_path(workspace: str) -> Path:
    if workspace:
        return Path(workspace).expanduser().resolve()
    return (Path.home() / ".psi-agent" / "workspaces" / "default").resolve()


def _default_api_key_env(ai: Provider) -> str:
    if ai == "anthropic-messages":
        return "ANTHROPIC_API_KEY"
    return "OPENAI_API_KEY"


def _default_model(ai: Provider) -> str:
    if ai == "anthropic-messages":
        return "claude-sonnet-4-5"
    return "gpt-4o-mini"


def _default_base_url(ai: Provider) -> str:
    if ai == "anthropic-messages":
        return "https://api.anthropic.com/v1"
    return "https://api.openai.com/v1"


def _write_workspace(workspace_path: Path, *, force: bool) -> None:
    if workspace_path.exists() and not workspace_path.is_dir():
        raise UserFacingError(
            f"Workspace path is not a directory: {workspace_path}",
            "Choose a directory path with --workspace.",
        )
    workspace_path.mkdir(parents=True, exist_ok=True)
    _write_file(
        workspace_path / "systems" / "system.py",
        _default_system_py(),
        force=force,
    )
    _write_file(workspace_path / "tools" / ".gitkeep", "", force=False)
    _write_file(workspace_path / "skills" / ".gitkeep", "", force=False)
    _write_file(
        workspace_path / "README.md",
        _default_workspace_readme(),
        force=force,
    )


def _write_config(
    *,
    config_path: Path,
    workspace_path: Path,
    profile: str,
    ai: Provider,
    model: str,
    base_url: str,
    api_key_env: str,
    force: bool,
) -> bool:
    if config_path.exists() and not force:
        return False
    if config_path.exists() and config_path.is_dir():
        raise UserFacingError(
            f"Config path is a directory: {config_path}",
            "Choose a file path with --config.",
        )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        textwrap.dedent(
            f"""\
            config_version = 1
            default_profile = {_toml_string(profile)}
            default_workspace = {_toml_string(str(workspace_path))}

            [profiles.{profile}]
            ai = {_toml_string(ai)}
            model = {_toml_string(model)}
            base_url = {_toml_string(base_url)}
            api_key_env = {_toml_string(api_key_env)}
            """
        ),
        encoding="utf-8",
    )
    return True


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _validate_profile_name(profile: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]+", profile):
        return
    raise UserFacingError(
        f"Invalid profile name: {profile}",
        "Use only letters, numbers, underscores, and hyphens.",
    )


def _write_file(path: Path, content: str, *, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return
    if path.exists() and path.is_dir():
        raise UserFacingError(
            f"Cannot write file because a directory already exists: {path}",
            "Move the directory or choose another workspace.",
        )
    path.write_text(content, encoding="utf-8")


def _default_system_py() -> str:
    return textwrap.dedent(
        """\
        async def system_prompt_builder() -> str:
            return (
                "You are a helpful assistant. Give clear, practical answers. "
                "When something is missing, explain the next step in plain language."
            )
        """
    )


def _default_workspace_readme() -> str:
    return textwrap.dedent(
        """\
        # psi-agent Default Workspace

        This workspace was created by `psi-agent init`.

        - Put custom tools in `tools/`.
        - Put skill folders in `skills/`.
        - Edit `systems/system.py` to change the assistant behavior.
        """
    )
