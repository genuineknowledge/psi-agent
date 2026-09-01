---
name: pretext
description: "用 @chenglou/pretext(15KB、零依赖的纯 JS/TS 文字测量与排版引擎,不碰 DOM、不触发 reflow)做创意浏览器 demo —— 把每个字符当几何体/物理体:ASCII art、文字绕障碍流动的排版、text-as-geometry 小游戏、动态字体动画(kinetic typography)、以文字驱动的生成艺术。产物是可直接打开的单文件 HTML(Canvas 2D/SVG + 从 esm.sh CDN 引入 pretext ESM),用 [SEND:绝对路径] 交付。LOAD when 用户要做酷炫的文字视觉/文字动画/文字排版 demo、文字绕图形流动、每个字都会动/受物理影响的效果、ASCII 动画、或想把一段文本变成可玩/可看的网页作品。NOT for 常规网页 UI 开发、只测一段文字高度做虚拟列表(那是 pretext 的本职但不算 creative demo)、生成静态图片(用 image-generation)、或后端文本处理(data-text-processing)。"
category: creative
---

# Pretext（文字即几何 · 创意浏览器 demo）

用 **`@chenglou/pretext`** 做**创意向的文字视觉作品**。pretext 是 Cheng Lou 写的纯 JS/TS 库(~15KB、零运行时依赖):它**不碰 DOM**(不用 `getBoundingClientRect`/`offsetHeight`,因此不触发 layout reflow),而是用它自己的字体测量逻辑,**纯算术**算出每一行、每个 grapheme 的宽度与位置。对创意 demo 的意义:你能拿到**每个字符的精确坐标**,于是可以把字当成粒子/物理体/几何单元来摆、来动、来碰撞,而不是交给 CSS 去排。

本 skill 的产物是**单文件 HTML**:一个 `.html` 里内联 CSS + JS,用 `<script type="module">` 从 **esm.sh CDN** 引入 pretext,渲染到 **Canvas 2D**(首选)或 SVG。写完落到 workspace,用 `[SEND:绝对路径]` 交付给用户。**本 skill 不写 Python、不新增依赖、不封装 tool** —— 纯前端。

默认用中文回答,除非用户明显用别的语言。

> 这是操作指南,不是唯一事实来源。pretext 迭代快(当前 npm latest 0.0.8),导出函数名、options、子路径都可能变。下否定结论前,以真机 `README.md`(仓库 `chenglou/pretext`,AGENTS.md 明确它是 API 的权威来源)和实际跑通的 demo 为准,别照记忆硬套 API。

## 何时使用

用户想要**把文字做成视觉/可玩作品**,典型信号:

- **ASCII art / ASCII 动画**:字符排成图案、随时间流动变形。
- **文字绕障碍流动**(text flow around obstacles / floated shapes):正文绕开一张图、一个圆、一个不规则形状排版。
- **text-as-geometry 小游戏**:每个字是碰撞体/可点击目标/掉落物。
- **kinetic typography**:字随物理(重力、弹簧、鼠标斥力)、随时间、随音频抖动/散开/聚拢。
- **文字驱动的生成艺术**:用一段文本 + 噪声/流场,生成不断演化的画面。

**不用本 skill:**
- 只是**常规网页布局/组件**(按钮不换行、虚拟列表高度)—— pretext 能干,但那不是本 creative skill 的目标,直接写普通前端即可。
- 要**静态图片文件** → `image-generation`(`generate_image` tool)。
- 要**后端/离线文本处理**(清洗、抽取、分类) → `data-text-processing` / `text-classifier-training`。
- 要**本地像素/渲染/抽帧** → `media-graphics`。

## pretext 心智模型(先懂这个再写)

pretext 分**两次调用**:一次贵的预处理 `prepare*()`,之后多次便宜的 `layout*()`。**永远不要对同一段文本+同一 font 重复 `prepare()`** —— 那等于白费它的预计算。resize/每帧只重跑 `layout*()`。

它服务两大用例:

1. **只要高度/行数**(虚拟化、防抖):`prepare()` → `layout()`。
2. **要自己摆每一行/每个字**(创意 demo 几乎都走这条):`prepareWithSegments()` → `layoutWithLines()` / `walkLineRanges()` / `layoutNextLineRange()`。

