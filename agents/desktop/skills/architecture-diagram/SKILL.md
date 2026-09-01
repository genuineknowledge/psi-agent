---
name: architecture-diagram
description: "用暗色主题 SVG 画系统架构 / 云 / 基础设施拓扑图,产物是可直接打开的单文件 HTML —— dark-themed SVG architecture/cloud/infra diagrams as HTML。两条路线:(A) 手写内联 SVG,完全控制节点/分组/连线/图注,官方云图标(AWS/Azure/GCP)用 <image> 从 iconify CDN 引;(B) Mermaid architecture-beta,文本描述 groups/services/edges,注册 iconify 图标包(如 logos)后 service(logos:aws-s3)[标签],theme:'dark' 出暗色图。两者都内联到一个 .html(深色背景 + 无衬线字体 + 圆角卡片 + 柔和描边),写到 workspace 后用 [SEND:绝对路径] 交付。LOAD when 用户要画架构图/系统拓扑/云部署图/网络图/数据流图/微服务关系图/基础设施图/deployment diagram,且想要暗色好看的成品(网页/截图/贴文档)。NOT for 生成静态位图(用 image-generation)、纯文字排版创意(pretext)、PPT 里放图(powerpoint)、UML 类图代码逆向(binary-reverse-engineering)、或只是要一张现成参考图(直接搜)。"
category: creative
---

# Architecture Diagram（暗色 SVG 架构 / 云 / 基础设施图）

画**系统架构图**:云部署拓扑、微服务关系、网络分区、数据流、基础设施布局。产物是**单文件 HTML**——深色背景、内联 SVG(或内联 Mermaid)、圆角卡片节点、柔和描边连线、清晰图注。写到 workspace 后用 `[SEND:绝对路径]` 交付。**本 skill 纯前端:不写 Python、不新增依赖、不封装 tool。**

默认用中文回答,除非用户明显用别的语言。

> 这是操作指南,不是唯一事实来源。云图标集(AWS/Azure/GCP)和 Mermaid 语法都在迭代。下否定结论前以官方文档和真机跑通的 demo 为准,别照记忆硬套版本号或图标名。

## 何时使用

用户要**把系统结构画成图**,典型信号:

- **云部署图**:VPC/子网、EC2/Lambda/S3/RDS、负载均衡、CDN、跨可用区。
- **微服务 / 系统架构**:服务节点 + API 网关 + 消息队列 + 数据库 + 缓存,谁调谁。
- **网络 / 基础设施图**:防火墙、DMZ、内外网分区、VPN、专线。
- **数据流图**:数据从采集 → 处理 → 存储 → 消费的流向。
- **部署 / 拓扑图**:容器、Pod、节点、集群的编排关系。

且用户想要**暗色、好看、能直接用**的成品(嵌网页、截图贴文档、放 slide)。

**不用本 skill:**
- 要**静态位图文件**(png/jpg) → `image-generation`(`generate_image`)。
- 纯**文字视觉 / 排版创意** → `pretext`。
- 图要放进 **PPT** → `powerpoint`(可先用本 skill 出图再截进去)。
- 要**报告/文档**里配结构说明 → `document-report-authoring`。
- 只是想**找一张现成架构参考图** → 直接搜,不用画。

## 选路线:手写 SVG vs Mermaid

| | 手写内联 SVG(路线 A) | Mermaid architecture-beta(路线 B) |
|---|---|---|
| 控制力 | 完全:任意坐标、曲线、渐变、官方云图标 | 中:引擎自动布局,细节受限 |
| 出图速度 | 慢:要自己摆坐标 | 快:文本描述即出图 |
| 官方云图标 | 用 `<image>` 引 iconify SVG,像素级摆放 | 注册 iconify 图标包,`service(logos:aws-s3)` |
| 适合 | 讲究版式的成品图、非标准布局、要精修 | 快速草图、标准 groups/services/edges 拓扑 |

**默认建议**:节点 ≤ ~12 且是标准"分组—服务—连线"结构 → 路线 B 快出。要精致版式 / 特殊布局 / 强调品牌图标摆位 → 路线 A。两条路线都输出**同一种暗色单文件 HTML**。

## 暗色视觉规范(两条路线通用)

统一的暗色底盘,别每次重发明:

