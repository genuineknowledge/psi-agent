"""iFLYTEK streaming speech-to-text implementation for the Haitun workspace."""

from __future__ import annotations

import base64
import io
import json
import os
import wave
from dataclasses import dataclass
from typing import Any

import _xfyun_speech as speech
import aiohttp
import anyio

_FRAME_BYTES = 1280
_FRAME_INTERVAL_SECONDS = 0.04
_MAX_DURATION_SECONDS = 60.0


@dataclass(frozen=True)
class AudioPayload:
    content: bytes
    encoding: str
    sample_rate: int
    duration_seconds: float | None


class TranscriptAssembler:
    """Apply normal and dynamic-correction STT result segments."""

    def __init__(self) -> None:
        self._segments: dict[int, str] = {}

    def apply(self, result: dict[str, Any]) -> None:
        words: list[str] = []
        raw_segments = result.get("ws")
        if isinstance(raw_segments, list):
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    continue
                candidates = raw_segment.get("cw")
                if not isinstance(candidates, list) or not candidates:
                    continue
                first = candidates[0]
                if isinstance(first, dict):
                    words.append(str(first.get("w", "")))

        raw_sn = result.get("sn")
        sn = raw_sn if isinstance(raw_sn, int) else max(self._segments, default=0) + 1
        if result.get("pgs") == "rpl":
            replace_range = result.get("rg")
            if (
                isinstance(replace_range, list)
                and len(replace_range) == 2
                and all(isinstance(item, int) for item in replace_range)
            ):
                start, end = replace_range
                for key in list(self._segments):
                    if start <= key <= end:
                        self._segments.pop(key)
        self._segments[sn] = "".join(words)

    @property
    def text(self) -> str:
        return "".join(self._segments[key] for key in sorted(self._segments))


def _decode_wav(content: bytes) -> tuple[bytes, int, float]:
    with wave.open(io.BytesIO(content), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        if channels != 1 or sample_width != 2 or sample_rate not in {8000, 16000}:
            raise ValueError("WAV must be mono, 16-bit PCM, at 8000 or 16000 Hz.")
        frames = wav_file.readframes(frame_count)
    return frames, sample_rate, frame_count / sample_rate


async def _load_audio(audio_path: str, sample_rate: int) -> AudioPayload:
    path = anyio.Path(audio_path)
    if not await path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    content = await path.read_bytes()
    suffix = os.path.splitext(audio_path)[1].lower()
    if suffix == ".wav":
        frames, wav_rate, duration = _decode_wav(content)
        return AudioPayload(frames, "raw", wav_rate, duration)
    if suffix == ".pcm":
        return AudioPayload(content, "raw", sample_rate, len(content) / (sample_rate * 2))
    if suffix == ".mp3":
        return AudioPayload(content, "lame", sample_rate, None)
    raise ValueError("Supported STT files: .wav, .pcm, and .mp3.")


async def transcribe_impl(
    *,
    audio_path: str,
    language: str,
    accent: str,
    sample_rate: int,
) -> speech.SpeechResult:
    config = speech.read_stt_config()
    if not config.ready:
        return speech.SpeechResult(ok=False, message=config.not_ready_message())
    if sample_rate not in {8000, 16000}:
        return speech.SpeechResult(ok=False, message="sample_rate must be 8000 or 16000.")

    resolved_path = _resolve_path(audio_path)
    try:
        audio = await _load_audio(resolved_path, sample_rate)
    except (OSError, ValueError) as exc:
        return speech.SpeechResult(ok=False, message=str(exc), path=resolved_path)
    if audio.duration_seconds is not None and audio.duration_seconds > _MAX_DURATION_SECONDS:
        return speech.SpeechResult(
            ok=False,
            message=f"iFLYTEK streaming STT accepts at most 60 seconds; got {audio.duration_seconds:.2f}.",
            path=resolved_path,
        )

    assembler = TranscriptAssembler()
    sid = ""
    done = anyio.Event()
    error: list[Exception] = []
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(speech.build_signed_url(config)) as websocket,
        ):

            async def receive() -> None:
                nonlocal sid
                async for message in websocket:
                    if message.type == aiohttp.WSMsgType.ERROR:
                        error.append(RuntimeError("iFLYTEK STT WebSocket transport failed."))
                        done.set()
                        return
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
                        error.append(speech.XfyunApiError(code, str(response.get("message", "")), sid))
                        done.set()
                        return
                    data = response.get("data")
                    if not isinstance(data, dict):
                        continue
                    result = data.get("result")
                    if isinstance(result, dict):
                        assembler.apply(result)
                    if data.get("status") == 2:
                        done.set()
                        return

            async with anyio.create_task_group() as task_group:
                task_group.start_soon(receive)
                chunks = [
                    audio.content[index : index + _FRAME_BYTES] for index in range(0, len(audio.content), _FRAME_BYTES)
                ]
                if not chunks:
                    task_group.cancel_scope.cancel()
                    return speech.SpeechResult(ok=False, message="Audio file is empty.", path=resolved_path)
                business: dict[str, object] = {
                    "language": language,
                    "domain": speech.STT_DOMAIN,
                    "accent": accent,
                    "dwa": "wpgs" if language == "zh_cn" else "",
                    "ptt": 1,
                    "eos": 3000,
                }
                for index, chunk in enumerate(chunks):
                    payload: dict[str, object] = {
                        "data": {
                            "status": 0 if index == 0 else 1,
                            "format": f"audio/L16;rate={audio.sample_rate}",
                            "encoding": audio.encoding,
                            "audio": base64.b64encode(chunk).decode(),
                        }
                    }
                    if index == 0:
                        payload["common"] = {"app_id": config.app_id}
                        payload["business"] = business
                    await websocket.send_json(payload)
                    await anyio.sleep(_FRAME_INTERVAL_SECONDS)
                await websocket.send_json(
                    {
                        "data": {
                            "status": 2,
                            "format": f"audio/L16;rate={audio.sample_rate}",
                            "encoding": audio.encoding,
                            "audio": "",
                        }
                    }
                )
                with anyio.fail_after(90):
                    await done.wait()
                task_group.cancel_scope.cancel()
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, speech.XfyunApiError) as exc:
        return speech.SpeechResult(ok=False, message=str(exc), path=resolved_path, sid=sid)

    if error:
        return speech.SpeechResult(ok=False, message=str(error[0]), path=resolved_path, sid=sid)
    return speech.SpeechResult(
        ok=True,
        message="Transcription completed.",
        text=assembler.text,
        path=resolved_path,
        sid=sid,
    )


def _resolve_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))
