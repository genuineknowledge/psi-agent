"""iFLYTEK online text-to-speech implementation for the Haitun workspace."""

from __future__ import annotations

import base64
import json
import os
import uuid

import _xfyun_speech as speech
import aiohttp
import anyio

_MAX_TEXT_BYTES = 8000


async def synthesize_impl(
    *,
    text: str,
    output_path: str,
    voice: str,
    speed: int,
    volume: int,
    pitch: int,
) -> speech.SpeechResult:
    config = speech.read_tts_config()
    if not config.ready:
        return speech.SpeechResult(ok=False, message=config.not_ready_message())
    if not text:
        return speech.SpeechResult(ok=False, message="text is required.")
    text_bytes = text.encode()
    if len(text_bytes) >= _MAX_TEXT_BYTES:
        return speech.SpeechResult(
            ok=False,
            message=f"iFLYTEK online TTS accepts less than 8000 UTF-8 bytes; got {len(text_bytes)}.",
        )
    for name, value in (("speed", speed), ("volume", volume), ("pitch", pitch)):
        if not 0 <= value <= 100:
            return speech.SpeechResult(ok=False, message=f"{name} must be between 0 and 100.")

    target = output_path.strip()
    if not target:
        target = os.path.join("generated", "audio", f"tts-{uuid.uuid4().hex}.mp3")
    if not target.lower().endswith(".mp3"):
        target = f"{target}.mp3"
    resolved_path = _resolve_path(target)

    frames: list[bytes] = []
    sid = ""
    payload = {
        "common": {"app_id": config.app_id},
        "business": {
            "aue": "lame",
            "sfl": 1,
            "auf": "audio/L16;rate=16000",
            "vcn": voice or speech.TTS_VOICE,
            "speed": speed,
            "volume": volume,
            "pitch": pitch,
            "tte": "UTF8",
        },
        "data": {
            "status": 2,
            "text": base64.b64encode(text_bytes).decode(),
        },
    }
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(speech.build_signed_url(config)) as websocket,
        ):
            await websocket.send_json(payload)
            async for message in websocket:
                if message.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError("iFLYTEK TTS WebSocket transport failed.")
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                response = json.loads(message.data)
                if not isinstance(response, dict):
                    continue
                raw_sid = response.get("sid")
                if isinstance(raw_sid, str):
                    sid = raw_sid
                code = response.get("code", 0)
                if isinstance(code, int) and code:
                    raise speech.XfyunApiError(code, str(response.get("message", "")), sid)
                data = response.get("data")
                if not isinstance(data, dict):
                    continue
                audio = data.get("audio")
                if isinstance(audio, str) and audio:
                    frames.append(base64.b64decode(audio))
                if data.get("status") == 2:
                    break
    except (aiohttp.ClientError, RuntimeError, json.JSONDecodeError, speech.XfyunApiError) as exc:
        return speech.SpeechResult(ok=False, message=str(exc), path=resolved_path, sid=sid)

    content = b"".join(frames)
    if not content:
        return speech.SpeechResult(
            ok=False,
            message="iFLYTEK TTS returned no audio data.",
            path=resolved_path,
            sid=sid,
        )
    output = anyio.Path(resolved_path)
    await output.parent.mkdir(parents=True, exist_ok=True)
    await output.write_bytes(content)
    return speech.SpeechResult(
        ok=True,
        message="Speech synthesis completed.",
        path=resolved_path,
        sid=sid,
    )


def _resolve_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))
