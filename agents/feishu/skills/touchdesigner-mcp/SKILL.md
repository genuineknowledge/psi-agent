---
name: touchdesigner-mcp
description: "通过 twozero MCP 控制正在运行的 TouchDesigner 实例 —— 建算子(create_operator)、设参数(set_operator_pars)、连线(outputConnectors/inputConnectors)、跑 Python(execute_python)、看网络/报错/性能、截图预览,搭建实时视觉。36 个原生工具,走 Streamable HTTP 连本机 TD(默认 127.0.0.1:40404)。LOAD whenever 用户要用 AI 操作/搭建 TouchDesigner 工程:建/改/连算子、注入参数、跑 TD Python、查网络结构或算子报错、截图看画面、录制输出、做音频反应/生成式/粒子/3D/反馈类实时视觉。NOT for 只读理解已有图(image-understanding)、一键云端文生图(image-generation)、本地像素/几何处理(media-graphics)、或用 ComfyUI 节点图出图(comfyui)。"
category: creative
---

# TouchDesigner(twozero MCP)

用本 skill 驱动 **twozero MCP** —— 一个装进 **正在运行的 TouchDesigner 实例** 里的免费插件,给 agent 暴露 **36 个原生工具**,可以建算子、设参数、连线、跑 TD Python、读写 DAT/CHOP、截图预览、查网络/报错/性能,从而搭建实时视觉(音频反应、生成式、粒子、3D、反馈)。

架构一句话:

```
haitun agent  ──Streamable HTTP──▶  twozero.tox(TD 内, 127.0.0.1:40404)  ──▶  TD Python 环境
```

- **连接层**是 TouchDesigner 里的一个 `.tox` 组件(`twozero.tox`),在 TD 进程内起了一个 MCP server,监听本机 **40404** 端口(Streamable HTTP)。它 **不是** pip 包、**不是** 独立可执行、**不能** 由 agent 启动——必须用户先开着 TD 且装好插件。
- **执行层**就是那 36 个 `td_*` 工具。本 skill 通过仓库已有的 `_mcp.py` MCP 桥接把它们暴露成 agent 工具(见下「接入 psi-agent」);或用户在别的 MCP 客户端里直接用。

默认用中文回答,除非用户明显用别的语言。

> 这是操作指南,不是唯一事实来源。TouchDesigner 与 twozero 迭代快,算子类型、参数名、工具签名都可能变。下否定结论前,以真机 `td_get_par_info`、`td_get_operators_info`、`td_get_docs` 的实际返回为准,别照记忆(尤其旧版 TD 的参数名)硬套。

## 何时使用

- 用户要 **AI 操作 / 搭建一个 TouchDesigner 工程**:建算子(TOP/CHOP/SOP/DAT/COMP/MAT)、改参数、连线成网络、跑 TD Python 脚本。
- 要 **查看 / 调试** 一个 TD 网络:看结构(`td_get_network`)、查算子报错(`td_get_errors`)、看性能(`td_get_perf`)、截图看当前画面(`td_get_screenshot`)。
- 要做 **实时视觉**:音频反应、生成式、粒子、3D、反馈回路;或 **录制输出**(`moviefileoutTOP`)。

**不用本 skill:**

- 只是 **理解 / 描述一张已有图** → `skills/image-understanding`(`describe_image`)。
- 只是 **一键云端文生图** → `skills/image-generation`(`generate_image` tool)。
- 只是 **本地像素 / 几何 / 抽帧处理** → `skills/media-graphics`。
- 要用 **ComfyUI 节点图** 出图/视频/音频 → `skills/comfyui`(那是另一套引擎)。

## 安全模型(最高优先级)

- **无鉴权、纯本机**:twozero MCP 只在 `127.0.0.1:40404` 监听,**没有任何鉴权**——本机任何进程都能发命令。确认连的就是用户本机那个 TD,别把端口暴露到网络,别加反代把它对外开放。
- **`td_execute_python` = 任意代码执行**:它以 TD 进程用户身份,对 TD 的完整 Python 环境和文件系统有 **无限制** 访问权。跑 Python 前想清楚副作用(删文件、写盘、改工程、外发数据都做得到)。**优先用原生 `td_*` 工具**,只有多步复杂逻辑才回退到 `td_execute_python`。
- **破坏性操作先告知**:`td_project_quit`(关掉用户工程,未存会丢改动)、大批量建算子/删算子、覆盖用户已有节点、写用户磁盘上的文件——动手前说清影响,别无人值守地删/关/覆盖。
- **长任务 / 占 GPU**:录制视频、跑重网络会长时间占满 GPU。开始前跟用户说一声。
- **产物落盘再交付**:截图 / 录制文件落到 workspace,再用 `[SEND:绝对路径]` 交付,别把大文件塞进对话。

## Setup / 连接(一次性)

twozero 是 **TD 内的插件**,agent 起不了 TD,只能引导用户完成一次性配置,然后探测端口。

**用户端一次性步骤(引导用户做,agent 不能代劳):**

