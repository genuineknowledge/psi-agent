---
name: speech-to-text
description: Transcribe an uploaded WAV, PCM, or MP3 file through iFLYTEK speech_to_text. LOAD when the user sends voice/audio or asks for STT, transcription, or speech recognition.
category: media
---

# Speech to text（讯飞语音听写）

## 使用时机

- 用户上传语音或音频，并要求转成文字。
- 消息中出现 `[RECV:<absolute-path>]`，且文件为 WAV、PCM 或 MP3。
- 用户明确要求 STT、语音识别或录音转写。

## 操作步骤

1. 从 `[RECV:]` 标记取得音频绝对路径。
2. 调用 `speech_to_text(audio_path=...)`。
3. 解析工具返回的 JSON。
4. 仅在 `ok: true` 时使用 `text` 回答用户；`ok: false` 时原样说明 `message`。

## 固定接口与环境变量

Tool 固定调用讯飞语音听写流式接口：

- Endpoint：`wss://iat-api.xfyun.cn/v2/iat`
- Domain：`iat`
- 最长音频：60秒

凭证只从 Gateway 进程环境读取，不允许作为 Tool 参数或聊天内容传入：

- `XFYUN_STT_APP_ID`
- `XFYUN_STT_API_KEY`
- `XFYUN_STT_API_SECRET`

也支持共享回退：`XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`。

## 前端关系

无需为本 Tool 单独连接上传接口。Web Console、飞书或 Telegram 接收文件后，会把文件保存到本机并生成 `[RECV:绝对路径]`；Agent 将该路径传给 Tool 即可。
