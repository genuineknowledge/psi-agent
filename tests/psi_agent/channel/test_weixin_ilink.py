from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import socket
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from psi_agent.channel.weixin_ilink import (
    WeixinIlinkClient,
    WeixinIlinkCredentials,
    WeixinIlinkState,
    credentials_from_weixin_ilink_status,
    extract_weixin_ilink_messages,
    extract_weixin_reply_media,
    load_weixin_ilink_credentials,
    login_weixin_ilink_by_qr,
    normalize_weixin_ilink_account_id,
    poll_weixin_ilink_once,
    recent_weixin_ilink_tokens,
    resolve_weixin_ilink_credentials,
    save_weixin_ilink_credentials,
)
from psi_agent.errors import UserFacingError


def _encrypt_test_weixin_payload(data: bytes, aes_key: bytes) -> bytes:
    padding = 16 - (len(data) % 16)
    padded = data + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def test_extract_weixin_ilink_text_message_updates_peer_and_context() -> None:
    messages = extract_weixin_ilink_messages(
        {
            "msgs": [
                {
                    "from_user_id": "user-1",
                    "to_user_id": "account-1",
                    "msg_id": "msg-1",
                    "context_token": "ctx-1",
                    "item_list": [{"type": 1, "text_item": {"text": "hello weixin"}}],
                }
            ]
        },
        account_id="account-1",
    )

    assert len(messages) == 1
    assert messages[0].peer_id == "user-1"
    assert messages[0].sender_id == "user-1"
    assert messages[0].message_id == "msg-1"
    assert messages[0].context_token == "ctx-1"
    assert messages[0].text == "hello weixin"


def test_extract_weixin_reply_media_removes_media_lines(tmp_path) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4\n")

    text, media = extract_weixin_reply_media(
        f"请查收报告。\nMEDIA:{report}\n谢谢",
        roots=[tmp_path],
    )

    assert text == "请查收报告。\n谢谢"
    assert len(media) == 1
    assert media[0].path == report
    assert media[0].display_name == "report.pdf"


def test_extract_weixin_reply_media_rejects_unsupported_extension(tmp_path) -> None:
    tex = tmp_path / "paper.tex"
    tex.write_text(r"\section{Draft}", encoding="utf-8")

    text, media = extract_weixin_reply_media(f"MEDIA:{tex}", roots=[tmp_path])

    assert text == "Weixin cannot send this file type: .tex (paper.tex)"
    assert media == []


def test_weixin_ilink_credentials_from_status_normalizes_storage_id() -> None:
    credentials = credentials_from_weixin_ilink_status(
        {
            "status": "confirmed",
            "bot_token": "token-1",
            "ilink_bot_id": "abc@im.bot",
            "baseurl": "https://redirect.example",
            "ilink_user_id": "user-1",
        },
        base_url="https://ilinkai.weixin.qq.com",
    )

    assert credentials.token == "token-1"
    assert credentials.account_id == "abc@im.bot"
    assert credentials.storage_id == "abc-im-bot"
    assert credentials.base_url == "https://redirect.example"
    assert credentials.user_id == "user-1"
    assert normalize_weixin_ilink_account_id("abc@im.wechat") == "abc-im-wechat"


def test_weixin_ilink_credentials_round_trip(tmp_path) -> None:
    credentials = WeixinIlinkCredentials(
        token="token-1",
        account_id="abc@im.bot",
        base_url="https://redirect.example",
        user_id="user-1",
        storage_id="abc-im-bot",
    )

    save_weixin_ilink_credentials(tmp_path, credentials)
    loaded = load_weixin_ilink_credentials(tmp_path)

    assert loaded == WeixinIlinkCredentials(
        token="token-1",
        account_id="abc@im.bot",
        base_url="https://redirect.example",
        user_id="user-1",
        storage_id="abc-im-bot",
        saved_at=loaded.saved_at if loaded else "",
    )
    assert recent_weixin_ilink_tokens(tmp_path) == ["token-1"]
    assert resolve_weixin_ilink_credentials(state_dir=tmp_path).token == "token-1"


