from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Annotated, Any

import aiohttp
import anyio
from loguru import logger
from tyro import conf

from psi_agent._logging import setup_logging

_DEFAULT_OUTPUT_DIR = "./feishu-setup"
_DEFAULT_SESSION_SOCKET = "http://127.0.0.1:8766"
_DEFAULT_WORKSPACE = "."
_DEFAULT_OPEN_PLATFORM_ORIGIN = "https://open.feishu.cn"
_OPEN_PLATFORM_APP_URL = "https://open.feishu.cn/app"
_APP_TOKEN_URI = "/open-apis/auth/v3/app_access_token/internal"
_TENANT_TOKEN_URI = "/open-apis/auth/v3/tenant_access_token/internal"

_REQUIRED_TENANT_SCOPES = (
    "cardkit:card:write",
    "im:message",
    "im:message:readonly",
    "im:message:send_as_bot",
    "im:resource",
)

_RECOMMENDED_TENANT_SCOPES = (
    "im:chat",
    "im:chat.access_event.bot_p2p_chat:read",
    "im:chat.members:bot_access",
    "im:message.group_at_msg:readonly",
    "im:message.group_msg",
    "im:message.p2p_msg:readonly",
)

_RUNTIME_SCOPE_NOTES = {
    "cardkit:card:write": "Required for `channel.stream()` card-based streaming replies.",
    "im:message": "Required for bot message handling in IM conversations.",
    "im:message:readonly": "Required to read inbound message payloads.",
    "im:message:send_as_bot": "Required to send replies as the bot.",
    "im:resource": "Required to download message attachments and upload outbound files.",
    "im:chat": "Recommended for chat lookup and bot chat interactions.",
    "im:chat.access_event.bot_p2p_chat:read": "Recommended for bot P2P access events.",
    "im:chat.members:bot_access": "Recommended when the bot needs chat member access.",
    "im:message.group_at_msg:readonly": "Recommended for group @-mention message events.",
    "im:message.group_msg": "Recommended for ordinary group message events.",
    "im:message.p2p_msg:readonly": "Recommended for private chat message events.",
}


@dataclass
class ScopeManifest:
    tenant: list[str]
    user: list[str]


@dataclass
class ScopeAnalysis:
    tenant_total: int
    user_total: int
    required_present: list[str]
    required_missing: list[str]
    recommended_present: list[str]
    recommended_missing: list[str]
    extra_tenant_scopes: list[str]
    extra_user_scopes: list[str]
    notes: dict[str, str]


@dataclass
class TokenProbe:
    attempted: bool
    ok: bool
    token_kind: str
    http_status: int | None
    code: int | None
    message: str
    expires_in: int | None


@dataclass
class CredentialValidation:
    app_access_token: TokenProbe
    tenant_access_token: TokenProbe


@dataclass
class SetupArtifacts:
    manifest_json: str
    report_json: str
    next_steps_markdown: str
    powershell_example: str