1. 开着要操作的 **TouchDesigner** 工程(建议 TD 2025.32+;老版本参数名不一样)。
2. 拿到 `twozero.tox`,拖进 TD 网络编辑器,点 **Install**。
3. 点 twozero 图标 → **Settings → mcp → "auto start MCP" → Yes**,让它开机自启 MCP。
4. 确认 MCP 起在本机 **40404**。

**agent 端探测(用 `bash`):**

```bash
# 端口通不通(最快的存活探测)
nc -z 127.0.0.1 40404 && echo "twozero MCP: READY" || echo "twozero MCP: 未就绪(先让用户在 TD 里装好 twozero.tox 并开启 MCP)"

# 健康检查:返回 JSON,含实例 PID、工程名、TD 版本
curl -s http://127.0.0.1:40404/mcp | head -c 400
```

探测失败时 **照实告诉用户**「TD 没开 / twozero 没装 / MCP 没启用」,别假装连上了。TD 不在跑时,本 skill 的所有工具都用不了——这也是它做成 **skill 而非常驻 tool** 的原因(常驻 tool 在导入期就要连 server,TD 没开会拖垮整个工具注册,见 [[psi-agent-browser-tools]] 里 `_mcp` 导入期发现工具的机制)。

## 接入 psi-agent(把 36 个工具暴露给 agent)

仓库自带 `tools/_mcp.py` 桥接:写一个 `@mcp` 装饰的声明函数,返回 MCP server 配置,**导入期** 就会连上 server、`list_tools()`、把每个工具生成成 agent 可调用的函数(工具名默认加 `<函数名>_` 前缀)。twozero 走 Streamable HTTP,配置示例:

```python
# tools/touchdesigner.py  —— 仅当用户要把 td_* 工具常驻暴露时才加;默认按需用本 skill 即可
from tools._mcp import mcp

@mcp
def td():
    """通过 twozero MCP 控制本机 TouchDesigner。需 TD 正在运行且 twozero MCP 已在 127.0.0.1:40404 启用。"""
    return {"transport": "http", "url": "http://127.0.0.1:40404/mcp", "prefix": ""}
```

**关键坑(照 [[psi-agent-browser-tools]] 的教训):**

- twozero 工具名已自带 `td_` 前缀(`td_create_operator` 等)。`_mcp.py` 默认还会加 `<函数名>_`(这里是 `td_`),会变成 `td_td_create_operator` 双前缀。**必须传 `prefix=""`**(需确认当前 `_mcp.py` 支持 `prefix` 键;若该分支未合入 prefix 支持,则把声明函数命名为空/改桥接,或直接按需用本 skill 走 `bash`+MCP 而不常驻)。
- **导入期要连 server**:`@mcp` 在模块导入时就 `_discover()` 连 40404。TD 没开会抛错、拖累工具注册。twozero 本质是「用户开着 TD 才有」的可选后端,**默认不建议常驻注册**;推荐按需用本 skill(需要时探测端口、用工具)。要常驻则接受「TD 没开时该模块加载失败被 registry 跳过」。

> 简单说:**默认走 skill**(引导 + 探测 + 按工具名调用);只有用户明确要把 `td_*` 变成一直在的 agent 工具,才加 `tools/touchdesigner.py`,并处理好 `prefix=""` 与导入期连接两件事。

## 36 个原生工具(按类)

以下为 twozero 暴露的工具(名字含 `td_` 前缀)。**别背参数名**——用前先 `td_get_par_info` / `td_get_docs` 查当前版本的确切签名。

| 类别 | 工具 |
|------|------|
| **核心 Core** | `td_execute_python`、`td_create_operator`、`td_set_operator_pars`、`td_get_operator_info`、`td_get_operators_info`、`td_get_network`、`td_get_errors`、`td_get_par_info`、`td_get_hints`、`td_get_focus` |
| **读写 R/W** | `td_read_dat`、`td_write_dat`、`td_read_chop`、`td_read_textport` |
| **视觉 Visual** | `td_get_screenshot`、`td_get_screenshots`、`td_get_screen_screenshot`、`td_navigate_to` |
| **搜索 Search** | `td_find_op`、`td_search` |
| **系统 System** | `td_get_perf`、`td_list_instances`、`td_get_docs`、`td_agents_md`、`td_reinit_extension`、`td_clear_textport` |
| **输入自动化 Input** | `td_input_execute`、`td_input_status`、`td_input_clear`、`td_op_screen_rect`、`td_click_screen_point`、`td_screen_point_to_global` |
| **管理 / 开发 Admin** | `td_project_quit`、`td_test_session`、`td_dev_log`、`td_clear_dev_log` |

## 核心工作流

### 1. 建算子(优先用原生工具)

```text
td_create_operator(type="noiseTOP",  parent="/project1", name="bg",  parameters={"resolutionw": 1280, "resolutionh": 720})
td_create_operator(type="levelTOP",  parent="/project1", name="brightness")
td_create_operator(type="nullTOP",   parent="/project1", name="out")
```