@pytest.mark.anyio
async def test_weixin_ilink_qr_login_saves_credentials(tmp_path) -> None:
    qrcode_requests: list[dict[str, object]] = []
    status_queries: list[str] = []

    async def qrcode_handler(request: web.Request) -> web.Response:
        qrcode_requests.append(await request.json())
        assert request.query["bot_type"] == "3"
        return web.json_response(
            {
                "ret": 0,
                "qrcode": "qr-1",
                "qrcode_img_content": "https://qr.example/1",
            }
        )

    async def status_handler(request: web.Request) -> web.Response:
        status_queries.append(request.query_string)
        assert request.query["qrcode"] == "qr-1"
        return web.json_response(
            {
                "ret": 0,
                "status": "confirmed",
                "bot_token": "token-1",
                "ilink_bot_id": "abc@im.bot",
                "baseurl": "https://redirect.example",
                "ilink_user_id": "user-1",
            }
        )

    async with _TcpServer(
        [
            ("POST", "/ilink/bot/get_bot_qrcode", qrcode_handler),
            ("GET", "/ilink/bot/get_qrcode_status", status_handler),
        ]
    ) as ilink_base_url:
        credentials = await login_weixin_ilink_by_qr(
            state_dir=tmp_path,
            base_url=ilink_base_url,
            timeout_seconds=5,
            poll_interval_seconds=0.01,
        )

    assert qrcode_requests == [{"local_token_list": [], "base_info": {"channel_version": "2.2.0"}}]
    assert status_queries == ["qrcode=qr-1"]
    assert credentials.token == "token-1"
    assert credentials.account_id == "abc@im.bot"
    assert load_weixin_ilink_credentials(tmp_path) == credentials


@pytest.mark.anyio
async def test_weixin_ilink_poll_calls_session_and_sendmessage() -> None:
    session_payloads: list[dict[str, object]] = []
    getupdates_payloads: list[dict[str, object]] = []
    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"reasoning_content": "hidden", "content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        getupdates_payloads.append(await request.json())
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "hello weixin"}}],
                    }
                ],
            }
        )

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        state = WeixinIlinkState(sync_buf="sync-prev")
        messages = await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=state,
            session=http_session,
            timeout_ms=1000,
        )

    assert [message.text for message in messages] == ["hello weixin"]
    assert state.sync_buf == "sync-next"
    assert state.context_tokens == {"user-1": "ctx-1"}
    assert session_payloads[0]["messages"] == [{"role": "user", "content": "hello weixin"}]
    assert getupdates_payloads[0]["get_updates_buf"] == "sync-prev"
    assert getupdates_payloads[0]["base_info"] == {"channel_version": "2.2.0"}

    sent_msg = cast(dict[str, Any], sendmessage_payloads[0]["msg"])
    assert isinstance(sent_msg, dict)
    assert sent_msg["to_user_id"] == "user-1"
    assert sent_msg["message_type"] == 2
    assert sent_msg["message_state"] == 2
    assert sent_msg["context_token"] == "ctx-1"
    assert sent_msg["item_list"] == [{"type": 1, "text_item": {"text": "reply text"}}]


