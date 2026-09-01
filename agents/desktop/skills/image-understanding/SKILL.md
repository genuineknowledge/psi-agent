---
name: image-understanding
description: Image-to-text via describe_image — use image path + question, never read bitmaps for vision. LOAD when the user uploads an image or asks what is in a picture.
category: media
---

# Image understanding (图生文)

## 定义

**图生文 = `describe_image` tool**：agent 提供**图片绝对路径** + **要问什么**，tool 在 helper 内读字节并调 **第二套 VISION API**（BYOK）；未配置或接不上 → **`ok: false`**。

| 你做 | Tool 做 |
|------|---------|
| `[RECV:path]` → `image_path` | 校验路径、格式、大小 |
| 用户意图 → `question` | 读字节 → vision API |
| 用返回的 `text` 回答用户 | 返回 `ok`, `text`, `backend`, … |

**禁止：** 对 PNG/JPG 等位图用 `read` 当识图；**禁止**在调 tool 前自己编造「图像描述」当入参（那是幻觉，不是看图）。

---

## 何时使用

- 用户上传图片或给出 `[RECV:<absolute-path>]`  
- 「图里有什么」「读截图里的字」「描述这张图」  
- 后续任务依赖**像素内容**（非仅文件名）

**不用本 skill：** 用户要**生成新图**（用 `image-generation` + `generate_image`）；文件是纯文本/SVG 源码（可用 `read`）。

---

## 配方步骤

### Step 0 — 准备入参

| 入参 | 说明 |
|------|------|
| `image_path` | Channel 给出的**绝对路径**，或 workspace 内已确认存在的路径 |
| `question` | 要问什么；空字符串 = 默认「详细描述这张图」 |

**question 示例**

| 用户说 | `question`（示意） |
|--------|-------------------|
| 这张图是什么 | （留空，用默认） |
| 图里有几个字 | `Read and transcribe all visible text in this image.` |
| 适合当壁纸吗 | `Describe the scene and whether it works as a desktop wallpaper.` |

### Step 1 — 调用 `describe_image`

```text
describe_image(
  image_path="D:/absolute/path/to/upload.png",
  question=""
)
```

### Step 2 — 解析返回 JSON

| 字段 | 含义 |
|------|------|
| `ok` | `true` 才可引用 `text` |
| `text` | 模型生成的文字 |
| `backend` | 成功时为 `api` |
| `message` | 错误或补充说明 |
| `image_path` | 实际处理的绝对路径 |

`ok: false` → 说明 `message`（常见：多模态 API 未配置），不要捏造图内容。

### Step 3 — 回复用户

- 基于 **`text`** 组织回答，不添加 `text` 中未出现的细节。  
- `ok: false` 时：转述 `message`，引导用户检查第二套多模态 API 配置。

---

## 第二套多模态 API（BYOK；与 Gateway 主对话 AI 分离）

在 workspace 根目录配置 ``.env.multimodal``（``MINIMAX_API_KEY`` 等）；tool 自动加载。

| 变量 | 必填 | 说明 |
|------|------|------|
| `MINIMAX_API_KEY` | 是 | 按量付费「接口密钥」 |
| `MINIMAX_API_HOST` | 否 | 默认 `https://api.minimaxi.com` |
| `VISION_MODEL` | 否 | 默认 `MiniMax-M3` |

未配置、占位符、或 HTTP 失败 → **`ok: false`**。

---

## 责任边界

| 现象 | 层 |
|------|-----|
| 对位图用了 `read` | Skill / agent |
| `ok: false`, 多模态 API 未配置 | Tool / 环境 |
| 回答与 `text` 不符 | agent 幻觉 |
| `image not found` | 路径错误 |

---

## 与文生图的关系

| | 文生图 | 图生文 |
|--|--------|--------|
| Tool | `generate_image` | `describe_image` |
| 入参 | `description` (+ 可选参考图) | `image_path` + `question` |
| 出参 | 图片 `path` | `text` |
| API env | `IMAGE_GEN_*` / `MINIMAX_*` | `VISION_*` / `MINIMAX_*` |