@dataclass
class ChannelFeishuSetup:
    """Generate a Feishu bot setup bundle from a scopes JSON manifest."""

    scopes_json: Annotated[str, conf.Positional]
    """Path to a JSON file that contains a top-level `scopes` object."""

    output_dir: str = _DEFAULT_OUTPUT_DIR
    """Directory where the generated setup bundle will be written."""

    app_id: str = ""
    """Feishu app ID (CLI arg > PSI_FEISHU_APP_ID env)."""

    app_secret: str = ""
    """Feishu app secret (CLI arg > PSI_FEISHU_APP_SECRET env)."""

    session_socket: str = _DEFAULT_SESSION_SOCKET
    """Session socket shown in generated startup examples."""

    workspace: str = _DEFAULT_WORKSPACE
    """Workspace path shown in generated startup examples."""

    open_platform_origin: str = _DEFAULT_OPEN_PLATFORM_ORIGIN
    """Open Platform origin used for token validation."""

    validate_credentials: bool = True
    """Whether to probe Feishu token endpoints with app_id/app_secret."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        logger.info(f"Loading Feishu scope manifest from {self.scopes_json}")

        manifest_path = anyio.Path(self.scopes_json)
        try:
            text = await manifest_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read scope manifest {self.scopes_json}: {e}")
            raise

        manifest = load_scope_manifest_from_text(text)
        analysis = analyze_scope_manifest(manifest)

        app_id = self.app_id or os.environ.get("PSI_FEISHU_APP_ID", "")
        app_secret = self.app_secret or os.environ.get("PSI_FEISHU_APP_SECRET", "")
        validation = await _validate_credentials(
            origin=self.open_platform_origin,
            app_id=app_id,
            app_secret=app_secret,
            enabled=self.validate_credentials,
        )

        artifacts = build_setup_artifacts(
            manifest=manifest,
            analysis=analysis,
            validation=validation,
            app_id=app_id,
            session_socket=self.session_socket,
            workspace=self.workspace,
        )
        await write_setup_bundle(self.output_dir, artifacts)

        missing_required = ", ".join(analysis.required_missing) or "(none)"
        logger.info(f"Feishu setup bundle written to {self.output_dir}")
        logger.info(f"Missing required runtime scopes: {missing_required}")
        logger.info(
            "Credential probe: "
            f"app={_probe_status(validation.app_access_token)} "
            f"tenant={_probe_status(validation.tenant_access_token)}"
        )


def load_scope_manifest_from_text(text: str) -> ScopeManifest:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in Feishu scope manifest: {e}") from e

    if not isinstance(raw, dict):
        msg = f"Feishu scope manifest must be an object, got {type(raw).__name__}"
        raise ValueError(msg)

    scopes = raw.get("scopes")
    if not isinstance(scopes, dict):
        raise ValueError("Feishu scope manifest must contain an object field `scopes`.")

    tenant = _parse_scope_list("scopes.tenant", scopes.get("tenant"))
    user = _parse_scope_list("scopes.user", scopes.get("user"))
    logger.debug(f"Loaded {len(tenant)} tenant scope(s) and {len(user)} user scope(s)")
    return ScopeManifest(tenant=tenant, user=user)


def analyze_scope_manifest(manifest: ScopeManifest) -> ScopeAnalysis:
    tenant_scopes = set(manifest.tenant)
    required_present = [scope for scope in _REQUIRED_TENANT_SCOPES if scope in tenant_scopes]
    required_missing = [scope for scope in _REQUIRED_TENANT_SCOPES if scope not in tenant_scopes]
    recommended_present = [scope for scope in _RECOMMENDED_TENANT_SCOPES if scope in tenant_scopes]
    recommended_missing = [scope for scope in _RECOMMENDED_TENANT_SCOPES if scope not in tenant_scopes]

    known_scopes = set(_REQUIRED_TENANT_SCOPES) | set(_RECOMMENDED_TENANT_SCOPES)
    extra_tenant_scopes = sorted(scope for scope in tenant_scopes if scope not in known_scopes)

    notes = {scope: _RUNTIME_SCOPE_NOTES[scope] for scope in required_present + recommended_present}
    return ScopeAnalysis(
        tenant_total=len(manifest.tenant),
        user_total=len(manifest.user),
        required_present=required_present,
        required_missing=required_missing,
        recommended_present=recommended_present,
        recommended_missing=recommended_missing,
        extra_tenant_scopes=extra_tenant_scopes,
        extra_user_scopes=manifest.user,
        notes=notes,
    )


def build_setup_artifacts(
    *,
    manifest: ScopeManifest,
    analysis: ScopeAnalysis,
    validation: CredentialValidation,
    app_id: str,
    session_socket: str,
    workspace: str,
) -> SetupArtifacts:
    manifest_json = json.dumps({"scopes": asdict(manifest)}, ensure_ascii=False, indent=2) + "\n"
    report = {
        "manifest": asdict(manifest),
        "analysis": asdict(analysis),
        "credential_validation": asdict(validation),
        "runtime_scope_notes": _RUNTIME_SCOPE_NOTES,
    }
    report_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    next_steps_markdown = _render_next_steps(
        analysis=analysis,
        validation=validation,
        app_id=app_id,
        session_socket=session_socket,
        workspace=workspace,
    )
    powershell_example = _render_powershell_example(app_id=app_id, session_socket=session_socket)
    return SetupArtifacts(
        manifest_json=manifest_json,
        report_json=report_json,
        next_steps_markdown=next_steps_markdown,
        powershell_example=powershell_example,
    )


async def write_setup_bundle(output_dir: str, artifacts: SetupArtifacts) -> None:
    out = anyio.Path(output_dir)
    await out.mkdir(parents=True, exist_ok=True)

    manifest_path = anyio.Path(f"{output_dir}/scope-manifest.normalized.json")
    report_path = anyio.Path(f"{output_dir}/report.json")
    next_steps_path = anyio.Path(f"{output_dir}/NEXT_STEPS.md")
    powershell_path = anyio.Path(f"{output_dir}/start-feishu.ps1.example")

    await manifest_path.write_text(artifacts.manifest_json, encoding="utf-8")
    await report_path.write_text(artifacts.report_json, encoding="utf-8")
    await next_steps_path.write_text(artifacts.next_steps_markdown, encoding="utf-8")
    await powershell_path.write_text(artifacts.powershell_example, encoding="utf-8")

    logger.debug(f"Wrote {manifest_path}")
    logger.debug(f"Wrote {report_path}")
    logger.debug(f"Wrote {next_steps_path}")
    logger.debug(f"Wrote {powershell_path}")


async def _validate_credentials(
    *,
    origin: str,
    app_id: str,
    app_secret: str,
    enabled: bool,
) -> CredentialValidation:
    if not enabled:
        skipped = TokenProbe(
            attempted=False,
            ok=False,
            token_kind="skipped",
            http_status=None,
            code=None,
            message="Credential probe disabled.",
            expires_in=None,
        )
        return CredentialValidation(app_access_token=skipped, tenant_access_token=skipped)

    if not app_id or not app_secret:
        missing = TokenProbe(
            attempted=False,
            ok=False,
            token_kind="missing",
            http_status=None,
            code=None,
            message="Set --app-id/--app-secret or PSI_FEISHU_APP_ID/PSI_FEISHU_APP_SECRET to probe credentials.",
            expires_in=None,
        )
        return CredentialValidation(app_access_token=missing, tenant_access_token=missing)

    app_probe = await _probe_token(
        origin=origin,
        uri=_APP_TOKEN_URI,
        token_kind="app_access_token",
        app_id=app_id,
        app_secret=app_secret,
    )
    tenant_probe = await _probe_token(
        origin=origin,
        uri=_TENANT_TOKEN_URI,
        token_kind="tenant_access_token",
        app_id=app_id,
        app_secret=app_secret,
    )
    return CredentialValidation(app_access_token=app_probe, tenant_access_token=tenant_probe)


async def _probe_token(
    *,
    origin: str,
    uri: str,
    token_kind: str,
    app_id: str,
    app_secret: str,
) -> TokenProbe:
    url = origin.rstrip("/") + uri
    logger.debug(f"Probing Feishu {token_kind} via {url}")
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json={"app_id": app_id, "app_secret": app_secret}) as response:
                text = await response.text()
                status = response.status
    except (aiohttp.ClientError, OSError) as e:
        logger.warning(f"Failed to probe {token_kind}: {e}")
        return TokenProbe(
            attempted=True,
            ok=False,
            token_kind=token_kind,
            http_status=None,
            code=None,
            message=str(e),
            expires_in=None,
        )

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        snippet = text[:160].replace("\r", " ").replace("\n", " ")
        logger.warning(f"{token_kind} probe returned non-JSON response: {snippet}")
        return TokenProbe(
            attempted=True,
            ok=False,
            token_kind=token_kind,
            http_status=status,
            code=None,
            message=f"Non-JSON response: {snippet}",
            expires_in=None,
        )

    if not isinstance(raw, dict):
        return TokenProbe(
            attempted=True,
            ok=False,
            token_kind=token_kind,
            http_status=status,
            code=None,
            message=f"Unexpected JSON type: {type(raw).__name__}",
            expires_in=None,
        )

    code_value = raw.get("code")
    code = code_value if isinstance(code_value, int) else None
    msg_value = raw.get("msg")
    message = msg_value if isinstance(msg_value, str) else f"HTTP {status}"
    expire_value = raw.get("expire")
    expires_in = expire_value if isinstance(expire_value, int) else None
    ok = status == 200 and code == 0
    logger.debug(f"{token_kind} probe finished: status={status} code={code} ok={ok}")
    return TokenProbe(
        attempted=True,
        ok=ok,
        token_kind=token_kind,
        http_status=status,
        code=code,
        message=message,
        expires_in=expires_in,
    )


def _parse_scope_list(field_name: str, value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list, got {type(value).__name__}")

    scopes: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{idx}] must be a string, got {type(item).__name__}")
        scope = item.strip()
        if not scope:
            raise ValueError(f"{field_name}[{idx}] must not be empty")
        scopes.append(scope)
    return sorted(set(scopes))


def _probe_status(probe: TokenProbe) -> str:
    if not probe.attempted:
        return "skipped"
    return "ok" if probe.ok else "failed"


def _render_next_steps(
    *,
    analysis: ScopeAnalysis,
    validation: CredentialValidation,
    app_id: str,
    session_socket: str,
    workspace: str,
) -> str:
    app_line = app_id or "<set PSI_FEISHU_APP_ID>"
    required_missing = _render_scope_lines(analysis.required_missing)
    recommended_missing = _render_scope_lines(analysis.recommended_missing)
    extra_tenant = _render_scope_lines(analysis.extra_tenant_scopes)
    extra_user = _render_scope_lines(analysis.extra_user_scopes)

    lines = [
        "# Feishu Bot Setup",
        "",
        "## Summary",
        "",
        f"- App ID: `{app_line}`",
        f"- Session socket example: `{session_socket}`",
        f"- Workspace example: `{workspace}`",
        f"- Required runtime scopes present: `{len(analysis.required_present)}/{len(_REQUIRED_TENANT_SCOPES)}`",
        f"- Recommended runtime scopes present: `{len(analysis.recommended_present)}/{len(_RECOMMENDED_TENANT_SCOPES)}`",
        f"- App token probe: `{_probe_status(validation.app_access_token)}`",
        f"- Tenant token probe: `{_probe_status(validation.tenant_access_token)}`",
        "",
        "## Missing Required Scopes",
        "",
        required_missing,
        "",
        "## Missing Recommended Scopes",
        "",
        recommended_missing,
        "",
        "## Extra Tenant Scopes",
        "",
        extra_tenant,
        "",
        "## User Scopes From Manifest",
        "",
        extra_user,
        "",
        "## Manual Open Platform Steps",
        "",
        f"1. Open `{_OPEN_PLATFORM_APP_URL}` and enter the target self-built app.",
        "2. In the permissions / scopes page, make sure every required and recommended runtime scope above is present.",
        "3. In the bot / messaging capability section, enable bot messaging and event delivery for the chats you want.",
        "4. Publish a new version, then reinstall or refresh the app inside the tenant if Feishu asks for it.",
        "5. Add the bot to a test group or open a P2P chat with it, then run `psi-agent channel feishu`.",
        "",
        "## Local Startup Example",
        "",
        "```powershell",
        f"$env:PSI_FEISHU_APP_ID = \"{app_id or '<set-me>'}\"",
        "$env:PSI_FEISHU_APP_SECRET = \"<set-me>\"",
        f"uv run psi-agent channel feishu --session-socket \"{session_socket}\"",
        "```",
        "",
        "## Credential Probe Notes",
        "",
        f"- App access token: {validation.app_access_token.message}",
        f"- Tenant access token: {validation.tenant_access_token.message}",
        "",
        "A common pattern is: app token succeeds but tenant token fails. That usually means the credentials are valid,",
        "but the app has not been fully released / installed for the tenant yet.",
    ]
    return "\n".join(lines) + "\n"


def _render_powershell_example(*, app_id: str, session_socket: str) -> str:
    lines = [
        f"$env:PSI_FEISHU_APP_ID = \"{app_id or '<set-me>'}\"",
        "$env:PSI_FEISHU_APP_SECRET = \"<set-me>\"",
        f"uv run psi-agent channel feishu --session-socket \"{session_socket}\"",
    ]
    return "\n".join(lines) + "\n"


def _render_scope_lines(scopes: list[str]) -> str:
    if not scopes:
        return "- None"
    return "\n".join(f"- `{scope}`" for scope in scopes)