@pytest.mark.anyio
async def test_weixin_ilink_poll_uploads_and_sends_media_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 report bytes\n")
    monkeypatch.setenv("WEIXIN_MEDIA_ROOTS", str(tmp_path))

    session_payloads: list[dict[str, object]] = []
    getupdates_payloads: list[dict[str, object]] = []
    upload_url_payloads: list[dict[str, object]] = []
    upload_bodies: list[bytes] = []
    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"请查收\nMEDIA:{report}\n"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        getupdates_payloads.append(await request.json())
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "send report"}}],
                    }
                ],
            }
        )

    async def getuploadurl_handler(request: web.Request) -> web.Response:
        upload_url_payloads.append(await request.json())
        return web.json_response(
            {
                "ret": 0,
                "upload_full_url": f"{ilink_base_url}/cdn/upload",
                "upload_param": "upload-param-1",
            }
        )

    async def upload_handler(request: web.Request) -> web.Response:
        upload_bodies.append(await request.read())
        return web.json_response({"ret": 0, "encrypt_query_param": "encrypted-param-1"})

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/getuploadurl", getuploadurl_handler),
                ("POST", "/cdn/upload", upload_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        state = WeixinIlinkState(sync_buf="sync-prev")
        messages = await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=state,
            session=http_session,
            timeout_ms=1000,
        )

    assert [message.text for message in messages] == ["send report"]
    assert session_payloads[0]["messages"] == [{"role": "user", "content": "send report"}]
    assert getupdates_payloads[0]["get_updates_buf"] == "sync-prev"
    assert re.fullmatch(r"[0-9a-f]{32}", cast(str, upload_url_payloads[0]["filekey"]))
    assert upload_url_payloads[0]["media_type"] == 3
    assert upload_url_payloads[0]["rawsize"] == report.stat().st_size
    assert upload_url_payloads[0]["rawfilemd5"]
    assert upload_url_payloads[0]["filesize"] == len(upload_bodies[0])
    assert re.fullmatch(r"[0-9a-f]{32}", cast(str, upload_url_payloads[0]["aeskey"]))
    assert upload_url_payloads[0]["no_need_thumb"] is True
    assert upload_bodies
    assert len(upload_bodies[0]) % 16 == 0
    assert len(sendmessage_payloads) == 2
    assert cast(dict[str, Any], sendmessage_payloads[0]["msg"])["item_list"] == [
        {"type": 1, "text_item": {"text": "请查收"}}
    ]

    sent_msg = cast(dict[str, Any], sendmessage_payloads[1]["msg"])
    assert sent_msg["to_user_id"] == "user-1"
    assert sent_msg["context_token"] == "ctx-1"
    assert sent_msg["item_list"][0]["type"] == 4
    file_item = sent_msg["item_list"][0]["file_item"]
    assert file_item["file_name"] == "report.pdf"
    assert file_item["len"] == str(report.stat().st_size)
    aes_key = cast(str, file_item["media"]["aes_key"])
    assert re.fullmatch(r"[0-9a-f]{32}", base64.b64decode(aes_key).decode("ascii"))
    assert file_item["media"]["encrypt_query_param"] == "encrypted-param-1"


@pytest.mark.anyio
async def test_weixin_ilink_poll_retries_markdown_media_as_txt(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    guide = tmp_path / "AGENTS.md"
    guide.write_text("# AGENTS\n\nhello\n", encoding="utf-8")
    monkeypatch.setenv("WEIXIN_MEDIA_ROOTS", str(tmp_path))

    upload_url_payloads: list[dict[str, object]] = []
    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        _ = await request.json()
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"MEDIA:{guide}\n"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "send guide"}}],
                    }
                ],
            }
        )

    async def getuploadurl_handler(request: web.Request) -> web.Response:
        payload = await request.json()
        upload_url_payloads.append(payload)
        if len(upload_url_payloads) == 1:
            return web.json_response({"ret": -2})
        return web.json_response(
            {
                "ret": 0,
                "upload_full_url": f"{ilink_base_url}/cdn/upload",
                "upload_param": "upload-param-1",
            }
        )

    async def upload_handler(request: web.Request) -> web.Response:
        _ = await request.read()
        return web.json_response({"ret": 0, "encrypt_query_param": "encrypted-param-1"})

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/getuploadurl", getuploadurl_handler),
                ("POST", "/cdn/upload", upload_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=WeixinIlinkState(sync_buf="sync-prev"),
            session=http_session,
            timeout_ms=1000,
        )

    assert len(upload_url_payloads) == 2
    assert all(re.fullmatch(r"[0-9a-f]{32}", cast(str, payload["filekey"])) for payload in upload_url_payloads)
    assert len(sendmessage_payloads) == 1
    sent_msg = cast(dict[str, Any], sendmessage_payloads[0]["msg"])
    file_item = sent_msg["item_list"][0]["file_item"]
    assert file_item["file_name"] == "AGENTS.txt"
    assert file_item["len"] == str(guide.stat().st_size)


