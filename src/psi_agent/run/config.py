from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from psi_agent.errors import UserFacingError

AiBackendName = Literal["openai-completions", "anthropic-messages"]


@dataclass(frozen=True)
class RunProfileConfig:
    ai: AiBackendName | None = None
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    base_url: str = ""
    workspace: str = ""


def load_run_profile_config(
    *,
    config_path: str = "",
    profile: str = "",
    require_api_key_env: bool = True,
) -> RunProfileConfig:
    path, explicit_path = _resolve_config_path(config_path)
    explicit_profile = bool(profile or os.environ.get("PSI_AGENT_PROFILE", ""))
    if not path.exists():
        if explicit_path or explicit_profile:
            raise UserFacingError(
                f"psi-agent config file not found: {path}",
                "Create ~/.psi-agent/config.toml or pass --config PATH.",
            )
        return RunProfileConfig()

    if path.is_dir():
        raise UserFacingError(
            f"psi-agent config path is a directory: {path}",
            "Pass --config PATH with a TOML file, or run psi-agent init to create one.",
        )

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        raise UserFacingError(
            f"psi-agent config is not UTF-8 text: {path}",
            "Save config.toml as UTF-8 text, or run psi-agent init to regenerate it.",
        ) from None
    except tomllib.TOMLDecodeError as e:
        line = getattr(e, "lineno", None)
        location = f" near line {line}" if isinstance(line, int) else ""
        raise UserFacingError(
            f"psi-agent config is not valid TOML: {path}",
            f"Check quotes, brackets, and profile tables{location}.",
        ) from None
    except OSError as e:
        raise UserFacingError(
            f"Cannot read psi-agent config: {path}",
            f"Check that --config points to a readable TOML file. ({type(e).__name__})",
        ) from None

    if not isinstance(data, dict):
        raise UserFacingError(
            f"psi-agent config must be a TOML table: {path}",
            "Run psi-agent init to regenerate a starter config.",
        )

    profile_name = (
        profile or os.environ.get("PSI_AGENT_PROFILE", "") or _optional_str(data, "default_profile") or "default"
    )
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise UserFacingError(
            f"psi-agent config missing [profiles.{profile_name}] table: {path}",
            "Add the requested profile table or choose an existing --profile.",
        )

    raw_profile = profiles.get(profile_name)
    if not isinstance(raw_profile, dict):
        raise UserFacingError(
            f"psi-agent profile not found: {profile_name}",
            "Check default_profile in config.toml or pass --profile with an existing profile name.",
        )

    ai = _optional_ai(raw_profile, "ai")
    api_key = _optional_str(raw_profile, "api_key")
    api_key_env = _optional_str(raw_profile, "api_key_env")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")
        if not api_key and require_api_key_env:
            raise UserFacingError(
                f"Environment variable is not set: {api_key_env}",
                f"Set {api_key_env} before running psi-agent, or update api_key_env in config.toml.",
            )

    return RunProfileConfig(
        ai=ai,
        model=_optional_str(raw_profile, "model"),
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=_optional_str(raw_profile, "base_url"),
        workspace=_optional_str(raw_profile, "workspace") or _optional_str(data, "default_workspace"),
    )


def _resolve_config_path(config_path: str) -> tuple[Path, bool]:
    raw = config_path or os.environ.get("PSI_AGENT_CONFIG", "")
    if raw:
        return Path(raw).expanduser(), True
    return Path.home() / ".psi-agent" / "config.toml", False


def _optional_str(table: dict, key: str) -> str:
    value = table.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise UserFacingError(
            f"psi-agent config field must be a string: {key}",
            f'Open config.toml and quote the value, for example {key} = "...".',
        )
    return value


def _optional_ai(table: dict, key: str) -> AiBackendName | None:
    value = _optional_str(table, key)
    if not value:
        return None
    if value == "openai-completions":
        return "openai-completions"
    if value == "anthropic-messages":
        return "anthropic-messages"
    raise UserFacingError(
        f'psi-agent config field "ai" must be one of: openai-completions, anthropic-messages; got {value!r}',
        'Open config.toml and set ai = "openai-completions" or ai = "anthropic-messages".',
    )