**font 与 letterSpacing 必须和你实际画字的样式一致**:`font` 用的是 canvas `ctx.font` 的格式(如 `'16px Inter'`、`'18px "Helvetica Neue"'`),`lineHeight` 要和你排版用的行高一致,`letterSpacing` 是 CSS px 值。测量样式和渲染样式不一致 = 坐标全错。

## 核心 API 速查(以 README 为准)

用例 1(测高):
- `prepare(text, font, options?) -> PreparedText`
- `layout(prepared, maxWidth, lineHeight) -> { height, lineCount }`

用例 2(手动排版,创意 demo 主力):
- `prepareWithSegments(text, font, options?) -> PreparedTextWithSegments`
- `layoutWithLines(prepared, maxWidth, lineHeight) -> { height, lineCount, lines: LayoutLine[] }` —— 固定宽度,一次拿到所有行。
- `walkLineRanges(prepared, maxWidth, onLine)` —— 每行回调一次,给宽度 + start/end cursor,**不建行字符串**;适合二分搜索"好看"的宽度(shrinkwrap / balanced text)。
- `measureLineStats(prepared, maxWidth) -> { lineCount, maxLineWidth }`、`measureNaturalWidth(prepared)`。
- `layoutNextLine(prepared, start, maxWidth) -> LayoutLine | null` / `layoutNextLineRange(...) -> LayoutLineRange | null` —— **逐行、每行可用不同宽度**,这是"文字绕障碍"的关键:每行按当前 y 是否撞到障碍来给不同 maxWidth。用上一行的 `end` 当下一行的 `start`,返回 `null` 表示排完。
- `materializeLineRange(prepared, range) -> LayoutLine` —— 把 range 变回带 `text` 的完整行。

`options`:`{ whiteSpace: 'pre-wrap' }`(保留空格/tab/硬换行,像 textarea)、`{ wordBreak: 'keep-all' }`、`{ letterSpacing: n }`。软连字符(soft hyphen `­`)会被当可选断点。

富文本内联(chips/mentions/多字体一行)另有子模块 `@chenglou/pretext/rich-inline`:`prepareRichInline(items)`、`layoutNextRichInlineLineRange(...)`、`walkRichInlineLineRanges(...)`、`materializeRichInlineLineRange(...)`。`RichInlineItem` 支持 `break: 'never'`(原子块)和 `extraWidth`(pill 的 padding/border)。

`LayoutLine = { text, width, start, end }`;`LayoutCursor = { segmentIndex, graphemeIndex }`。

## 引入方式(单文件 HTML · CDN ESM)

无需 npm、无需构建。单文件 HTML 里用 ESM import,**pin 版本**(当前 `0.0.8`):

```html
<script type="module">
  import { prepareWithSegments, layoutWithLines }
    from 'https://esm.sh/@chenglou/pretext@0.0.8';
  // 富文本子路径:from 'https://esm.sh/@chenglou/pretext@0.0.8/rich-inline'
</script>
```

- **一定 pin 版本号**(`@0.0.8`),别用无版本 URL —— 免得哪天上游发版把 demo 冲挂。
- esm.sh 已验证可达并返回 `application/javascript`;`/rich-inline` 子路径同样可用。
- 想完全离线自包含:`npm pack @chenglou/pretext`(或从 CDN 抓 `.mjs`)把源码内联进 `<script type="module">`。默认用 CDN 即可,除非用户要求离线单文件。
- 备选 CDN:`https://cdn.jsdelivr.net/npm/@chenglou/pretext@0.0.8/+esm`(esm.sh 抽风时用)。

> 关键陷阱:pretext 用 **canvas 文字测量**做地基,依赖浏览器字体引擎。**它跑在浏览器里,不在 Node/Python**。所以本 skill 的交付物必须是用户在浏览器里打开的 HTML;不要试图在 agent 端"跑" pretext 出结果。字体也要真的能加载(系统字体或 `@font-face`/Google Fonts),否则测量基准和渲染不一致。

## 写 demo 的标准姿势

