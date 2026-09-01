---
name: excalidraw
description: "生成手绘风格的 Excalidraw 图 —— 架构图(arch)、流程图(flow)、时序图(seq)。产物是标准 `.excalidraw` 场景文件(纯 JSON,type=excalidraw、version=2),用户在 excalidraw.com 或 VS Code Excalidraw 插件里直接打开即可继续手拖编辑。用 Python 标准库 `json` 拼装元素(rectangle/diamond/ellipse/text/arrow/line),手绘感来自 roughness=1 + Excalifont 字体。文本绑定进容器(containerId/boundElements)、箭头绑定两端节点(startBinding/endBinding + focus/gap)。用 [SEND:绝对路径] 交付 .excalidraw 文件。LOAD when 用户要一张可继续编辑的手绘风示意图:系统架构图、服务/组件关系图、业务或代码流程图、时序/交互图、思维导图式框图,或想把一段描述变成 Excalidraw 画布。NOT for 要成品位图/海报(用 image-generation)、要暗色矢量架构图 HTML(用 architecture-diagram)、要实时协作画布让 agent 边画边看(用 canvas 工具接 Excalidraw MCP)、要 Mermaid/PlantUML 文本图、或要跑本地 ComfyUI 出图(comfyui)。"
category: creative
---

# Excalidraw（手绘风格示意图 · arch / flow / seq）

生成 **Excalidraw 场景文件**(`.excalidraw`,纯 JSON):架构图、流程图、时序图,一律**手绘风格**。产物落到 workspace,用 `[SEND:绝对路径]` 交付。用户在 **excalidraw.com**、桌面版,或 **VS Code Excalidraw 插件**里打开,能直接继续手拖、改色、加节点 —— 这是本 skill 相对"出一张死图"的核心价值:**产出的是可编辑的画布,不是位图**。

本 skill **纯用 Python 标准库 `json`** 拼 JSON,**不新增任何依赖、不封装常驻 tool、不写前端**。所有文件读写走 workspace 的 `write`/`read` 或 `bash`。默认用中文回答,除非用户明显用别的语言。