@pytest.mark.anyio
async def test_weixin_ilink_poll_uses_upload_response_header_when_full_url_has_query(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 report bytes\n")
    monkeypatch.setenv("WEIXIN_MEDIA_ROOTS", str(tmp_path))

    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        _ = await request.json()
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"MEDIA:{report}\n"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "send report"}}],
                    }
                ],
            }
        )

    async def getuploadurl_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "upload_full_url": f"{ilink_base_url}/cdn/upload?encrypt_query_param=url-param-1",
            }
        )

    async def upload_handler(request: web.Request) -> web.Response:
        _ = await request.read()
        return web.Response(status=200, headers={"x-encrypted-param": "header-param-1"})

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/getuploadurl", getuploadurl_handler),
                ("POST", "/cdn/upload", upload_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=WeixinIlinkState(sync_buf="sync-prev"),
            session=http_session,
            timeout_ms=1000,
        )

    sent_msg = cast(dict[str, Any], sendmessage_payloads[0]["msg"])
    file_item = sent_msg["item_list"][0]["file_item"]
    assert file_item["media"]["encrypt_query_param"] == "header-param-1"


@pytest.mark.anyio
async def test_weixin_ilink_poll_builds_cdn_upload_url_and_uses_response_header(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 report bytes\n")
    monkeypatch.setenv("WEIXIN_MEDIA_ROOTS", str(tmp_path))

    upload_paths: list[str] = []
    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        _ = await request.json()
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"MEDIA:{report}\n"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "send report"}}],
                    }
                ],
            }
        )

    async def getuploadurl_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "upload_param": "upload param/with+chars=",
                "cdn_base_url": ilink_base_url,
            }
        )

    async def upload_handler(request: web.Request) -> web.Response:
        upload_paths.append(request.raw_path)
        _ = await request.read()
        return web.Response(status=200, headers={"x-encrypted-param": "header-param-1"})

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/getuploadurl", getuploadurl_handler),
                ("POST", "/upload", upload_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=WeixinIlinkState(sync_buf="sync-prev"),
            session=http_session,
            timeout_ms=1000,
        )

    assert upload_paths
    assert upload_paths[0].startswith("/upload?")
    query = parse_qs(urlsplit(upload_paths[0]).query)
    assert query["encrypted_query_param"] == ["upload param/with+chars="]
    assert re.fullmatch(r"[0-9a-f]{32}", query["filekey"][0])
    sent_msg = cast(dict[str, Any], sendmessage_payloads[0]["msg"])
    file_item = sent_msg["item_list"][0]["file_item"]
    assert file_item["media"]["encrypt_query_param"] == "header-param-1"


@pytest.mark.anyio
async def test_weixin_ilink_poll_downloads_inbound_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEIXIN_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    session_payloads: list[dict[str, object]] = []
    download_bodies = [b"%PDF-1.4 inbound report\n"]
    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "received"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [
                            {"type": 1, "text_item": {"text": "请看文件"}},
                            {
                                "type": 4,
                                "file_item": {
                                    "file_name": "report.pdf",
                                    "len": len(download_bodies[0]),
                                    "md5": "md5-1",
                                    "download_url": f"{ilink_base_url}/cdn/download/report.pdf",
                                },
                            },
                        ],
                    }
                ],
            }
        )

    async def download_handler(request: web.Request) -> web.Response:
        return web.Response(body=download_bodies[0], content_type="application/pdf")

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("GET", "/cdn/download/report.pdf", download_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        messages = await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=WeixinIlinkState(sync_buf="sync-prev"),
            session=http_session,
            timeout_ms=1000,
        )

    assert len(messages) == 1
    inbound_text = cast(list[dict[str, str]], session_payloads[0]["messages"])[0]["content"]
    assert "请看文件" in inbound_text
    assert "FILE:" in inbound_text
    assert "report.pdf" in inbound_text
    downloaded_path = tmp_path / "downloads" / "user-1" / "msg-1" / "report.pdf"
    assert downloaded_path.read_bytes() == download_bodies[0]
    assert sendmessage_payloads


