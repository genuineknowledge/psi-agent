---
name: comfyui
description: "用 ComfyUI 生成图片/视频/音频 —— 安装/启动/管理 ComfyUI,装自定义节点与模型,再用 HTTP + WebSocket API 提交 workflow 并做参数注入(改 prompt/seed/尺寸等)、监听进度、取回产物。生命周期用官方 comfy-cli(comfy install/launch/node/model),执行走 ComfyUI 自带的 REST(POST /prompt、GET /history、GET /view)+ WebSocket(/ws) API。LOAD whenever 用户要用本地或自建 ComfyUI 出图/生成视频/生成音频、跑一个 workflow(API 格式 JSON)、排队一个 prompt、管理节点/模型、查队列/历史、或下载生成结果。NOT for 一键云端文生图(用 image-generation skill + generate_image tool),NOT for 只读理解已有图(image-understanding)。"
category: creative
---

# ComfyUI(生成图 / 视频 / 音频)

用本 skill 驱动 **ComfyUI** —— 开源的节点式生成引擎,能出**图片、视频、音频**。分两层:

- **生命周期**用官方 **`comfy-cli`**(外部 CLI,`pip install comfy-cli`):装 ComfyUI、启动/停止服务、装自定义节点、下模型、看环境。全部通过 `bash` 工具跑。
- **执行**用 ComfyUI 自带的 **REST + WebSocket API**:把一个 **API 格式的 workflow(节点图 JSON)** 做参数注入后 `POST /prompt` 排队,连 `ws://.../ws` 监听进度到完成,再 `GET /history/{id}` 拿产物文件名、`GET /view` 下载落盘。这层用**内联 Python**(`bash` 里跑 `python -c`/临时脚本,用仓库已装的 `aiohttp`)或 `curl`,**本 skill 不封装 Python tool、不新增依赖**。

默认用中文回答,除非用户明显用别的语言。

> 这是操作指南,不是唯一事实来源。ComfyUI 与 comfy-cli 迭代快,节点/flag/端点都可能变。下否定结论前,以真机 `comfy --help`、`/object_info` 和用户实际的 workflow JSON 为准,别照记忆硬套。

## 何时使用

- 用户要跑一个 **ComfyUI workflow / prompt graph** 出图、生成视频、生成音频,尤其是需要**参数注入**(换正/负向提示词、改 seed、宽高、步数、换 checkpoint/LoRA、批量出图)。
- 要**管理 ComfyUI 本身**:安装、启动/停止服务、装/更新自定义节点、下载模型、查环境与路径。
- 需要**排队与取回**:查队列(`/queue`)、查历史(`/history`)、下载产物(`/view`)、中断执行。

**不用本 skill:**
- 只是**一键云端文生图** → 用 `generate_image` tool + `skills/image-generation/SKILL.md`,不用自己起服务。
- 只是**理解/描述已有图** → `skills/image-understanding/SKILL.md`(`describe_image`)。
- 只是**本地像素/图形处理**(渲染、分割、抽帧、gcode) → `skills/media-graphics`。
- 语音合成/识别有专用 tool → `text-to-speech` / `speech-to-text`(除非你就是要用 ComfyUI 的音频节点跑 workflow)。

## 安全模型(最高优先级)

- **凭据不落痕**:CivitAI / HuggingFace 的 token(`CIVITAI_API_TOKEN` / `HF_API_TOKEN`)、ComfyUI 服务地址,**不打印、不写进 workflow、不提交**。下模型优先用环境变量传 token(comfy-cli 明确说环境变量传的 token 不会持久化到配置),别用 `--set-*-token` 把 token 写进用户配置,除非用户要求。
- **只连用户指定的 ComfyUI 主机**(默认 `127.0.0.1:8188`)。不要把 workflow、提示词或产物外发到第三方服务。
- **产物落到 workspace** 再用 `[SEND:绝对路径]` 交付(见下)。不要把大文件塞进对话。
- **谨慎的破坏性操作**:`comfy model remove`、删 workspace、大批量下模型(占几十 GB 磁盘)、装来路不明的自定义节点(自定义节点是任意 Python 代码,等于执行第三方代码)——动手前告诉用户影响,别无人值守地删/装。
- **长任务先告知**:一个大 workflow 可能长时间占满 GPU。排队大批量或视频生成前先跟用户说一声。
- **服务暴露**:`comfy launch -- --listen 0.0.0.0` 会把 ComfyUI 暴露到网络且**默认无鉴权**。除非用户明确要,默认只监听 `127.0.0.1`;要对外时提醒用户加反向代理/鉴权。

