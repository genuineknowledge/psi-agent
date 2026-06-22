from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

import anyio
import yaml

from psi_agent._logging import setup_logging
from psi_agent.ai.anthropic_messages.server import serve_anthropic_messages
from psi_agent.ai.openai_completions.server import serve_openai_completions
from psi_agent.channel.platform import (
    DISCORD_DEFAULT_GATEWAY_INTENTS,
    DISCORD_DEFAULT_GATEWAY_URL,
    DingTalkAdapter,
    DiscordAdapter,
    FeishuAdapter,
    PlatformAdapter,
    QQBridgeAdapter,
    SlackAdapter,
    TelegramAdapter,
    WeChatBridgeAdapter,
    WhatsAppAdapter,
    serve_discord_gateway_channel,
    serve_platform_channels,
)
from psi_agent.errors import UserFacingError
from psi_agent.net import is_tcp_endpoint, read_endpoint_sidecar
from psi_agent.run.config import AiBackendName
from psi_agent.session import _run_one_schedule, build_session_agent
from psi_agent.session.scheduler import load_schedules_from_workspace
from psi_agent.session.server import serve_session
from psi_agent.workspace import resolve_workspace_path

DEFAULT_GATEWAY_HOME = "~/.psi-agent/gateway"
DEFAULT_PROFILE_NAME = "default"
DEFAULT_WEBHOOK_PATH = "/webhook"


ChannelKind = Literal[
    "telegram",
    "whatsapp",
    "discord",
    "slack",
    "qq-bridge",
    "wechat-bridge",
    "feishu",
    "dingtalk",
]


@dataclass(frozen=True)
class ProfileChannelConfig:
    name: str
    kind: ChannelKind
    listen: str = "http://127.0.0.1:8080"
    webhook_path: str = DEFAULT_WEBHOOK_PATH
    enabled: bool = True
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayProfile:
    name: str
    profile_dir: Path
    workspace: str
    ai: AiBackendName
    model: str
    api_key: str = ""
    api_key_env: str = ""
    base_url: str = ""
    ai_socket: str = ""
    session_socket: str = ""
    channels: tuple[ProfileChannelConfig, ...] = ()