- 背景:深色渐变,如 `#0d1117 → #161b22`(GitHub 暗)或 `#0b0f19 → #131a2b`(蓝黑)。
- 卡片节点:`fill:#1f2430` 圆角 `rx=10`,描边 `stroke:#2f3646 stroke-width:1.5`,轻微阴影(`<filter>` 高斯模糊 + 低透明黑)。
- 文字:主色 `#e6edf3`,次要 `#8b949e`;无衬线 `font-family:'Inter','Segoe UI',system-ui,sans-serif`。
- 连线:`stroke:#4b5563`,箭头用 `<marker>`;强调路径用品牌色或 `#58a6ff`。
- 分组框(VPC/子网/集群):半透明填充 `fill:#ffffff08`,虚线描边 `stroke-dasharray:4 3 stroke:#30363d`,左上角标题。
- 强调 / 分类色板(节点着色):`#58a6ff`(蓝)`#3fb950`(绿)`#d29922`(黄)`#f85149`(红)`#bc8cff`(紫)。
- 图注(legend):右下或底部一行色块 + 文字。

## 路线 A:手写内联 SVG（完全控制）

一个 `.html`,`<body>` 深色背景,内联一段 `<svg viewBox="0 0 W H">`。基本骨架:

```html
<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>架构图</title>
<style>
  html,body{margin:0;background:#0d1117;color:#e6edf3;
    font-family:'Inter','Segoe UI',system-ui,sans-serif}
  .wrap{max-width:1100px;margin:0 auto;padding:24px}
  svg{width:100%;height:auto}
  .lbl{fill:#e6edf3;font-size:13px}
  .sub{fill:#8b949e;font-size:11px}
</style></head>
<body><div class="wrap">
<svg viewBox="0 0 1000 620" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#161b22"/>
    </linearGradient>
    <filter id="sh" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000" flood-opacity="0.4"/>
    </filter>
    <marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="#4b5563"/>
    </marker>
  </defs>
  <rect x="0" y="0" width="1000" height="620" fill="url(#bg)"/>

  <!-- 分组框(如 VPC) -->
  <rect x="60" y="70" width="880" height="360" rx="14"
        fill="#ffffff08" stroke="#30363d" stroke-dasharray="4 3"/>
  <text x="76" y="94" class="sub">VPC 10.0.0.0/16</text>

  <!-- 卡片节点(可复用成一个 JS 函数或手写) -->
  <g filter="url(#sh)">
    <rect x="120" y="140" width="150" height="70" rx="10" fill="#1f2430" stroke="#2f3646" stroke-width="1.5"/>
    <image href="https://api.iconify.design/logos/aws-ec2.svg?width=28&amp;height=28" x="134" y="154" width="28" height="28"/>
    <text x="172" y="170" class="lbl">Web 层</text>
    <text x="172" y="188" class="sub">EC2 · ASG</text>
  </g>

  <!-- 连线 -->
  <path d="M270 175 H360" stroke="#4b5563" stroke-width="1.6" fill="none" marker-end="url(#arw)"/>
</svg>
</div></body></html>
```

要点:
- **官方云图标**用 `<image href>` 从 **Iconify API** 引单个 SVG:`https://api.iconify.design/<pack>/<icon>.svg`(可加 `?width=28&height=28` 定尺寸;写进 HTML 属性时 `&` 要转义成 `&amp;`)。注意 **不是** `@iconify-json` 的 npm 包路径——那个包只有整包 `icons.json`,没有单文件 `/svg/*.svg` 子路径(会 404),单图标必须走 `api.iconify.design`。常用包:`logos`(彩色品牌 logo,含 `aws-ec2`/`aws-s3`/`aws-aurora`/`google-cloud`/`microsoft-azure`)、`skill-icons`、`devicon`。图标名去 icones.js.org 查。也可直接把厂商官方 SVG 内联进来。
- 节点多时,别一个个手写坐标:在 `<script>` 里用一个 `node(x,y,label,sub,icon)` 函数拼 SVG 字符串,循环生成,连线同理。生成完 `container.innerHTML = svg`。
- viewBox 用逻辑坐标,`svg{width:100%}` 自适应;图大就横向留白 + 允许滚动。

## 路线 B:Mermaid architecture-beta（文本描述,快速出图）

`architecture-beta` 是 Mermaid 专为架构/云拓扑设计的图类型:声明 `group`(分组)、`service`(服务节点)、`edge`(连线)、`junction`(汇合点)。默认只有 5 个内置图标(cloud/database/disk/internet/server),**要云厂商图标必须注册 iconify 图标包**。