@pytest.mark.anyio
async def test_weixin_ilink_poll_downloads_inbound_file_from_cdn_media(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEIXIN_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    session_payloads: list[dict[str, object]] = []
    plaintext = b"%PDF-1.4 inbound report via cdn\n"
    aes_key = secrets.token_bytes(16)
    encrypted_body = _encrypt_test_weixin_payload(plaintext, aes_key)

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [
                            {
                                "type": 4,
                                "file_item": {
                                    "file_name": "__agent_1.pdf",
                                    "len": str(len(plaintext)),
                                    "md5": hashlib.md5(plaintext).hexdigest(),
                                    "media": {
                                        "encrypt_query_param": "download-param-1",
                                        "aes_key": base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii"),
                                        "encrypt_type": 1,
                                    },
                                },
                            }
                        ],
                    }
                ],
            }
        )

    async def download_handler(request: web.Request) -> web.Response:
        assert request.query["encrypted_query_param"] == "download-param-1"
        return web.Response(body=encrypted_body, content_type="application/octet-stream")

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("GET", "/download", download_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        monkeypatch.setenv("WEIXIN_CDN_BASE_URL", ilink_base_url)
        messages = await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=WeixinIlinkState(sync_buf="sync-prev"),
            session=http_session,
            timeout_ms=1000,
        )

    assert len(messages) == 1
    inbound_text = cast(list[dict[str, str]], session_payloads[0]["messages"])[0]["content"]
    assert "FILE:" in inbound_text
    assert "__agent_1.pdf" in inbound_text
    downloaded_path = tmp_path / "downloads" / "user-1" / "msg-1" / "__agent_1.pdf"
    assert downloaded_path.read_bytes() == plaintext


@pytest.mark.anyio
async def test_weixin_ilink_poll_reports_inbound_file_download_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEIXIN_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    session_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "item_list": [
                            {
                                "type": 4,
                                "file_item": {
                                    "file_name": "report.pdf",
                                    "len": 123,
                                    "md5": "md5-1",
                                },
                            }
                        ],
                    }
                ],
            }
        )

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer([("POST", "/ilink/bot/getupdates", getupdates_handler)]) as ilink_base_url,
        ClientSession() as http_session,
    ):
        messages = await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=WeixinIlinkState(sync_buf="sync-prev"),
            session=http_session,
            timeout_ms=1000,
        )

    assert len(messages) == 1
    inbound_text = cast(list[dict[str, str]], session_payloads[0]["messages"])[0]["content"]
    assert "report.pdf" in inbound_text
    assert "missing download url" in inbound_text


@pytest.mark.anyio
async def test_weixin_ilink_poll_raises_on_sendmessage_error() -> None:
    async def session_handler(request: web.Request) -> web.StreamResponse:
        _ = await request.json()
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "item_list": [{"type": 1, "text_item": {"text": "hello weixin"}}],
                    }
                ],
            }
        )

    async def sendmessage_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response({"ret": 1, "errmsg": "send failed"})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        with pytest.raises(UserFacingError, match="send failed"):
            await poll_weixin_ilink_once(
                session_socket=f"{session_base_url}/v1",
                client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
                state=WeixinIlinkState(sync_buf="sync-prev"),
                session=http_session,
                timeout_ms=1000,
            )


class _TcpServer:
    def __init__(self, routes: list[tuple[str, str, Callable[[web.Request], Awaitable[web.StreamResponse]]]]) -> None:
        self._routes = routes
        self._runner: web.AppRunner | None = None
        self._url = ""

    async def __aenter__(self) -> str:
        app = web.Application()
        for method, path, handler in self._routes:
            app.router.add_route(method, path, handler)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock, self._url = _bind_localhost()
        site = web.SockSite(self._runner, sock)
        await site.start()
        return self._url

    async def __aexit__(self, *_args: object) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


def _bind_localhost() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, f"http://127.0.0.1:{port}"