> 这是操作指南,不是唯一事实来源。Excalidraw schema 会随版本演进(当前稳定发布 `v0.18.x`,场景 `version: 2`)。下否定结论前,以官方 [json-schema 文档](https://docs.excalidraw.com/docs/codebase/json-schema) 和实际在 excalidraw.com 打开跑通为准,别照记忆硬套字段。

## 何时使用

用户想要**一张可继续编辑的手绘风框图**,典型信号:

- **架构图(arch)**:服务/模块/组件方框 + 连线,标数据流向、依赖关系、分层。
- **流程图(flow)**:开始/结束(圆角矩形或椭圆)、处理步骤(矩形)、判断(菱形)+ 带箭头连线、Yes/No 分支。
- **时序图(seq)**:参与者(顶部方框)+ 生命线(竖虚线)+ 横向消息箭头(实线调用 / 虚线返回)。
- **框图 / 思维导图**:中心节点向外发散的方框与连线。
- "把这段描述画成 Excalidraw / 画个可以我自己接着改的示意图"。

**不用本 skill:**
- 要**成品位图/插画/海报**(png/jpg) → `image-generation`(`generate_image` tool)。
- 要**暗色矢量风的架构/云/基建图**、作为单文件 HTML 展示 → `architecture-diagram`。
- 要 agent **边画边看的实时协作画布**(在真 Excalidraw 里建算子/连线) → `canvas` 工具集(接 Excalidraw MCP)。
- 要 **Mermaid / PlantUML** 文本定义图 → 直接写 Mermaid(architecture-diagram 也含 architecture-beta)。
- 要**跑本地 ComfyUI** 生成图/视频 → `comfyui`。

> 提示:Excalidraw 官方支持 **Mermaid → Excalidraw**("Mermaid to Excalidraw" 转换)。如果图很规整、用户不介意先经 Mermaid,可先写 Mermaid 让用户在 excalidraw.com 里粘贴转换;但本 skill 的主线是**直接产出 `.excalidraw` JSON**,可控性和手绘节点样式最好。

## 心智模型（先懂这个再写）

一个 `.excalidraw` 文件 = **顶层信封 + 一个扁平的 `elements` 数组**。没有嵌套树:分组、容器文本、箭头绑定,全靠**元素之间用 `id` 互相引用**表达。画图 = 造一串元素对象、算好坐标、连好引用。

顶层信封(固定):

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [ /* 所有元素,扁平数组,按绘制顺序(后面的盖前面的) */ ],
  "appState": { "gridSize": null, "viewBackgroundColor": "#ffffff" },
  "files": {}
}
```

- `type` 必须是 `"excalidraw"`,`version` 用 `2`。`source` 用 `"https://excalidraw.com"`。
- `elements` 顺序 = z 序,靠后的画在上层。**容器要在它绑定的文本之前**、**箭头一般放最后**(压在节点上无所谓,但绑定关系靠 id 不靠顺序)。
- `files` 只有放 image 元素才用;纯框图留 `{}`。
- `appState` 保留少量即可;`collaborators` 别塞,`gridSize: null`。

**手绘感 = 两个字段**:每个可见元素 `roughness: 1`(artist,带手抖笔触;0=architect 更直,2=cartoonist 更夸张),文本 `fontFamily: 5`(Excalifont,即手写体;2=Helvetica、3=Cascadia 等宽)。

## 元素通用字段（每个元素都要有）

所有元素共享这套 base 字段。**缺字段轻则渲染异常、重则文件打不开**,务必给全:

| 字段 | 值 | 说明 |
|---|---|---|
| `id` | 唯一字符串 | 自己生成(见下方 helper),互相引用靠它 |
| `type` | `rectangle`/`diamond`/`ellipse`/`text`/`arrow`/`line`/`frame` | |
| `x`,`y` | number | 左上角画布坐标(y 向下为正) |
| `width`,`height` | number | 包围盒尺寸 |
| `angle` | `0` | 弧度制旋转 |
| `strokeColor` | `"#1e1e1e"` | 描边色(默认近黑) |
| `backgroundColor` | `"transparent"` 或 `"#a5d8ff"` 等 | 填充色 |
| `fillStyle` | `"solid"`/`"hachure"`/`"cross-hatch"` | hachure=手绘斜线填充,很出彩 |
| `strokeWidth` | `1`(细)/`2`(粗)/`4` | |
| `strokeStyle` | `"solid"`/`"dashed"`/`"dotted"` | |
| `roughness` | `1` | **手绘关键**:0/1/2 |
| `opacity` | `100` | 0–100 |
| `seed` | 随机正整数 | 固定笔触形状,渲染稳定 |
| `version` | `1` | 元素版本号,给 1 即可 |
| `versionNonce` | 随机正整数 | 协作对账用,给随机数 |
| `index` | `null` | 分数索引,新元素给 null(打开后自动补) |
| `isDeleted` | `false` | |
| `groupIds` | `[]` | 分组用,同组填相同 group id |
| `frameId` | `null` | |
| `boundElements` | `null` 或 `[{id,type}]` | **谁绑在我身上**(文本、指向我的箭头) |
| `updated` | 毫秒时间戳 | `int(time.time()*1000)` |
| `link` | `null` | |
| `locked` | `false` | |
| `roundness` | 见下 | 圆角:`null` 直角;`{"type":3}` 矩形自适应圆角;`{"type":2}` 线性/菱形 |

各形状特有字段:
- **text**:`text`、`originalText`(同 `text`)、`fontSize`(20)、`fontFamily`(**5**=Excalifont)、`textAlign`(`left`/`center`/`right`)、`verticalAlign`(`top`/`middle`)、`containerId`(绑进容器则填容器 id,否则 `null`)、`autoResize`(`true`)、`lineHeight`(`1.25`)。
- **arrow / line**:`points`(**相对 x,y 的点数组**,首点常 `[0,0]`)、`startBinding`/`endBinding`(`null` 或绑定对象)、`startArrowhead`/`endArrowhead`(`null`/`"arrow"`/`"triangle"`/`"dot"` 等)、`elbowed`(arrow 用,`false`=直线,`true`=直角折线)。line 另有 `polygon`(布尔)。

## 两种绑定（最容易写错，看清楚）

Excalidraw 的"关系"全靠 id 互引,有两处必须**双向**写对,否则拖动节点时文字/箭头不跟随、甚至报错。

### 1. 文本绑进容器(节点里的标签)

想让方框里有字、拖框时字跟着走:文本元素 `containerId` 指向容器,**同时**容器的 `boundElements` 要含这条文本。

- 文本:`"containerId": "<容器id>"`,且 `textAlign:"center"`、`verticalAlign:"middle"`(居中到框内);文本的 `x/y/width/height` 给个框内的近似值即可,打开后 Excalidraw 会按容器重新摆正。
- 容器:`"boundElements": [{"type":"text","id":"<文本id>"}]`。

> 若嫌绑定麻烦,也可以**不绑**:把 text 当独立元素摆到框中央(`containerId:null`,自己算居中坐标)。绑定的好处是拖动/缩放容器时文字自动重排;独立文本更简单但拖动不跟随。**做交付图优先用绑定**。

### 2. 箭头绑定两端节点(连线)

想让箭头两端"吸"在节点上、拖节点时线跟着弯:箭头的 `startBinding`/`endBinding` 指向节点,**同时**两个节点的 `boundElements` 各自加上这条箭头。

绑定对象(稳定发布版 v0.18 格式,**用 focus + gap**):

```json
"startBinding": { "elementId": "<起点节点id>", "focus": 0, "gap": 4 }
```

- `focus`:-1..1,箭头在节点边上的偏移(0=中心对齐,连线更直)。给 0 最省心。
- `gap`:箭头端点与节点边缘留的空隙(px),给 `2`~`8`。
- 两端节点都要在自己的 `boundElements` 里加 `{"type":"arrow","id":"<箭头id>"}`。

> 版本差异陷阱:Excalidraw **主分支(未发布)** 把绑定改成了 `FixedPointBinding`(`fixedPoint`+`mode`)。**面向 excalidraw.com 生产站点请用上面的 `focus`+`gap`**(v0.18 稳定格式,向前兼容)。真机打开若发现箭头不吸附,再核对当前站点版本的 schema。
>
> 箭头 `points` 是**相对箭头自身 x,y** 的坐标数组;箭头的 `x,y` 一般设为起点节点边缘附近,`points` 首点 `[0,0]`、末点是"到终点的相对位移"。绑定存在时坐标只要大致对,Excalidraw 打开后会按绑定重算吸附点。

## Python 生成配方（标准姿势）

在 `bash` 里跑内联 Python 或临时脚本;**只用标准库**(`json`/`random`/`time`/`itertools`)。核心是一个 `base()` helper 补齐通用字段,再写 `box()`/`text_in()`/`arrow()` 等小工厂,最后 `json.dump`。

```python
import json, random, time, itertools