## Setup / 生命周期(comfy-cli)

`comfy-cli` 是外部 CLI(PyPI 包 `comfy-cli`,需 Python ≥3.10),不是本仓库依赖。缺失时 `bash` 报 `comfy: command not found` —— 照实转达用户,别假装成功。装 ComfyUI + 下模型是**用户级、重资源**操作,agent 只引导。

```bash
comfy --version 2>&1 || echo "comfy-cli 未安装"     # 先探
pip install comfy-cli                               # 安装 CLI
comfy --help 2>&1 | head -40                        # 查当前版本的确切命令/flag(迭代快,以此为准)

# 安装 ComfyUI(默认装到 ~/comfy;-y 非交互)。也可 --workspace=<path> 指定位置
comfy install                                       # 含 ComfyUI-Manager;--skip-manager 可跳过
comfy --workspace=/path/to/comfy install            # 装到指定 workspace

# 工作区定位:--workspace=<path> / --recent / --here 三选一;或先设默认
comfy set-default /path/to/comfy
comfy which                                         # 看当前目标 workspace 路径
comfy env                                           # 看环境、后台实例、运行状态
```

### 启动 / 停止服务

```bash
comfy launch                                        # 前台启动(默认 127.0.0.1:8188)
comfy launch --background                           # 后台启动(comfy env 里可管理、comfy stop 停)
comfy launch --background -- --port 8188 --listen 127.0.0.1   # `--` 之后是透传给 ComfyUI 的原生参数
comfy stop                                          # 停后台实例

# 起完等它就绪(REST 通了才能提交)
curl -s http://127.0.0.1:8188/system_stats >/dev/null && echo "ComfyUI ready" || echo "还没起来"
```

`--` 之后的都是 ComfyUI 原生 flag(如 `--cpu`、`--lowvram`、`--port`、`--listen`)。**`--listen 0.0.0.0` 才对外暴露且无鉴权**,见安全模型。

### 自定义节点

```bash
comfy node show all --channel recent                # 看可装节点
comfy node simple-show installed                    # 看已装
comfy node install comfyui-impact-pack              # 装(自定义节点=任意 Python,先确认可信)
comfy node update all                               # 更新全部
comfy node save-snapshot                            # 存快照;restore-snapshot <name> 恢复
```

装完节点通常**要重启 ComfyUI**(`comfy stop` 再 `comfy launch --background`)才会加载。

### 模型

```bash
# URL 支持 CivitAI 页面 / HuggingFace 文件 URL / 直链;--relative-path 指定放到哪个子目录
comfy model download --url <URL> --relative-path models/checkpoints
CIVITAI_API_TOKEN=xxx comfy model download --url <civitai-url> --relative-path models/loras
HF_API_TOKEN=xxx      comfy model download --url <hf-file-url> --relative-path models/checkpoints
comfy model list                                    # 看已装模型
```

token 优先用**环境变量**临时传(不持久化);`--set-civitai-api-token` / `--set-hf-api-token` 会写进用户配置,除非用户要求否则别用。

## 核心工作流:用 API 跑 workflow(参数注入)

