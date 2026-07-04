"""Tests for Haitun workspace iFLYTEK STT and TTS tools."""

from __future__ import annotations

import base64
import importlib
import io
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

speech: Any = importlib.import_module("_xfyun_speech")
stt: Any = importlib.import_module("_xfyun_stt")
tts: Any = importlib.import_module("_xfyun_tts")
stt_tool: Any = importlib.import_module("speech_to_text")
tts_tool: Any = importlib.import_module("text_to_speech")


class _FakeWebSocket:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._messages = [
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(response)) for response in responses
        ]
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> Any:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def __aenter__(self) -> _FakeWebSocket:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, websocket: _FakeWebSocket) -> None:
        self.websocket = websocket
        self.last_url = ""

    def ws_connect(self, url: str) -> _FakeWebSocket:
        self.last_url = url
        return self.websocket

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def clear_xfyun_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "XFYUN_APP_ID",
        "XFYUN_API_KEY",
        "XFYUN_API_SECRET",
        "XFYUN_STT_APP_ID",
        "XFYUN_STT_API_KEY",
        "XFYUN_STT_API_SECRET",
        "XFYUN_TTS_APP_ID",
        "XFYUN_TTS_API_KEY",
        "XFYUN_TTS_API_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 640)
    return buffer.getvalue()


def _configure_stt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XFYUN_STT_APP_ID", "stt-app")
    monkeypatch.setenv("XFYUN_STT_API_KEY", "stt-key")
    monkeypatch.setenv("XFYUN_STT_API_SECRET", "stt-secret")


def _configure_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XFYUN_TTS_APP_ID", "tts-app")
    monkeypatch.setenv("XFYUN_TTS_API_KEY", "tts-key")
    monkeypatch.setenv("XFYUN_TTS_API_SECRET", "tts-secret")


def test_tool_docstrings_publish_environment_contract() -> None:
    stt_metadata = ToolFunction.from_callable(stt_tool.speech_to_text)
    tts_metadata = ToolFunction.from_callable(tts_tool.text_to_speech)
    assert "XFYUN_STT_APP_ID" in stt_metadata.description
    assert "XFYUN_STT_API_KEY" in stt_metadata.description
    assert "XFYUN_STT_API_SECRET" in stt_metadata.description
    assert "XFYUN_TTS_APP_ID" in tts_metadata.description
    assert "XFYUN_TTS_API_KEY" in tts_metadata.description
    assert "XFYUN_TTS_API_SECRET" in tts_metadata.description


@pytest.mark.anyio
async def test_stt_tool_reports_missing_environment(
    tmp_path: Path,
    clear_xfyun_env: None,
) -> None:
    audio = tmp_path / "test.wav"
    audio.write_bytes(_wav_bytes())
    data = json.loads(await stt_tool.speech_to_text(str(audio)))
    assert data["ok"] is False
    assert "XFYUN_STT_APP_ID" in data["message"]


@pytest.mark.anyio
async def test_tts_tool_reports_missing_environment(clear_xfyun_env: None) -> None:
    data = json.loads(await tts_tool.text_to_speech("hello"))
    assert data["ok"] is False
    assert "XFYUN_TTS_APP_ID" in data["message"]


@pytest.mark.anyio
async def test_stt_mock_success(
    tmp_path: Path,
    clear_xfyun_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_stt(monkeypatch)
    audio = tmp_path / "test.wav"
    audio.write_bytes(_wav_bytes())
    websocket = _FakeWebSocket(
        [
            {
                "code": 0,
                "sid": "stt-sid",
                "data": {
                    "status": 1,
                    "result": {"sn": 1, "ws": [{"cw": [{"w": "你好"}]}]},
                },
            },
            {"code": 0, "sid": "stt-sid", "data": {"status": 2}},
        ]
    )
    session = _FakeSession(websocket)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: session)

    result = await stt.transcribe_impl(
        audio_path=str(audio),
        language="zh_cn",
        accent="mandarin",
        sample_rate=16000,
    )
    assert result.ok is True
    assert result.text == "你好"
    assert result.sid == "stt-sid"
    assert session.last_url.startswith(speech.STT_ENDPOINT)
    assert websocket.sent[0]["common"]["app_id"] == "stt-app"
    assert websocket.sent[0]["business"]["domain"] == "iat"
    assert websocket.sent[-1]["data"]["status"] == 2


@pytest.mark.anyio
async def test_tts_mock_success(
    tmp_path: Path,
    clear_xfyun_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_tts(monkeypatch)
    websocket = _FakeWebSocket(
        [
            {
                "code": 0,
                "sid": "tts-sid",
                "data": {
                    "audio": base64.b64encode(b"abc").decode(),
                    "status": 1,
                },
            },
            {
                "code": 0,
                "sid": "tts-sid",
                "data": {
                    "audio": base64.b64encode(b"def").decode(),
                    "status": 2,
                },
            },
        ]
    )
    session = _FakeSession(websocket)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: session)
    output = tmp_path / "speech.mp3"

    result = await tts.synthesize_impl(
        text="你好",
        output_path=str(output),
        voice="xiaoyan",
        speed=50,
        volume=50,
        pitch=50,
    )
    assert result.ok is True
    assert result.path == str(output)
    assert output.read_bytes() == b"abcdef"
    assert session.last_url.startswith(speech.TTS_ENDPOINT)
    assert websocket.sent[0]["common"]["app_id"] == "tts-app"
    assert websocket.sent[0]["business"]["vcn"] == "xiaoyan"
    assert websocket.sent[0]["business"]["aue"] == "lame"