@dataclass
class ProfileGateway:
    """Start one profile-based gateway runtime.

    The runtime owns AI backend, session server, platform channel servers, and
    profile-local state files. Platform adapters keep their existing protocol
    behaviour; this layer only orchestrates them as one profile process.
    """

    profile: str = DEFAULT_PROFILE_NAME
    """Profile gateway name under --home/profiles, or an explicit profile directory."""

    home: str = DEFAULT_GATEWAY_HOME
    """Profile gateway home directory containing profiles/<name>/profile.yaml."""

    profile_dir: str = ""
    """Explicit profile directory. Overrides --home and --profile."""

    workspace: str = ""
    """Workspace override. Defaults to profile.yaml workspace."""

    ai_socket: str = ""
    """Existing AI backend socket or http(s) /v1 endpoint. If omitted, runtime starts one."""

    session_socket: str = ""
    """Session socket override. Defaults to profile-local session endpoint."""

    config: str = ""
    """Explicit profile YAML path. Defaults to <profile-dir>/profile.yaml."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        profile = load_gateway_profile(
            profile=self.profile,
            home=self.home,
            profile_dir=self.profile_dir,
            config_path=self.config,
            workspace_override=self.workspace,
            ai_socket_override=self.ai_socket,
            session_socket_override=self.session_socket,
        )
        await serve_profile_gateway(profile)


async def serve_profile_gateway(profile: GatewayProfile) -> None:
    profile.profile_dir.mkdir(parents=True, exist_ok=True)
    (profile.profile_dir / "logs").mkdir(exist_ok=True)
    (profile.profile_dir / "sessions").mkdir(exist_ok=True)

    ai_endpoint = profile.ai_socket or _default_profile_endpoint(profile.profile_dir, "ai")
    session_endpoint = profile.session_socket or _default_profile_endpoint(profile.profile_dir, "session")
    effective_base_url = _resolve_profile_base_url(profile)
    effective_api_key = _resolve_profile_api_key(profile)

    _validate_runtime_config(profile=profile, ai_endpoint=ai_endpoint, base_url=effective_base_url)
    _write_gateway_state(
        profile,
        status="starting",
        ai_endpoint=ai_endpoint,
        session_endpoint=session_endpoint,
        channels=[],
    )

    schedules = await load_schedules_from_workspace(resolve_workspace_path(profile.workspace) / "schedules")
    agent = await build_session_agent(workspace=profile.workspace, ai_socket=ai_endpoint, model=profile.model)
    lock = anyio.Lock()

    enabled_channels = [channel for channel in profile.channels if channel.enabled]
    _write_channel_directory(profile, session_endpoint=session_endpoint, channels=enabled_channels)
    _write_gateway_state(
        profile,
        status="running",
        ai_endpoint=ai_endpoint,
        session_endpoint=session_endpoint,
        channels=enabled_channels,
    )

    try:
        async with anyio.create_task_group() as tg:
            if not profile.ai_socket:
                tg.start_soon(
                    _serve_ai_backend,
                    profile.ai,
                    ai_endpoint,
                    profile.model,
                    effective_api_key,
                    effective_base_url,
                )
            tg.start_soon(
                partial(
                    serve_session,
                    channel_socket=session_endpoint,
                    agent=agent,
                    lock=lock,
                    after_turn_task_group=tg,
                )
            )
            for schedule in schedules:
                tg.start_soon(_run_one_schedule, schedule, agent, lock, tg)
            for channel in _discord_gateway_channels(enabled_channels):
                tg.start_soon(_serve_discord_gateway_profile_channel, session_endpoint, channel)
            for listen, routes in _platform_channel_groups(enabled_channels).items():
                tg.start_soon(
                    partial(
                        serve_platform_channels,
                        session_socket=session_endpoint,
                        listen=listen,
                        routes=routes,
                    )
                )
    finally:
        _write_gateway_state(
            profile,
            status="stopped",
            ai_endpoint=ai_endpoint,
            session_endpoint=session_endpoint,
            channels=enabled_channels,
        )


def load_gateway_profile(
    *,
    profile: str = DEFAULT_PROFILE_NAME,
    home: str = DEFAULT_GATEWAY_HOME,
    profile_dir: str = "",
    config_path: str = "",
    workspace_override: str = "",
    ai_socket_override: str = "",
    session_socket_override: str = "",
) -> GatewayProfile:
    resolved_profile_dir = _resolve_profile_dir(profile=profile, home=home, profile_dir=profile_dir)
    config_file = Path(config_path).expanduser() if config_path else resolved_profile_dir / "profile.yaml"
    raw = _load_profile_yaml(config_file)

    profile_name = _optional_str(raw, "name") or resolved_profile_dir.name
    workspace = workspace_override or _optional_str(raw, "workspace")
    ai = _optional_ai(raw, "ai") or "openai-completions"
    model = _optional_str(raw, "model") or _env_model(ai)
    channels = tuple(_load_channels(raw.get("channels")))

    return GatewayProfile(
        name=profile_name,
        profile_dir=resolved_profile_dir,
        workspace=workspace,
        ai=ai,
        model=model,
        api_key=_optional_str(raw, "api_key"),
        api_key_env=_optional_str(raw, "api_key_env"),
        base_url=_optional_str(raw, "base_url"),
        ai_socket=ai_socket_override or _optional_str(raw, "ai_socket"),
        session_socket=session_socket_override or _optional_str(raw, "session_socket"),
        channels=channels,
    )


async def _serve_ai_backend(
    ai: AiBackendName,
    endpoint: str,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    if ai == "openai-completions":
        await serve_openai_completions(socket_path=endpoint, model=model, api_key=api_key, base_url=base_url)
        return
    await serve_anthropic_messages(socket_path=endpoint, model=model, api_key=api_key, base_url=base_url)


async def _serve_discord_gateway_profile_channel(session_endpoint: str, channel: ProfileChannelConfig) -> None:
    bot_token = _required_option(channel, "bot_token", "DISCORD_BOT_TOKEN")
    await serve_discord_gateway_channel(
        session_socket=session_endpoint,
        bot_token=bot_token,
        api_base_url=channel.options.get("api_base_url", "https://discord.com/api/v10"),
        gateway_url=channel.options.get("gateway_url", DISCORD_DEFAULT_GATEWAY_URL),
        gateway_intents=int(channel.options.get("gateway_intents") or str(DISCORD_DEFAULT_GATEWAY_INTENTS)),
    )


def _discord_gateway_channels(channels: list[ProfileChannelConfig]) -> list[ProfileChannelConfig]:
    return [channel for channel in channels if channel.kind == "discord" and channel.options.get("mode") == "gateway"]


def _platform_channel_groups(channels: list[ProfileChannelConfig]) -> dict[str, list[tuple[str, PlatformAdapter]]]:
    groups: dict[str, list[tuple[str, PlatformAdapter]]] = {}
    for channel in channels:
        if channel.kind == "discord" and channel.options.get("mode") == "gateway":
            continue
        groups.setdefault(channel.listen, []).append((channel.webhook_path, _build_adapter(channel)))
    return groups


def _build_adapter(channel: ProfileChannelConfig) -> PlatformAdapter:
    if channel.kind == "telegram":
        return TelegramAdapter(
            token=_required_option(channel, "token", "TELEGRAM_BOT_TOKEN"),
            api_base_url=channel.options.get("api_base_url", "https://api.telegram.org"),
            webhook_secret=channel.options.get("webhook_secret", ""),
        )
    if channel.kind == "whatsapp":
        return WhatsAppAdapter(
            token=_required_option(channel, "token", "WHATSAPP_ACCESS_TOKEN"),
            phone_number_id=_required_option(channel, "phone_number_id", "WHATSAPP_PHONE_NUMBER_ID"),
            api_base_url=channel.options.get("api_base_url", "https://graph.facebook.com"),
            verify_token=channel.options.get("verify_token", ""),
        )
    if channel.kind == "discord":
        return DiscordAdapter(
            bot_token=_required_option(channel, "bot_token", "DISCORD_BOT_TOKEN"),
            api_base_url=channel.options.get("api_base_url", "https://discord.com/api/v10"),
            relay_secret=channel.options.get("relay_secret", ""),
        )
    if channel.kind == "slack":
        return SlackAdapter(
            bot_token=_required_option(channel, "bot_token", "SLACK_BOT_TOKEN"),
            api_base_url=channel.options.get("api_base_url", "https://slack.com/api"),
            signing_secret=channel.options.get("signing_secret", ""),
        )
    if channel.kind == "qq-bridge":
        return QQBridgeAdapter(
            reply_url=channel.options.get("reply_url", ""),
            bridge_secret=channel.options.get("bridge_secret", ""),
        )
    if channel.kind == "wechat-bridge":
        return WeChatBridgeAdapter(
            reply_url=channel.options.get("reply_url", ""),
            bridge_secret=channel.options.get("bridge_secret", ""),
        )
    if channel.kind == "feishu":
        return FeishuAdapter(
            tenant_access_token=channel.options.get("tenant_access_token")
            or os.environ.get("FEISHU_TENANT_ACCESS_TOKEN", ""),
            app_id=channel.options.get("app_id") or os.environ.get("FEISHU_APP_ID", ""),
            app_secret=channel.options.get("app_secret") or os.environ.get("FEISHU_APP_SECRET", ""),
            api_base_url=channel.options.get("api_base_url", "https://open.feishu.cn"),
            verification_token=channel.options.get("verification_token", ""),
        )
    if channel.kind == "dingtalk":
        return DingTalkAdapter(
            session_webhook=channel.options.get("session_webhook", ""),
            outgoing_token=channel.options.get("outgoing_token", ""),
        )
    raise UserFacingError(f"Unsupported Profile channel type: {channel.kind}")


def _load_profile_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise UserFacingError(
            f"Profile gateway config not found: {path}",
            "Create profile.yaml or pass --config PATH.",
        )
    if path.is_dir():
        raise UserFacingError(f"Profile gateway config path is a directory: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except UnicodeDecodeError:
        raise UserFacingError(f"Profile gateway config is not UTF-8 text: {path}") from None
    except yaml.YAMLError as e:
        raise UserFacingError(f"Profile gateway config is not valid YAML: {path}", str(e)) from None
    if not isinstance(data, dict):
        raise UserFacingError(f"Profile gateway config must be a YAML mapping: {path}")
    return cast(dict[str, Any], data)


def _load_channels(raw_channels: object) -> list[ProfileChannelConfig]:
    if raw_channels is None:
        return []
    if not isinstance(raw_channels, list):
        raise UserFacingError("Profile gateway field must be a list: channels")

    channels: list[ProfileChannelConfig] = []
    for index, raw in enumerate(raw_channels):
        if not isinstance(raw, dict):
            raise UserFacingError(f"Profile channel entry must be a mapping: channels[{index}]")
        channel = cast(dict[str, Any], raw)
        kind = _optional_str(channel, "type") or _optional_str(channel, "kind")
        if not kind:
            raise UserFacingError(f"Profile channel entry missing type: channels[{index}]")
        normalized_kind = _normalize_channel_kind(kind)
        name = _optional_str(channel, "name") or normalized_kind
        listen = _optional_str(channel, "listen") or "http://127.0.0.1:8080"
        webhook_path = _optional_str(channel, "webhook_path") or DEFAULT_WEBHOOK_PATH
        enabled = _optional_bool(channel, "enabled", default=True)
        options = _load_channel_options(channel)
        channels.append(
            ProfileChannelConfig(
                name=name,
                kind=normalized_kind,
                listen=listen,
                webhook_path=webhook_path,
                enabled=enabled,
                options=options,
            )
        )
    return channels


def _load_channel_options(channel: dict[str, Any]) -> dict[str, str]:
    reserved = {"name", "type", "kind", "listen", "webhook_path", "enabled", "options"}
    options: dict[str, str] = {}
    raw_options = channel.get("options")
    if raw_options is not None:
        if not isinstance(raw_options, dict):
            raise UserFacingError("Profile channel options must be a mapping.")
        for key, value in raw_options.items():
            if value is None:
                continue
            options[str(key)] = str(value)

    for key, value in channel.items():
        if key in reserved or value is None:
            continue
        options[str(key)] = str(value)
    return options


def _normalize_channel_kind(kind: str) -> ChannelKind:
    normalized = kind.strip().lower().replace("_", "-")
    aliases = {
        "qq": "qq-bridge",
        "qqbridge": "qq-bridge",
        "wechat": "wechat-bridge",
        "weixin": "wechat-bridge",
        "weixin-bridge": "wechat-bridge",
    }
    normalized = aliases.get(normalized, normalized)
    supported = {
        "telegram",
        "whatsapp",
        "discord",
        "slack",
        "qq-bridge",
        "wechat-bridge",
        "feishu",
        "dingtalk",
    }
    if normalized not in supported:
        raise UserFacingError(f"Unsupported Profile channel type: {kind}")
    return cast(ChannelKind, normalized)


def _resolve_profile_dir(*, profile: str, home: str, profile_dir: str) -> Path:
    if profile_dir:
        return Path(profile_dir).expanduser().resolve()

    profile_path = Path(profile).expanduser()
    if profile_path.is_absolute() or (profile_path.parts and any(part in profile for part in (os.sep, "/"))):
        return profile_path.resolve()

    return (Path(home).expanduser() / "profiles" / (profile or DEFAULT_PROFILE_NAME)).resolve()


def _default_profile_endpoint(profile_dir: Path, name: str) -> str:
    if os.name == "nt":
        return str(profile_dir / f"{name}.sock")
    return str(profile_dir / f"{name}.sock")


def _resolve_profile_api_key(profile: GatewayProfile) -> str:
    if profile.api_key:
        return profile.api_key
    if profile.api_key_env:
        return os.environ.get(profile.api_key_env, "")
    env_name = "OPENAI_API_KEY" if profile.ai == "openai-completions" else "ANTHROPIC_API_KEY"
    return os.environ.get(env_name, "")


def _resolve_profile_base_url(profile: GatewayProfile) -> str:
    if profile.base_url:
        return profile.base_url
    if profile.ai == "openai-completions":
        return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")


def _validate_runtime_config(*, profile: GatewayProfile, ai_endpoint: str, base_url: str) -> None:
    if not profile.workspace:
        raise UserFacingError(
            "Profile gateway is missing workspace.",
            "Set workspace in profile.yaml or pass --workspace PATH.",
        )
    resolve_workspace_path(profile.workspace)

    if not profile.model:
        env_name = "OPENAI_MODEL" if profile.ai == "openai-completions" else "ANTHROPIC_MODEL"
        raise UserFacingError("Profile gateway is missing model.", f"Set model in profile.yaml or {env_name}.")

    if not profile.ai_socket:
        api_key = _resolve_profile_api_key(profile)
        if not api_key:
            env_name = profile.api_key_env or (
                "OPENAI_API_KEY" if profile.ai == "openai-completions" else "ANTHROPIC_API_KEY"
            )
            raise UserFacingError(
                "Profile gateway API key is not configured.",
                f"Set api_key_env in profile.yaml or export {env_name}.",
            )
        if not base_url:
            raise UserFacingError("Profile gateway base_url is not configured.")

    if not ai_endpoint:
        raise UserFacingError("Profile gateway AI endpoint is empty.")


def _required_option(channel: ProfileChannelConfig, option_name: str, env_name: str) -> str:
    value = channel.options.get(option_name) or os.environ.get(env_name, "")
    if value:
        return value
    raise UserFacingError(
        f"Missing {channel.kind} channel option: {option_name}",
        f"Set channels[].{option_name} in profile.yaml or export {env_name}.",
    )


def _write_channel_directory(
    profile: GatewayProfile,
    *,
    session_endpoint: str,
    channels: list[ProfileChannelConfig],
) -> None:
    payload = {
        "profile": profile.name,
        "session_endpoint": _advertised_endpoint(session_endpoint),
        "updated_at": _now_unix(),
        "channels": [_channel_directory_entry(channel) for channel in channels],
    }
    _write_json(profile.profile_dir / "channel_directory.json", payload)


def _write_gateway_state(
    profile: GatewayProfile,
    *,
    status: str,
    ai_endpoint: str,
    session_endpoint: str,
    channels: list[ProfileChannelConfig],
) -> None:
    payload = {
        "profile": profile.name,
        "pid": os.getpid(),
        "status": status,
        "updated_at": _now_unix(),
        "ai_endpoint": _advertised_endpoint(ai_endpoint),
        "session_endpoint": _advertised_endpoint(session_endpoint),
        "channels": [_channel_directory_entry(channel) for channel in channels],
    }
    _write_json(profile.profile_dir / "gateway_state.json", payload)
    (profile.profile_dir / "gateway.pid").write_text(str(os.getpid()), encoding="utf-8")


def _channel_directory_entry(channel: ProfileChannelConfig) -> dict[str, Any]:
    return {
        "name": channel.name,
        "type": channel.kind,
        "listen": channel.listen,
        "webhook_path": channel.webhook_path,
        "enabled": channel.enabled,
        "mode": channel.options.get("mode", "webhook"),
    }


def _advertised_endpoint(endpoint: str) -> str:
    if is_tcp_endpoint(endpoint):
        return endpoint
    sidecar = read_endpoint_sidecar(endpoint)
    return sidecar or endpoint


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now_unix() -> int:
    return int(time.time())


def _optional_str(table: dict[str, Any], key: str) -> str:
    value = table.get(key, "")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raise UserFacingError(f"Profile gateway field must be a string: {key}")


def _optional_bool(table: dict[str, Any], key: str, *, default: bool) -> bool:
    value = table.get(key, default)
    if isinstance(value, bool):
        return value
    raise UserFacingError(f"Profile gateway field must be a boolean: {key}")


def _optional_ai(table: dict[str, Any], key: str) -> AiBackendName | None:
    value = _optional_str(table, key)
    if not value:
        return None
    if value in {"openai-completions", "anthropic-messages"}:
        return cast(AiBackendName, value)
    raise UserFacingError(
        f'Profile gateway field "ai" must be one of: openai-completions, anthropic-messages; got {value!r}'
    )


def _env_model(ai: AiBackendName) -> str:
    if ai == "openai-completions":
        return os.environ.get("OPENAI_MODEL", "")
    return os.environ.get("ANTHROPIC_MODEL", "")