ComfyUI 服务本身跑在 aiohttp 上,一套 REST + 一个 WebSocket。**关键前提:workflow 必须是「API 格式」JSON**(节点 id → `{class_type, inputs}` 的字典),不是从画布保存的普通 workflow。在 ComfyUI 网页里 **Settings 开启 "Enable Dev mode Options" → 用 "Save (API Format)"** 导出;用户给的若是普通格式,先请他导出 API 格式。

**参数注入 = 改这个 JSON 里对应节点的 `inputs` 字段**,再提交:

- 正/负向提示词:改 `CLIPTextEncode` 节点的 `inputs.text`。
- 随机种子:改 `KSampler` 节点的 `inputs.seed`。
- 尺寸/批量:改 `EmptyLatentImage` 的 `inputs.width/height/batch_size`。
- 换模型:改 `CheckpointLoaderSimple` 的 `inputs.ckpt_name`(名字要和已装模型一致,见排错)。

### 端点速查

| 方法 | 端点 | 作用 |
|------|------|------|
| POST | `/prompt` | 提交 workflow,body `{"prompt": <api_json>, "client_id": <id>}`,返回 `{"prompt_id": ...}` |
| WS | `/ws?clientId=<id>` | 实时进度:`executing`(`data.node == null` 且 `prompt_id` 匹配即**该 prompt 执行完**)、`progress`(`value/max`)、`executed`(带 output) |
| GET | `/history/{prompt_id}` | 拿执行结果:`[id]["outputs"][node_id]` 下的 `images` / `gifs` / `audio` 等,每项含 `filename` `subfolder` `type` |
| GET | `/view?filename=&subfolder=&type=output` | 下载单个产物文件(图/视频/音频通用) |
| GET | `/queue` `/history` | 查队列 / 全部历史 |
| POST | `/interrupt` | 中断当前执行 |
| GET | `/object_info` | 列出所有节点及其输入定义(排查节点/入参用) |

进度**必须走 WebSocket**:`/prompt` 是异步排队,立刻返回 `prompt_id`,不代表跑完。判定完成的权威信号是 WS 收到 `type=="executing"` 且 `data["node"] is None and data["prompt_id"]==<你的id>`。

### 推荐:内联 Python(aiohttp,已装)

仓库已装 **`aiohttp`**(异步,HTTP + `ws_connect` 都支持,见 `tools/_xfyun_tts.py` 的 `ws_connect` 写法),无需新增任何库。把下面存成临时脚本跑,它做**注入 → 提交 → WS 等完成 → 下载落盘**一条龙:

```bash
cat > /tmp/comfy_run.py <<'PY'
import asyncio, json, sys, uuid, os
import aiohttp

SERVER = os.environ.get("COMFY_SERVER", "127.0.0.1:8188")

async def main(wf_path, out_dir):
    with open(wf_path, encoding="utf-8") as f:
        prompt = json.load(f)                       # API 格式 workflow
    # === 参数注入示例:按需改对应节点的 inputs ===
    # prompt["6"]["inputs"]["text"] = "a red fox in snow, masterpiece"
    # prompt["3"]["inputs"]["seed"] = 12345
    client_id = str(uuid.uuid4())
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://{SERVER}/prompt",
                          json={"prompt": prompt, "client_id": client_id}) as r:
            r.raise_for_status()
            prompt_id = (await r.json())["prompt_id"]
        # WS 等这个 prompt 执行完
        async with s.ws_connect(f"ws://{SERVER}/ws?clientId={client_id}") as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue                         # 二进制是 latent 预览,跳过
                m = json.loads(msg.data)
                if m.get("type") == "progress":
                    d = m["data"]; print(f"progress {d['value']}/{d['max']}", file=sys.stderr)
                if m.get("type") == "executing":
                    d = m["data"]
                    if d.get("node") is None and d.get("prompt_id") == prompt_id:
                        break                        # 执行完成
        # 取产物文件名
        async with s.get(f"http://{SERVER}/history/{prompt_id}") as r:
            hist = (await r.json())[prompt_id]
        os.makedirs(out_dir, exist_ok=True)
        saved = []
        for node in hist["outputs"].values():
            for key in ("images", "gifs", "audio", "videos"):   # 图/视频/音频产物键
                for item in node.get(key, []):
                    params = {"filename": item["filename"],
                              "subfolder": item.get("subfolder", ""),
                              "type": item.get("type", "output")}
                    async with s.get(f"http://{SERVER}/view", params=params) as r:
                        data = await r.read()
                    dest = os.path.join(out_dir, item["filename"])
                    with open(dest, "wb") as f:
                        f.write(data)
                    saved.append(os.path.abspath(dest))
    for p in saved:
        print(p)                                     # 打印落盘的绝对路径

asyncio.run(main(sys.argv[1], sys.argv[2]))
PY
python /tmp/comfy_run.py /path/to/workflow_api.json ./generated/comfyui
```

