---
name: text-to-speech
description: Generate an MP3 through iFLYTEK text_to_speech and deliver it with [SEND:]. LOAD when the user asks to read text aloud, synthesize speech, or create a voice file.
category: media
---

# Text to speech（讯飞在线语音合成）

## 使用时机

- 用户要求“读出来”“念出来”“文字转语音”或 TTS。
- 用户需要一个可以下载、发送或播放的语音文件。

## 操作步骤

1. 整理要朗读的纯文本。
2. 调用 `text_to_speech(text=...)`。
3. 解析工具返回的 JSON。
4. `ok: true` 时，用返回的绝对 `path` 单独输出 `[SEND:<path>]`。
5. `ok: false` 时说明 `message`，不要假装生成成功。

## 固定接口与环境变量

Tool 固定调用讯飞在线语音合成：

- Endpoint：`wss://tts-api.xfyun.cn/v2/tts`
- 输出：MP3，16kHz
- 默认基础发音人 VCN：`xiaoyan`

凭证只从 Gateway 进程环境读取：

- `XFYUN_TTS_APP_ID`
- `XFYUN_TTS_API_KEY`
- `XFYUN_TTS_API_SECRET`

也支持共享回退：`XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`。

非默认 `voice` 必须已经在讯飞控制台授权。密钥不得写入 Skill、Tool、提交记录或聊天内容。

## 前端关系

Tool 在 workspace 中生成 MP3，Agent 通过 `[SEND:绝对路径]` 交给现有 Channel。后续前端配置页面只需把凭证注入同名进程环境变量，不需要改变 Tool 签名。
