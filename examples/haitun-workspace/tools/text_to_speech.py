"""Public Haitun workspace tool for iFLYTEK text-to-speech."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    import _xfyun_speech as speech
    import _xfyun_tts as tts
finally:
    sys.path.pop(0)


async def text_to_speech(
    text: str,
    output_path: str = "",
    voice: str = "xiaoyan",
    speed: int = 50,
    volume: int = 50,
    pitch: int = 50,
) -> str:
    """Synthesize an MP3 file with iFLYTEK online TTS.

    Uses the fixed iFLYTEK ``wss://tts-api.xfyun.cn/v2/tts`` endpoint and
    standard online TTS service. Credentials are read only from process
    environment: ``XFYUN_TTS_APP_ID``, ``XFYUN_TTS_API_KEY``,
    ``XFYUN_TTS_API_SECRET``; shared fallbacks are ``XFYUN_APP_ID``,
    ``XFYUN_API_KEY``, ``XFYUN_API_SECRET``. Never pass credentials as tool
    arguments.

    The default voice is the fixed basic VCN ``xiaoyan``. A different
    ``voice`` must already be authorized in the iFLYTEK console. After a
    successful call, deliver the returned absolute ``path`` with ``[SEND:]``.

    Args:
        text: Text to synthesize; UTF-8 encoding must be under 8000 bytes.
        output_path: MP3 destination. Empty creates ``generated/audio/tts-*.mp3``.
        voice: Authorized iFLYTEK VCN; default ``xiaoyan``.
        speed: Speech speed from 0 to 100.
        volume: Speech volume from 0 to 100.
        pitch: Speech pitch from 0 to 100.

    Returns:
        JSON with ok, path, backend, sid, and message.
    """
    result = await tts.synthesize_impl(
        text=text,
        output_path=output_path,
        voice=voice,
        speed=speed,
        volume=volume,
        pitch=pitch,
    )
    return speech.dumps_result(result)