1. **一个 `<canvas>`**,拿 `const ctx = canvas.getContext('2d')`,设 `ctx.font` = 你要测的 font(和传给 `prepare*()` 的**完全一致**),处理 devicePixelRatio 高清屏(`canvas.width = cssW * dpr; ctx.scale(dpr, dpr)`)。
2. **prepare 一次**:`const prepared = prepareWithSegments(text, FONT)`(FONT 常量,如 `'20px Inter'`)。
3. **布局**:静态就 `layoutWithLines(prepared, maxWidth, lineHeight)` 拿 `lines`;绕障碍/可变宽就 `layoutNextLineRange` 循环。
4. **渲染 + 动画**:`requestAnimationFrame` 循环里 `ctx.clearRect` → 画字。要逐字动画时,自己按 `line.text` 的 grapheme 累加宽度算每个字的 x(用 `ctx.measureText(char).width`,或对逐字精度更高的场景把每个字当独立文本 prepare)。给每个字附物理状态(位置、速度),每帧更新。
5. **落盘 + 交付**:把 HTML 写到 workspace(绝对路径),然后 `[SEND:绝对路径]` 交付。

### 例:文字绕圆形障碍流动(核心手法)

```html
<canvas id="c" width="800" height="600"></canvas>
<script type="module">
import { prepareWithSegments, layoutNextLineRange, materializeLineRange }
  from 'https://esm.sh/@chenglou/pretext@0.0.8';

const ctx = document.getElementById('c').getContext('2d');
const FONT = '18px Georgia', LH = 26, COL = 760, PAD = 20;
ctx.font = FONT;

const text = '这里是一整段会绕开中间那个圆的正文…（放你的长文本）';
const prepared = prepareWithSegments(text, FONT);
const circle = { cx: 400, cy: 300, r: 90 };

let cursor = { segmentIndex: 0, graphemeIndex: 0 };
let y = PAD;
while (true) {
  // 这一行的可用宽度:若该行 y 落在圆的纵向范围内,就把圆挖掉一块
  let maxW = COL;
  if (y + LH > circle.cy - circle.r && y < circle.cy + circle.r) {
    maxW = Math.max(40, circle.cx - circle.r - PAD); // 只在圆左侧排字(简化版)
  }
  const range = layoutNextLineRange(prepared, cursor, maxW);
  if (range === null) break;
  const line = materializeLineRange(prepared, range);
  ctx.fillText(line.text, PAD, y + LH);
  cursor = range.end;
  y += LH;
}
</script>
```

真正的双侧绕流可对每行左右各排一段(左块用左侧宽度、右块从障碍右缘继续),核心永远是**每行按当前 y 与障碍的关系给不同的 maxWidth**,这正是 `layoutNextLineRange` 的用途。

### 例:kinetic typography（每个字是物理体）

思路:先 `layoutWithLines` 拿到每行 `text` 和起点,遍历每行的 grapheme,用 `ctx.measureText` 累加算出每个字的初始 (x, y),给它一个粒子对象 `{ ch, x, y, vx, vy, homeX, homeY }`;每帧对鼠标位置施加斥力、对 home 施加弹簧力,`requestAnimationFrame` 里 `clearRect` 后逐字 `fillText(ch, x, y)`。pretext 负责给你**准确的排版基线**(homeX/homeY),物理动画是你自己的循环。

## 参考 demo(找灵感/抄手法)

- 官方 live:`chenglou.me/pretext/`(仓库 `chenglou/pretext` 的 `/demos`,`bun start` 本地跑;`dynamic-layout` demo 是可变宽逐行布局的富例子)。
- `pretext.cool`:20 个交互 demo(reflow、游戏、ASCII art、多语种编辑排版)。
- `Poojan38380/pretext-playground-upgrade`:三个 Canvas 2D demo(物理龙散字、深海水母、ASCII 动画),"每个字符都是物理体、零 DOM 文字",是本 skill 目标形态的极佳范本。

## 安全 / 交付

- **产物落到 workspace 再用 `[SEND:绝对路径]` 交付**,别把整段 HTML 塞进对话正文(除非用户就要看源码)。
- **只从可信 CDN(esm.sh / jsdelivr)引 pretext**,pin 版本;不要引来路不明的第三方脚本。
- 用户给的文本原样使用即可;若含明显 PII 且只是占位需求,用通用占位文本。
- 纯前端、无服务端、无鉴权面 —— 不涉及网络暴露风险。若用户后续要把 demo 托管上线,再单独讨论托管方式。
