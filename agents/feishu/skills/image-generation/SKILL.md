---
name: image-generation
description: Text-to-image (and reference-based edits) via generate_image — distill user intent to a scene description, call the tool, deliver with [SEND:]. LOAD when the user wants a picture, illustration, icon, or image file.
category: media
---

# Image generation (文生图)

## 定义

**文生图 = `generate_image` tool**：agent 提供**画面描述**（+ 可选参考图路径），tool 经 **第二套 IMAGE_GEN API**（BYOK）出图；未配置或接不上 → **`ok: false`**。

| 你做 | Tool 做 |
|------|---------|
| 用户话 → `description` | 校验入参、读 IMAGE_GEN env、调 API |
| 可选：参考图路径 → `reference_images` | 调 `/images/generations`（文生图） |
| 读 JSON → 说明 + `[SEND:path]` 或报错 | 返回 `ok`, `path`, `backend`, … |

**禁止：** 把用户原话（「请帮我画一只猫」）直接塞进 `description`；**禁止**用 `read` / `bash` 代替生图；**禁止**在对话里贴大图字节代替文件交付。

---

## 何时使用

- 用户要**生成图片**：插画、图标、示意图、配图、头像概念图等  
- 任务产物应是 **image 文件**（配合 `[SEND:]`）  
- 用户提供参考图并要求**改风格 / 变体**（`reference_images` 非空）

**不用本 skill：** 仅文字说明怎么画、仅要 SVG/代码画图且不需 raster 文件、用户要的是**理解已有图**（用 `skills/image-understanding/SKILL.md` + `describe_image`）。

---

## 第二套多模态 API（BYOK；与 Gateway 主对话 AI 分离）

Tool helper 从下列变量读凭证；**agent 不要传 key**，也不要在对话里问用户要 key。  
用户应在 workspace 根目录 ``.env.multimodal`` 中填写 ``MINIMAX_API_KEY`` 等。

| 变量 | 必填 | 说明 |
|------|------|------|
| `MINIMAX_API_KEY` | 是 | 按量付费「接口密钥」（与文生图共用） |
| `MINIMAX_API_HOST` | 否 | 默认 `https://api.minimaxi.com` |
| `IMAGE_GEN_MODEL` | 否 | 默认 `image-01` → `POST /v1/image_generation` |
| `IMAGE_GEN_BACKEND` | （已废弃，忽略） | 历史字段；现仅 API 或报错 |

未配置、仍为占位符（如 `YOUR_*_HERE`）、或 HTTP 失败 → **`ok: false`**，`message` 说明原因；**不要** `[SEND:]`，引导用户检查多模态 API 配置。
---

## 配方步骤

### Step 0 — 整理 `description`（调用前，必做）

把用户意图写成**可绘制的画面描述**，不是任务请求句。

| 保留 | 去掉 |
|------|------|
| 主体、数量、姿态 | 「请帮我画」「能不能」「生成一张图」 |
| 场景、背景、光线 | 与画面无关的闲聊 |
| 风格（写实 / 扁平 / 水彩等） | |
| 禁忌（不要文字、不要水印） | |

**示例**

| 用户说 | `description`（示意） |
|--------|---------------------|
| 请帮我画一只猫 | `a fluffy orange tabby cat, sitting, soft daylight, simple background` |
| 赛博朋克城市夜景壁纸 | `cyberpunk city at night, neon lights, rain reflections, wide wallpaper composition, cinematic` |
| 把这张图改成水彩风（已给 `[RECV:path]`） | `watercolor painting style, soft edges, pastel tones` + `reference_images` |

描述用 **英文或中文** 均可；宜具体、可画，避免空泛（「好看的图」）。

### Step 1 — 参考图（可选）

用户通过 `[RECV:<absolute-path>]` 给了图，或 workspace 里已有路径：

- 确认路径存在（`list_dir` / 用户给出的绝对路径）  
- 填入 `reference_images`：**JSON 字符串**，路径数组，例如 `'["D:/path/to/ref.png"]'`  
- `description` 写**要改成什么样**（图生图 / 改风格），不是重复描述参考图内容  

空字符串 `""` 或 `'[]'` = 纯文生图。

### Step 2 — 调用 `generate_image`

| 入参 | 必填 | 说明 |
|------|------|------|
| `description` | 是 | Step 0 整理后的画面描述 |
| `reference_images` | 否 | JSON 路径数组字符串；默认 `""` |
| `output_path` | 否 | 输出文件路径；空则 `generated/images/gen-*.png` |
| `width` | 否 | 像素宽；`0` = 默认 512 |
| `height` | 否 | 像素高；`0` = 默认 512 |
| `seed` | 否 | `-1` = 随机；固定整数可复现同描述下的出图 |

**调用示例（文生图）**

```text
generate_image(
  description="a fluffy orange tabby cat, sitting, soft daylight, simple background"
)
```

**调用示例（参考图改风格）**

```text
generate_image(
  description="watercolor style, soft pastel tones",
  reference_images='["D:/workspace/uploads/ref.png"]'
)
```

### Step 3 — 解析返回 JSON

Tool 返回 **一个 JSON 字符串**（不是 markdown）。解析后看：

| 字段 | 含义 |
|------|------|
| `ok` | `true` 才可交付图片 |
| `path` | 图片**绝对路径**（用于 `[SEND:]`） |
| `mode` | `text_to_image` 或 `image_to_image` |
| `seed` | 本次使用的种子（重试时可改） |
| `width` / `height` | 出图尺寸 |
| `backend` | 成功时为 `api` |
| `message` | 错误或补充说明 |

`ok: false` → 向用户说明 `message`（常见：多模态 API 未配置或上游报错），**不要** `[SEND:]`，不要假装成功。

### Step 4 — 交付

1. **1–3 句**说明画了什么、关键参数（若用户关心尺寸/风格）。  
2. **单独一行**发出文件（路径必须来自 JSON 的 `path`）：

```text
[SEND:D:/absolute/path/to/image.png]
```

3. **不要**在对话里再贴整张图的 base64 或超长像素描述。

### Step 5 — 不满意时重试

1. **先改 `description`**（更具体的主体、风格、构图、禁忌）。  
2. 再改 **`seed`**（同描述下换一张）。  
3. 有参考图时检查 `reference_images` 路径是否正确。  
4. **不要**不改参数连续多次调用。

---

## 责任边界（排错）

| 现象 | 层 |
|------|-----|
| `description` 仍是「请帮我画…」 | Skill / agent — 未做 Step 0 |
| `ok: false`, 多模态 API 未配置 | Tool / 环境 — 引导用户填写 `.env.multimodal` |
| `ok: false`, 空描述或 JSON 非法 | Tool 校验 — 检查入参 |
| 有真后端但画得不像 | 改 `description` 或 `seed` |
| 文件有了用户没收到 | agent 忘了 Step 4 `[SEND:]` |

---

## 与 tool 文档一致的限制

以下内容 **不要** 传给 `generate_image`（在 tool/helper 内部处理）：

- token、embedding、steps、cfg、sampler  
- API key、base_url（由 env / 日后 Gateway 注入）  
- 用户未经整理的整句请求  

Tool 契约见 tool docstring；**skill 入参出参不变**。
