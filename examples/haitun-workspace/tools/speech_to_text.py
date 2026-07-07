"""Public Haitun workspace tool for iFLYTEK speech-to-text."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    import _xfyun_speech as speech
    import _xfyun_stt as stt
finally:
    sys.path.pop(0)


async def speech_to_text(
    audio_path: str,
    language: str = "zh_cn",
    accent: str = "mandarin",
    sample_rate: int = 16000,
) -> str:
    """Transcribe an uploaded audio file with iFLYTEK streaming STT.

    Uses the fixed iFLYTEK ``wss://iat-api.xfyun.cn/v2/iat`` endpoint and
    ``iat`` domain. Credentials are read only from process environment:
    ``XFYUN_STT_APP_ID``, ``XFYUN_STT_API_KEY``, ``XFYUN_STT_API_SECRET``;
    shared fallbacks are ``XFYUN_APP_ID``, ``XFYUN_API_KEY``,
    ``XFYUN_API_SECRET``. Never pass credentials as tool arguments.

    Frontend uploads already arrive as ``[RECV:<absolute-path>]``. Pass that
    absolute path here. Supported files are WAV (mono 16-bit, 8/16 kHz), PCM,
    and MP3, with a maximum duration of 60 seconds.

    Args:
        audio_path: Absolute path from the frontend ``[RECV:]`` marker.
        language: iFLYTEK language code; default ``zh_cn``.
        accent: iFLYTEK accent code; default ``mandarin``.
        sample_rate: PCM/MP3 sample rate, either 8000 or 16000.

    Returns:
        JSON with ok, text, path, backend, sid, and message.
    """
    result = await stt.transcribe_impl(
        audio_path=audio_path,
        language=language,
        accent=accent,
        sample_rate=sample_rate,
    )
    return speech.dumps_result(result)