_ids = ("el-%d" % i for i in itertools.count(1))   # 稳定可读的 id
def nid(): return next(_ids)
def rnd(): return random.randint(1, 2**31 - 1)
NOW = int(time.time() * 1000)

def base(**kw):
    d = dict(angle=0, strokeColor="#1e1e1e", backgroundColor="transparent",
             fillStyle="solid", strokeWidth=2, strokeStyle="solid",
             roughness=1, opacity=100, seed=rnd(), version=1, versionNonce=rnd(),
             index=None, isDeleted=False, groupIds=[], frameId=None,
             boundElements=None, updated=NOW, link=None, locked=False,
             roundness=None)
    d.update(kw); return d

def box(x, y, w=160, h=60, shape="rectangle", bg="transparent", rounded=True):
    return base(id=nid(), type=shape, x=x, y=y, width=w, height=h,
                backgroundColor=bg,
                roundness={"type": 3} if rounded and shape == "rectangle" else
                          ({"type": 2} if shape == "diamond" else None))

def label(cid, s, x, y, w, h, size=20):
    # 绑进容器的居中文本
    return base(id=nid(), type="text", x=x + 12, y=y + h/2 - size/2,
                width=w - 24, height=size * 1.25, text=s, originalText=s,
                fontSize=size, fontFamily=5, textAlign="center",
                verticalAlign="middle", containerId=cid, autoResize=True,
                lineHeight=1.25)