跑完拿到落盘路径后,最后一行用 `[SEND:<绝对路径>]` 交付给用户(和 `image-generation` 一致的产物交付约定)。

### 轻量:纯 curl(不等进度)

只想快速排一个 prompt、稍后再取(不实时监听进度)时:

```bash
# 提交
curl -s -X POST http://127.0.0.1:8188/prompt \
  -H 'Content-Type: application/json' \
  -d "{\"prompt\": $(cat workflow_api.json)}" 
# 轮询历史(返回非空且含 outputs 即完成),再 /view 下载
curl -s "http://127.0.0.1:8188/history/<prompt_id>"
curl -s "http://127.0.0.1:8188/view?filename=ComfyUI_00001_.png&subfolder=&type=output" -o out.png
```

进度/完成判定不如 WebSocket 精确,批量或长任务优先用上面的 Python 版。

## 排错

| 症状 | 原因 / 处理 |
|------|-------------|
| `comfy: command not found` | comfy-cli 未装 → `pip install comfy-cli`(Python ≥3.10)。 |
| `curl` 连不上 `/system_stats` | 服务没起或端口/地址不对 → `comfy launch --background` 后等就绪;确认 `COMFY_SERVER`。 |
| `POST /prompt` 返回 400 / `invalid prompt` | 多半是给了**画布格式**而非 **API 格式** workflow → 在 ComfyUI 网页开 Dev mode 用 "Save (API Format)" 重新导出。 |
| 报某个 `class_type` 不存在 | 该 workflow 依赖**未安装的自定义节点** → `comfy node install <pack>` 后重启;`GET /object_info` 看现有节点。 |
| `ckpt_name` / LoRA / VAE 找不到 | 模型没下或名字不符 → `comfy model list` 看实际文件名,注入时用**真实文件名**;缺就 `comfy model download`。 |
| WS 一直不发 `executing node=null` | 可能排在队列后面或卡在某节点 → 查 `GET /queue`;`progress` 停滞看 ComfyUI 控制台报错。 |
| 显存不足(CUDA OOM) | 降尺寸/批量,或 `comfy launch -- --lowvram` / `--cpu`(慢)。 |
| 收到二进制 WS 帧 | 那是 latent 预览图,不是错误 → 只处理 `WSMsgType.TEXT`,二进制跳过(脚本已这么做)。 |
| 装完节点不生效 | 自定义节点要**重启服务**才加载 → `comfy stop && comfy launch --background`。 |

## 与相邻 skill 的关系

- **`image-generation`**(`generate_image` tool):一键调云端 API 出图,零运维、可控性低。要**自备/自控服务、跑任意 workflow、出图/视频/音频、精细参数注入**才用本 skill。
- **`media-graphics`**:本地渲染/分割/抽帧/几何,不涉及 ComfyUI 服务。
- **`text-to-speech` / `speech-to-text`**:专用语音 tool;只有当你要用 ComfyUI 的音频节点跑 workflow 时才归本 skill。

本 skill 归 **creative**:用生成式引擎产出图/视频/音频这类创意产物。