自包含单文件 HTML:

```html
<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>架构图</title>
<style>html,body{margin:0;background:#0b0f19}
  .mermaid{display:flex;justify-content:center;padding:24px}</style>
</head>
<body>
<pre class="mermaid">
architecture-beta
    group api(logos:aws)[生产环境]

    service db(logos:aws-aurora)[数据库] in api
    service disk1(logos:aws-s3)[存储] in api
    service server(logos:aws-ec2)[服务] in api
    service gw(logos:aws-api-gateway)[网关] in api

    gw:R --> L:server
    server:R --> L:db
    server:B --> T:disk1
</pre>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.registerIconPacks([
    { name: 'logos',
      loader: () => fetch('https://cdn.jsdelivr.net/npm/@iconify-json/logos@1/icons.json').then(r => r.json()) },
  ]);
  mermaid.initialize({ startOnLoad: true, theme: 'dark' });
</script>
</body></html>
```

语法要点(以官方 docs 为准,当前需 Mermaid v11.1.0+):
- `service id(icon)[标签] in 组名` —— `in` 把服务放进分组;`group id(icon)[标签]` 声明分组。
- 连线带方向锚点:`svc:R --> L:other` 表示从 svc 右侧连到 other 左侧,方位是 `T/B/L/R`。`{group}` 语法可跨组连。
- 图标名 = 注册的包名 + iconify key:`logos:aws-s3`、`logos:google-cloud`、`logos:microsoft-azure`、`logos:postgresql`、`logos:redis`、`logos:kubernetes`、`logos:nginx`。去 icones.js.org 搜准确 key。
- 想更暗/定制:`mermaid.initialize({ theme:'base', themeVariables:{ darkMode:true, background:'#0b0f19', primaryColor:'#1f2430', lineColor:'#4b5563', primaryTextColor:'#e6edf3' } })`。
- 一个页面多张图:多个 `<pre class="mermaid">` 即可,`startOnLoad` 全渲染。

## 工作流程

1. **问清结构**(缺了就合理假设并说明):有哪些节点/服务?怎么分组(环境/VPC/层)?谁调用谁(连线方向)?云厂商是谁(决定图标包)?
2. **选路线**:标准拓扑快出 → B;讲究版式/精修 → A。
3. 写单文件 HTML,套用上面的暗色规范。
4. **本地验证**再交付(见下)。
5. 落到 workspace,`[SEND:绝对路径]` 交付。附一句这张图画了什么、怎么改(改哪段文本/坐标)。

## 验证(交付前必做)

- **打开自检**:HTML 能独立打开、SVG/Mermaid 真渲染出来、**图标真加载**(iconify CDN 通,别留空框/裂图);节点不重叠、连线不穿字、文字不溢出卡片。
- 路线 B 额外:Mermaid 语法错会整块渲染失败——先确认 `architecture-beta` 语法(方向锚点、`in` 分组、图标名)跑通,再加内容。
- 用 `browser_navigate` 打开产物 + `browser_take_screenshot` 截图肉眼核对是最稳的;没有 browser 工具时,至少静读 HTML 确认结构闭合、CDN URL 拼对、图标 key 存在。
- CDN 被墙/超时:mermaid 与 iconify 整包走 jsdelivr / unpkg 互为备份(`cdn.jsdelivr.net/npm/...` ↔ `unpkg.com/...`);路线 A 的单图标走 `api.iconify.design`。都不通就把用到的少量图标 SVG 直接内联进 HTML(把 `<image>` 换成内联 `<svg>`,可先 curl `https://api.iconify.design/<pack>/<icon>.svg` 拿到源码),彻底离线自包含。

## 安全 / 交付

- **产物落到 workspace 再用 `[SEND:绝对路径]` 交付**,别把整段 HTML 塞进对话正文(除非用户就要看源码)。
- **只从可信 CDN(jsdelivr / unpkg)引 mermaid 和 iconify 图标包**,pin 大版本(`@11`、`@1`);不引来路不明的第三方脚本。
- 纯前端、无服务端、无鉴权面——不涉及网络暴露风险。用户后续要托管上线再单独讨论。
- 架构图里若含真实内网 IP / 主机名 / 账号 ID 等敏感信息,按用户所给原样使用即可;若只是占位需求,用通用示例值(`10.0.0.0/16`、`svc-a`)。