def bind(container, *child_ids_types):
    container["boundElements"] = [{"type": t, "id": i} for i, t in child_ids_types]

def arrow(x1, y1, x2, y2, src=None, dst=None, dashed=False, head="arrow"):
    a = base(id=nid(), type="arrow", x=x1, y=y1, width=x2 - x1, height=y2 - y1,
             strokeStyle="dashed" if dashed else "solid",
             points=[[0, 0], [x2 - x1, y2 - y1]],
             startArrowhead=None, endArrowhead=head, elbowed=False,
             startBinding={"elementId": src, "focus": 0, "gap": 4} if src else None,
             endBinding={"elementId": dst, "focus": 0, "gap": 4} if dst else None)
    return a

def dump(elements, path):
    scene = {"type": "excalidraw", "version": 2,
             "source": "https://excalidraw.com", "elements": elements,
             "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
             "files": {}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=2)
```

**用法**:造节点 → 造绑进去的文本 → 在节点上 `bind(...)` 登记文本和相连箭头 → 造箭头(带 start/end binding)→ 把所有元素按 z 序放进列表 → `dump`。

### 例 1：架构图（arch）

三层:客户端 → API 网关 → 服务,服务连数据库。方框 + 手绘填充色 + 绑定箭头。

```python
els = []
def node(x, y, s, bg, w=180, h=64):
    b = box(x, y, w, h, bg=bg); t = label(b["id"], s, x, y, w, h)
    els.extend([b, t]); return b, t

client, _ = node(60,  40, "Web / Mobile", "#a5d8ff")
gw,     _ = node(60, 180, "API Gateway",  "#b2f2bb")
svc,    _ = node(60, 320, "Order Service","#ffec99")
db,     _ = node(320, 320, "PostgreSQL",  "#ffc9c9")

a1 = arrow(150, 104, 150, 180, src=client["id"], dst=gw["id"])
a2 = arrow(150, 244, 150, 320, src=gw["id"],     dst=svc["id"])
a3 = arrow(240, 352, 320, 352, src=svc["id"],    dst=db["id"])
# 双向登记:每个节点记住连在自己身上的箭头 + 自己的文本
bind(client, (client_t_id := els[1]["id"], "text"), (a1["id"], "arrow"))
# ↑ 简洁起见实战里在 node() 返回后逐个 bind;关键是"节点.boundElements 要含其 text 和所有相连 arrow"
els += [a1, a2, a3]
dump(els, "/abs/path/arch.excalidraw")
```

> 实战建议把"登记 boundElements"做扎实:对每个节点,收集它的文本 id + 所有以它为 src/dst 的箭头 id,一次性 `bind()`。绑定不全不会打不开,但拖动时线不跟随。

### 例 2：流程图（flow）

圆角矩形起止、矩形处理、**菱形判断**(`shape="diamond"`)+ Yes/No 分支箭头(箭头上可再绑一条文本当标签)。

```python
els = []
start, _ = node(120,  40, "开始", "#b2f2bb", w=120, h=50)   # 圆角矩形当起点
step,  _ = node(100, 150, "读取输入", "#a5d8ff", w=160, h=60)
dec = box(110, 270, 140, 100, shape="diamond", bg="#ffec99")
dect = label(dec["id"], "有效?", 110, 270, 140, 100); els += [dec, dect]
ok,   _ = node(320, 285, "处理并保存", "#b2f2bb", w=160, h=60)
end,  _ = node(90,  430, "结束", "#ffc9c9", w=120, h=50)

