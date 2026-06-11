from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from psi_agent._logging import setup_logging
from psi_agent.errors import UserFacingError
from psi_agent.run.config import RunProfileConfig, load_run_profile_config


@dataclass
class _Check:
    status: str
    name: str
    detail: str


@dataclass
class Doctor:
    """Check the local psi-agent setup and print next steps."""

    workspace: str = ""
    """Optional workspace path to check."""

    profile: str = ""
    """Optional profile name to check."""

    config: str = ""
    """Optional config TOML path. Defaults to PSI_AGENT_CONFIG or ~/.psi-agent/config.toml."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        profile_config = _load_profile_for_doctor(config=self.config, profile=self.profile)
        workspace = self.workspace or (profile_config.workspace if profile_config is not None else "")

        checks = [
            _check_python(),
            _check_console_encoding(),
            *_check_workspace(workspace),
            *_check_profile(config=self.config, profile=self.profile, profile_config=profile_config),
        ]

        sys.stdout.write("psi-agent doctor\n\n")
        for check in checks:
            sys.stdout.write(f"[{check.status}] {check.name}: {check.detail}\n")

        failures = [check for check in checks if check.status == "FAIL"]
        if failures:
            raise UserFacingError(
                f"doctor found {len(failures)} blocking issue(s).",
                "Fix the FAIL items above, then run psi-agent doctor again.",
            )

        warnings = [check for check in checks if check.status == "WARN"]
        if warnings:
            sys.stdout.write(f"\nResult: usable with {len(warnings)} warning(s).\n")
        else:
            sys.stdout.write("\nResult: ready.\n")


def _check_python() -> _Check:
    version = platform.python_version()
    return _Check("OK", "Python", f"{version}")


def _check_console_encoding() -> _Check:
    encoding = sys.stdout.encoding or "unknown"
    if "utf" in encoding.lower():
        return _Check("OK", "Console", f"stdout encoding is {encoding}")
    return _Check("WARN", "Console", f"stdout encoding is {encoding}; CLI will force UTF-8 at startup")


def _check_workspace(workspace: str) -> list[_Check]:
    if not workspace:
        return [_Check("WARN", "Workspace", "not checked; pass --workspace PATH to validate one")]

    path = Path(workspace).expanduser()
    if not path.exists():
        return [_Check("FAIL", "Workspace", f"not found: {path}")]
    if not path.is_dir():
        return [_Check("FAIL", "Workspace", f"not a directory: {path}")]

    checks = [_Check("OK", "Workspace", f"found: {path.resolve()}")]
    system_py = path / "systems" / "system.py"
    if system_py.exists():
        checks.append(_Check("OK", "System prompt", f"found: {system_py}"))
    else:
        checks.append(_Check("WARN", "System prompt", f"not found: {system_py}"))

    tools_dir = path / "tools"
    if tools_dir.exists():
        tool_count = len(list(tools_dir.glob("*.py")))
        checks.append(_Check("OK", "Tools", f"{tool_count} Python tool file(s)"))
    else:
        checks.append(_Check("WARN", "Tools", f"not found: {tools_dir}"))

    skills_dir = path / "skills"
    if skills_dir.exists():
        skill_count = len(list(skills_dir.glob("*/SKILL.md")))
        checks.append(_Check("OK", "Skills", f"{skill_count} skill(s)"))
    else:
        checks.append(_Check("WARN", "Skills", f"not found: {skills_dir}"))
    return checks


def _load_profile_for_doctor(*, config: str, profile: str) -> RunProfileConfig | None:
    path = _resolve_config_path(config)
    if not path.exists() and not profile and not config and not os.environ.get("PSI_AGENT_PROFILE"):
        return None
    try:
        return load_run_profile_config(config_path=config, profile=profile, require_api_key_env=False)
    except UserFacingError:
        return None


def _check_profile(*, config: str, profile: str, profile_config: RunProfileConfig | None) -> list[_Check]:
    path = _resolve_config_path(config)
    if not path.exists() and not profile and not config and not os.environ.get("PSI_AGENT_PROFILE"):
        return [_Check("WARN", "Profile config", f"not found: {path}")]

    if profile_config is None:
        try:
            profile_config = load_run_profile_config(config_path=config, profile=profile, require_api_key_env=False)
        except UserFacingError as e:
            return [_Check("FAIL", "Profile config", str(e).replace("\n", " "))]

    ai = profile_config.ai or "openai-completions"
    model_env = "ANTHROPIC_MODEL" if ai == "anthropic-messages" else "OPENAI_MODEL"
    base_url_env = "ANTHROPIC_BASE_URL" if ai == "anthropic-messages" else "OPENAI_BASE_URL"
    base_url_default = "https://api.anthropic.com/v1" if ai == "anthropic-messages" else "https://api.openai.com/v1"
    api_key_env = "ANTHROPIC_API_KEY" if ai == "anthropic-messages" else "OPENAI_API_KEY"

    checks = [_Check("OK", "Profile config", f"loaded: {path}")]
    checks.append(_status_for_value("AI backend", profile_config.ai or "openai-completions"))
    checks.append(_status_for_value("Model", profile_config.model or os.environ.get(model_env, "")))
    checks.append(
        _status_for_value(
            "Base URL",
            profile_config.base_url or os.environ.get(base_url_env, base_url_default),
        )
    )
    checks.append(_status_for_value("API key", profile_config.api_key or os.environ.get(api_key_env, "")))
    return checks


def _resolve_config_path(config: str) -> Path:
    raw = config or os.environ.get("PSI_AGENT_CONFIG", "")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".psi-agent" / "config.toml"


def _status_for_value(name: str, value: object) -> _Check:
    if value:
        detail = "configured"
        if name == "API key":
            detail = "configured (hidden)"
        return _Check("OK", name, detail)
    return _Check("FAIL", name, "missing")