- **算子类型** 用 TD 的类名(`noiseTOP`、`levelTOP`、`nullTOP`、`moviefileoutTOP`、`constantCHOP`…)。不确定就 `td_search` / `td_get_docs` 查。
- **NEVER 猜参数名**:先对该算子类型 `td_get_par_info(type=...)` 拿到真实参数,再 `parameters={...}` 传。旧训练数据对 TD 2025.32 常常是错的。

### 2. 设参数

```text
td_set_operator_pars(path="/project1/bg", parameters={"roughness": 0.6, "monochrome": true})
```

### 3. 连线(没有原生工具,走 `td_execute_python`)

twozero **没有** 专门的连线工具。连线用 TD Python 的 `outputConnectors` / `inputConnectors`:

```text
# td_execute_python:
op('/project1/bg').outputConnectors[0].connect(op('/project1/fx').inputConnectors[0])
```

### 4. 批量建 + 连线一条龙(td_execute_python)

多步逻辑(建一串算子再顺序连起来)回退到一段 Python:

```python
# td_execute_python script:
root = op('/project1')
nodes = []
for name, optype in [('bg', noiseTOP), ('fx', levelTOP), ('out', nullTOP)]:
    n = root.create(optype, name)
    nodes.append(n.path)
# 顺序连成链
for i in range(len(nodes) - 1):
    op(nodes[i]).outputConnectors[0].connect(op(nodes[i + 1]).inputConnectors[0])
result = {'created': nodes}
```

### 5. 验证 / 取画面

```text
td_get_errors(path="/project1", recursive=true)     # 先查报错,别默认成功
td_get_perf()                                       # 帧率 / cook 耗时
td_get_operator_info(path="/project1/out", detail="full")
td_get_screenshot(path="/project1/out")             # 截某算子的输出,落盘后 [SEND:] 交付
```

**约定**:建完 / 改完网络,先 `td_get_errors` 确认无红框报错,再截图给用户看。别只凭「工具没报错」就宣布成功——TD 里算子可能带自己的 error/warning。

### 6. 录制视频(td_execute_python)

```python
# td_execute_python script:
root = op('/project1')
rec = root.create(moviefileoutTOP, 'recorder')
op('/project1/out').outputConnectors[0].connect(rec.inputConnectors[0])
rec.par.type = 'movie'
rec.par.file = '/tmp/output.mov'
rec.par.videocodec = 'prores'     # macOS;或 'mjpa' 兜底。H.264/H.265/AV1 需 Commercial license
rec.par.record = True
```

## 版本 / license 注意

- **NEVER 猜参数名**:先 `td_get_par_info`。这是 twozero 反复强调的头号规则(旧版 TD 参数名和 2025.32 不一致)。
- **优先原生工具,复杂才用 Python**:能用 `td_create_operator` / `td_set_operator_pars` 完成的别写 `td_execute_python`。
- **Non-Commercial TD** 分辨率上限 **1280×1280**;要更大用 `outputresolution = 'custom'`(且需相应 license)。
- **编码器**:`prores`(macOS)/ `mjpa` 兜底可用;H.264 / H.265 / AV1 需 **Commercial** license。

## 排错

| 症状 | 原因 / 处理 |
|------|-------------|
| `nc` / `curl` 连不上 40404 | TD 没开 / twozero.tox 没装 / MCP 没启用 → 引导用户走上面 Setup 四步;确认工程正在运行。 |
| 报某个算子 `type` 不存在 | 类名拼错或 TD 版本没这个算子 → `td_search` / `td_get_docs` 查真实类名。 |
| `set_operator_pars` 报参数名无效 | 猜了参数名 → `td_get_par_info(type=...)` 拿真实参数名再传。 |
| 建了算子但画面没变化 | 多半 **没连线** 或没连到输出链 → 用 `outputConnectors/inputConnectors` 连,`td_get_network` 看结构。 |
| 算子带红框但工具没报错 | 工具成功 ≠ 网络无错 → 显式 `td_get_errors(recursive=true)`。 |
| 分辨率被截到 1280 | Non-Commercial 上限 → `outputresolution='custom'`(需 license)。 |
| 录制文件是空 / 编码失败 | 编码器需 license(H.264/H.265/AV1)→ 换 `prores`/`mjpa`;确认 `rec.par.record=True` 且有输入连进来。 |
| 接入 psi-agent 后工具名变 `td_td_*` | `_mcp` 双前缀 → 声明里传 `prefix=""`(见「接入 psi-agent」)。 |

## 与相邻 skill 的关系

- **`comfyui`**:另一套生成引擎(节点式出图/视频/音频,HTTP+WS API)。要 **实时视觉 / 操作 TD 工程** 才用本 skill;要跑 ComfyUI workflow 用 `comfyui`。
- **`image-generation`**(`generate_image`):一键云端文生图,零运维。
- **`image-understanding`** / **`media-graphics`**:只读理解图 / 本地像素几何处理,不涉及 TD。

本 skill 归 **creative**:用 TouchDesigner 搭建实时视觉这类创意产物。

