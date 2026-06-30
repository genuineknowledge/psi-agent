from __future__ import annotations

import json

import anyio
import pytest

from psi_agent.channel.feishu_setup import (
    ChannelFeishuSetup,
    analyze_scope_manifest,
    load_scope_manifest_from_text,
)


def test_load_scope_manifest_from_text_normalizes_lists():
    manifest = load_scope_manifest_from_text(
        json.dumps(
            {
                "scopes": {
                    "tenant": ["im:message:send_as_bot", "im:message", "im:message", "im:resource"],
                    "user": ["im:chat.access_event.bot_p2p_chat:read"],
                }
            }
        )
    )
    assert manifest.tenant == ["im:message", "im:message:send_as_bot", "im:resource"]
    assert manifest.user == ["im:chat.access_event.bot_p2p_chat:read"]


def test_load_scope_manifest_from_text_rejects_bad_shape():
    with pytest.raises(ValueError, match="`scopes`"):
        load_scope_manifest_from_text(json.dumps({"tenant": []}))


def test_analyze_scope_manifest_reports_missing_required_scopes():
    manifest = load_scope_manifest_from_text(
        json.dumps(
            {
                "scopes": {
                    "tenant": [
                        "cardkit:card:write",
                        "im:message",
                        "im:message:send_as_bot",
                        "im:resource",
                    ],
                    "user": [],
                }
            }
        )
    )
    analysis = analyze_scope_manifest(manifest)
    assert analysis.required_missing == ["im:message:readonly"]
    assert "im:message.group_msg" in analysis.recommended_missing


@pytest.mark.anyio
async def test_channel_feishu_setup_writes_bundle(tmp_path):
    manifest_path = anyio.Path(str(tmp_path / "scopes.json"))
    output_dir = str(tmp_path / "bundle")
    await manifest_path.write_text(
        json.dumps(
            {
                "scopes": {
                    "tenant": [
                        "cardkit:card:write",
                        "im:chat",
                        "im:message",
                        "im:message:readonly",
                        "im:message:send_as_bot",
                        "im:resource",
                    ],
                    "user": ["im:chat.access_event.bot_p2p_chat:read"],
                }
            }
        ),
        encoding="utf-8",
    )

    cmd = ChannelFeishuSetup(
        scopes_json=str(manifest_path),
        output_dir=output_dir,
        app_id="cli_test_app",
        validate_credentials=False,
        session_socket="http://127.0.0.1:9900",
        workspace="./examples/haitun-workspace",
    )
    await cmd.run()

    report_text = await anyio.Path(f"{output_dir}/report.json").read_text(encoding="utf-8")
    next_steps = await anyio.Path(f"{output_dir}/NEXT_STEPS.md").read_text(encoding="utf-8")
    powershell = await anyio.Path(f"{output_dir}/start-feishu.ps1.example").read_text(encoding="utf-8")

    report = json.loads(report_text)
    assert report["analysis"]["required_missing"] == []
    assert report["credential_validation"]["app_access_token"]["message"] == "Credential probe disabled."
    assert "uv run psi-agent channel feishu --session-socket \"http://127.0.0.1:9900\"" in next_steps
    assert "$env:PSI_FEISHU_APP_ID = \"cli_test_app\"" in powershell
