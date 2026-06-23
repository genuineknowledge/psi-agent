from __future__ import annotations

import asyncio
import getpass
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from psi_agent._logging import setup_logging
from psi_agent.doctor import Doctor
from psi_agent.errors import UserFacingError
from psi_agent.init import (
    Provider,
    _default_api_key_env,
    _default_base_url,
    _default_model,
    _resolve_config_path,
    _resolve_workspace_path,
    _restrict_file_permissions,
    _validate_profile_name,
    _write_config,
    _write_workspace,
)

DEFAULT_GATEWAY_HOME = "~/.psi-agent/gateway"


async def _ask_text(prompt: str, default: str) -> str:
    raw = await asyncio.to_thread(input, f"{prompt} [{default}]: ")
    return raw.strip() or default


async def _ask_choice(prompt: str, choices: dict[str, str], default: str) -> str:
    options = "/".join(choices)
    while True:
        raw = await asyncio.to_thread(input, f"{prompt} [{options}] ({default}): ")
        value = raw.strip().lower() or default
        if value in choices:
            return choices[value]
        sys.stdout.write(f"Please choose one of: {options}\n")


async def _ask_secret_required(prompt: str) -> str:
    while True:
        raw = await asyncio.to_thread(getpass.getpass, prompt)
        value = raw.strip()
        if value:
            return value
        sys.stdout.write("A value is required.\n")


def _gateway_profile_path(gateway_home: str, profile: str) -> Path:
    return (Path(gateway_home).expanduser() / "profiles" / profile / "profile.yaml").resolve()


def _build_gateway_profile(
    *,
    profile: str,
    workspace_path: Path,
    ai: Provider,
    model: str,
    base_url: str,
    api_key_env: str,
    api_key: str,
    channel: dict[str, object],
) -> dict[str, object]:
    return {
        "name": profile,
        "workspace": str(workspace_path),
        "ai": ai,
        "model": model,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "api_key": api_key,
        "channels": [channel],
    }


def _write_gateway_profile_yaml(path: Path, profile_data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(profile_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _restrict_file_permissions(path)


async def _collect_feishu_channel() -> dict[str, object]:
    app_id = await _ask_text("Feishu App ID", "")
    app_secret = await _ask_secret_required("Feishu App Secret (hidden): ")
    verification_token = await _ask_text("Feishu verification token (optional)", "")
    channel: dict[str, object] = {
        "name": "feishu",
        "type": "feishu",
        "listen": "http://127.0.0.1:8080",
        "webhook_path": "/feishu/webhook",
        "app_id": app_id,
        "app_secret": app_secret,
    }
    if verification_token:
        channel["verification_token"] = verification_token
    return channel


async def _collect_wechat_channel() -> dict[str, object]:
    reply_url = await _ask_text("WeChat bridge reply URL", "")
    bridge_secret = await _ask_text("WeChat bridge secret (optional)", "")
    channel: dict[str, object] = {
        "name": "wechat",
        "type": "wechat-bridge",
        "listen": "http://127.0.0.1:8080",
        "webhook_path": "/wechat/webhook",
        "reply_url": reply_url,
    }
    if bridge_secret:
        channel["bridge_secret"] = bridge_secret
    return channel


@dataclass
class Setup:
    """Interactive wizard: configure a profile, API key, and an optional channel."""

    config: str = ""
    """Config TOML path. Defaults to PSI_AGENT_CONFIG or ~/.psi-agent/config.toml."""

    workspace: str = ""
    """Workspace directory to create. Defaults to ~/.psi-agent/workspaces/default."""

    profile: str = "fusion"
    """Profile name to create."""

    gateway_home: str = DEFAULT_GATEWAY_HOME
    """Gateway home directory for the generated channel profile.yaml."""

    force: bool = False
    """Overwrite generated config and workspace files."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        if not sys.stdin.isatty():
            raise UserFacingError(
                "psi-agent setup needs an interactive terminal.",
                "Run it in a real terminal, or use psi-agent init and set the API key env var.",
            )

        profile = self.profile.strip() or "fusion"
        _validate_profile_name(profile)
        config_path = _resolve_config_path(self.config)
        workspace_path = _resolve_workspace_path(self.workspace)

        sys.stdout.write("psi-agent setup\n\n")

        ai: Provider = await _ask_choice(  # ty: ignore[invalid-assignment]
            "AI backend",
            {"openai": "openai-completions", "anthropic": "anthropic-messages"},
            "openai",
        )
        model = await _ask_text("Model", _default_model(ai))
        base_url = await _ask_text("Base URL", _default_base_url(ai))
        api_key_env = _default_api_key_env(ai)
        api_key = await _ask_secret_required(f"API key for {api_key_env} (hidden): ")

        force = self.force
        if config_path.exists() and not force:
            answer = await _ask_choice(
                f"Config exists at {config_path}. Overwrite?",
                {"y": "yes", "n": "no"},
                "n",
            )
            force = answer == "yes"

        _write_workspace(workspace_path, force=force)
        config_written = _write_config(
            config_path=config_path,
            workspace_path=workspace_path,
            profile=profile,
            ai=ai,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
            api_key=api_key,
            force=force,
        )

        channel_choice = await _ask_choice(
            "Configure a chat channel now?",
            {"feishu": "feishu", "wechat": "wechat", "skip": "skip"},
            "skip",
        )
        gateway_path: Path | None = None
        if channel_choice != "skip":
            if channel_choice == "feishu":
                channel = await _collect_feishu_channel()
            else:
                channel = await _collect_wechat_channel()
            gateway_path = _gateway_profile_path(self.gateway_home, profile)
            _write_gateway_profile_yaml(
                gateway_path,
                _build_gateway_profile(
                    profile=profile,
                    workspace_path=workspace_path,
                    ai=ai,
                    model=model,
                    base_url=base_url,
                    api_key_env=api_key_env,
                    api_key=api_key,
                    channel=channel,
                ),
            )

        sys.stdout.write("\n")
        sys.stdout.write(f"Workspace: {workspace_path}\n")
        sys.stdout.write(f"Config: {config_path}\n")
        sys.stdout.write(f"Profile: {profile}\n")
        if config_written:
            sys.stdout.write("Created config with the API key stored in the config file.\n")
        else:
            sys.stdout.write("Existing config kept. Re-run with --force to rewrite it.\n")
        if gateway_path is not None:
            sys.stdout.write(f"Channel profile: {gateway_path}\n")

        sys.stdout.write("\nChecking setup...\n\n")
        await Doctor(config=self.config, profile=profile, workspace=str(workspace_path)).run()

        sys.stdout.write("\nNext steps:\n")
        sys.stdout.write('  uv run psi-agent run --message "Summarize what you can do in one sentence"\n')
        if gateway_path is not None:
            sys.stdout.write(f"  uv run psi-agent gateway --profile {profile}\n")
