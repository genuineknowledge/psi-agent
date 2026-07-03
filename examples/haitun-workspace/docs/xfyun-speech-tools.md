# iFLYTEK STT/TTS workspace tools

This workspace exposes two provider-specific tools:

| Tool | Fixed iFLYTEK service | Input | Output |
|---|---|---|---|
| `speech_to_text` | Streaming dictation `/v2/iat` | Local audio path | JSON text result |
| `text_to_speech` | Online TTS `/v2/tts` | Text and voice controls | JSON MP3 path |

The tools are backend-only workspace capabilities. They do not add a dedicated
frontend settings screen. Existing channel file transport is already sufficient:

- uploaded files become `[RECV:<absolute-path>]`;
- generated audio is returned with `[SEND:<absolute-path>]`.

## Environment contract

Production credentials are read from the Gateway process environment:

```text
XFYUN_STT_APP_ID
XFYUN_STT_API_KEY
XFYUN_STT_API_SECRET
XFYUN_TTS_APP_ID
XFYUN_TTS_API_KEY
XFYUN_TTS_API_SECRET
```

If STT and TTS use the same application credentials, the shared
`XFYUN_APP_ID`, `XFYUN_API_KEY`, and `XFYUN_API_SECRET` variables may be used.

`.env.xfyun.example` is documentation only. The tools intentionally do not
auto-load secret files. A future frontend may set these variables when spawning
the Gateway without changing the Tool API.

## Local development

Set variables in the shell that starts the Gateway. PowerShell example:

```powershell
$env:XFYUN_STT_APP_ID="..."
$env:XFYUN_STT_API_KEY="..."
$env:XFYUN_STT_API_SECRET="..."
$env:XFYUN_TTS_APP_ID="..."
$env:XFYUN_TTS_API_KEY="..."
$env:XFYUN_TTS_API_SECRET="..."
```

Unit tests mock WebSocket responses and never consume iFLYTEK quota.