els += [
  arrow(180,  90, 180, 150, src=start["id"], dst=step["id"]),
  arrow(180, 210, 180, 270, src=step["id"],  dst=dec["id"]),
  arrow(250, 320, 320, 315, src=dec["id"],   dst=ok["id"]),    # Yes →
  arrow(150, 370, 150, 430, src=dec["id"],   dst=end["id"]),   # No ↓
]
dump(els, "/abs/path/flow.excalidraw")
```

分支标签:在箭头中点附近放独立 text(`containerId:null`)写 "Yes"/"No",或绑进箭头的 `boundElements`(`type:"text"`)。

### 例 3：时序图（seq）

顶部参与者方框 + 向下的**虚线生命线**(`type:"line"`,dashed)+ 横向**消息箭头**(实线=调用,虚线=返回)。时序图节点不必互绑,重点是坐标对齐。

```python
els = []
actors = ["User", "API", "DB"]
xs = [100, 340, 580]
for name, x in zip(actors, xs):
    b = box(x-70, 30, 140, 50, bg="#a5d8ff"); els += [b, label(b["id"], name, x-70, 30, 140, 50)]
    # 生命线:从框底垂到 y=520 的虚线
    els.append(base(id=nid(), type="line", x=x, y=80, width=0, height=440,
                    strokeStyle="dashed", strokeColor="#868e96",
                    points=[[0,0],[0,440]], startArrowhead=None, endArrowhead=None,
                    elbowed=False))
def msg(x1, x2, y, s, ret=False):
    a = arrow(x1, y, x2, y, dashed=ret)          # 消息不绑生命线,直接横线
    els.append(a)
    els.append(base(id=nid(), type="text", x=min(x1,x2)+10, y=y-22,
                    width=abs(x2-x1)-20, height=20, text=s, originalText=s,
                    fontSize=16, fontFamily=5, textAlign="center",
                    verticalAlign="top", containerId=None, autoResize=True, lineHeight=1.25))
msg(100, 340, 140, "GET /orders")
msg(340, 580, 200, "SELECT *")
msg(580, 340, 260, "rows", ret=True)
msg(340, 100, 320, "200 OK", ret=True)
dump(els, "/abs/path/seq.excalidraw")
```

## 验证（交付前必做）

生成后**务必**验证文件是合法 JSON 且结构正确,别把坏文件发给用户:

```bash
python -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); \
assert d['type']=='excalidraw' and d['version']==2 and isinstance(d['elements'],list); \
ids={e['id'] for e in d['elements']}; \
print('ok', len(d['elements']),'elements'); \
[print('WARN dangling containerId',e['id']) for e in d['elements'] if e.get('containerId') and e['containerId'] not in ids]" /abs/path/x.excalidraw
```

- JSON 能 `json.load` 通过、顶层 `type/version/elements` 齐。
- 所有 `containerId`、`startBinding.elementId`、`endBinding.elementId`、`boundElements[].id` 引用的 id **都真实存在**于 elements(悬空引用会导致渲染异常)。
- 条件允许时,提示用户把文件拖进 excalidraw.com 打开确认 —— agent 端无法渲染,**真机打开是最终判据**。

## 安全 / 交付

- **产物落到 workspace(绝对路径),用 `[SEND:绝对路径]` 交付**,不要把整段 JSON 塞进对话正文(除非用户明确要看源码)。
- 扩展名用 **`.excalidraw`**(也可 `.json`,但 `.excalidraw` 能被插件/网站直接识别)。
- 纯本地文件生成,**无网络暴露、无鉴权面、无新依赖**。字体(Excalifont 等)由 Excalidraw 打开时自带,不需 agent 提供。
- 用户给的文本/标签原样使用;若含明显 PII 且只是占位需求,用通用占位名(如 Service A / User)。

