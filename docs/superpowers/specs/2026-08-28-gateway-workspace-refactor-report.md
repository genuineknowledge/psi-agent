# Gateway / Workspace 架构重排 —— 交付汇报

**汇报日期**：2026-08-28　**分支**：`refactor/gateway-workspace-evolution` @ `3fa34a4c`　**基线**：`main` @ `64b6273b`（未动）

---

## 一、结论

本轮 **A1–A7 七项 + B1/B2/B5/B6 四项落地并验收通过**（B2 与 A 线共用一次提交），**B3 做过后撤回**，另完成 review 提出的两项：OAuth 中继搬进 `feishu/`、抽出 `workspace/toc`。核心目标达成：

| 目标 | 结果 | 判据 |
|---|---|---|
| 骨架层与产品线解耦 | **达成** | 骨架 `.py` 从 26 → **6** 个；`server.py` 对产品符号的反向 import **8 → 0** |
| 内核不反向依赖产品线 | **达成** | 新建 `runtime/` 包（11 文件），`runtime → gateway` 代码依赖 **0** |
| 两条产品线各自成包 | **达成** | `desktop/` 12 个 `.py`、`feishu/` 5 个 `.py`，互不 import |
| ToB 能力包脱离 `examples/` | **达成** | `examples/` 12 → **11** 个示范件；生产资产独立为 `workspace/tob`（486 文件） |
| ToC 能力包独立 | **达成** | 抽出 `workspace/toc` **266 文件**；提示词里点名却不存在的工具 83 → **0**（见 2.5） |
| 行为零回归 | **达成** | 全量 57 failed / **1481 passed** / 7 skipped，失败集与基线**逐条相同** |
| ToB 前端脚手架 | **达成** | 9 个源文件，构建 654ms，占位页 `GET /defaults` HTTP 200 |

**第九章的 7 项讨论已于 2026-08-29 过会，5 项拍定、2 项仍开放。**

- **已定**：用户数据移出 `{app}` 落到 AppData 的 `.haitun`（9.1 + 9.3 + 9.6 收敛成一个方向，具体落点与实施推后）、开发启动方式**加参数分开选**而不是选个习惯（9.2）、`toc`/`tob` 的重复**短期接受、记为欠账**（9.4）、**一次性合并主干且改名先落**（9.7）。会上另追加一项：顶层 `workspace/` 改名 `agents/`、`tob`/`toc` 改名 `feishu`/`desktop`（9.8）。
- **仍开放**：桌面版要不要长期记忆（9.5），依赖 ToC 身份体系，不是本轮范围。

已定的都落成了 Kanban 任务，任务号见 9.0 的表。各小节保留了过会前的原始材料 —— B3 正是没先定边界就动手、做完发现没换来保护而撤回的例子，留着是为了让后来人看得到当时为什么这么选。

**注意：本报告里所有 `workspace/tob`、`workspace/toc` 字面路径都是改名前的坐标**，读的时候按 `agents/feishu`、`agents/desktop` 换算。

**还差的一件事**：`workspace/toc` 已能被 Gateway 挂上（负责人实操，启动日志 `Default agent: …\workspace\toc`），但**没在它上面真跑过一轮对话** —— 工具真调起、skill 真读入、回复真产出这一段没验。见 7.1。

---

## 二、改了什么（结构对比）

本轮动了**两块**：`src/` 下的代码结构（A 线）与 `workspace/` + 安装器结构（B 线）。分开讲。

### 2.1 一句话概括

**代码侧**：原来 `gateway/` 一个包里塞了 26 个模块 —— 内核管理器、桌面端（托盘/webview/登录）、飞书端混在一起，且内核管理器反向 import 桌面端的品牌字面量。现在切成三层：内核 `runtime/`、骨架 `gateway/`、产品线 `gateway/{desktop,feishu}/`。

**workspace 侧**：ToB 的能力包原先藏在 `examples/` 里当"示范件"，实际是生产资产（安装器打的就是它）。现在迁出为顶层 `workspace/tob`，并把包内 / 包外文件在安装器里分清落点。

### 2.2 代码结构前后对照（A 线）

```
【改前】main @ 64b6273b                    【改后】HEAD @ 3fa34a4c
src/psi_agent/gateway/                     src/psi_agent/runtime/          ← 新建, 内核
├── _ai_manager.py        ┐               ├── _ai_manager.py        ┐
├── _session_manager.py   │ 10 个          ├── _session_manager.py   │ 10 个 manager
├── _scheduler_manager.py │ manager        ├── _scheduler_manager.py │ 1740 行整体平移
├── ... (另 7 个)          ┘               ├── ... (另 7 个)          ┘
├── _tray.py              ┐               │
├── _webview.py           │               src/psi_agent/gateway/           ← 骨架, 只剩 6 个 .py
├── _auth_manager.py      │ ToC           ├── __init__.py      (装配入口)
├── _workspace_manager.py │ 12 个         ├── server.py        (1031 → 610 行)
├── ... (另 8 个)          ┘               ├── _defaults.py     (品牌字面量唯一落点)
├── _feishu_manager.py    ┐ ToB           ├── _openapi.py / _openapi_core.py
├── ...                   ┘               ├── _oauth_manager.py / _state.py
├── spa/  spa-v2/         (2 棵 SPA)      ├── desktop/         ← ToC 产品包 (12 .py + 2 棵 SPA)
├── server.py  (1031 行)                  └── feishu/          ← ToB 产品包 (4 .py + feishu-web)
└── _openapi.py (915 行)
```

### 2.3 workspace 与安装器结构前后对照（B 线）

```
【改前】main @ 64b6273b                    【改后】HEAD @ 3fa34a4c
examples/                                  examples/                  ← 只剩 11 个示范件
├── haitun-workspace/   ← 生产资产          ├── openclaw-style-workspace/
│   485 文件, 安装器打的就是它,             ├── hermes-style-workspace/
│   却和示范件混住                          ├── ... (另 9 个, 全是真示范)
├── openclaw-style-workspace/               │
├── hermes-style-workspace/                 workspace/                 ← 新建顶层目录
├── ... (另 9 个)                           ├── tob/                   ← 486 文件, 生产资产独立
   合计 12 个                               └── toc/                   ← 269 文件, 桌面版能力包
                                               合计 11 + 2

安装器 haitun.iss                           安装器 haitun.iss
Source: 11 条                               Source: 11 条  ← B3 已撤回, 见 7.3 / 9.1
└── examples\haitun-workspace\*             └── workspace\tob\*
    整目录一把拷贝, 升级时                       整目录一把拷贝, 换了路径没换语义,
    用户数据混在里面分不出来                     出厂内容与用户数据仍混住(留作讨论项)
```

| 指标 | 改前 | 改后 |
|---|---|---|
| `examples/` 下 workspace 个数 | 12（含 1 个生产资产） | **11**（全是真示范件） |
| `workspace/` 顶层目录 | 不存在 | **新建**，含 `tob` + `toc` |
| ToB 能力包文件数 | `examples/haitun-workspace` 485 | `workspace/tob` **486** |
| ToC 能力包文件数 | 不存在 | `workspace/toc` **266**（见 2.5） |
| 该次搬迁改动量 | — | 529 文件，+250 / −243 |
| 安装器 `Source:` 行数 | 11 | **11**（B3 已撤回，见 7.3 / 9.1） |
| `system_prompt.py` 推 agent 根方式 | `__file__` 硬推 4 处 | **接收传入路径**（B6，+328 / −22） |
| hook 契约测试 | 无 | **12×6 实测表**（B5） |

**B3 做过又撤回了，`Source:` 由 14 条回到 11 条** —— B3 原本把 `SOUL.md` / `USER.md` / `schedules\*` 从整目录拷贝里摘出来单独列，想让「哪些是出厂的、哪些是用户的」在安装器里有个落点。撤回的理由是：`{app}\app` 上挂的是 `SwapComponent('app')`，升级时整目录换新，**单独列 `Source:` 并不改变任何换新行为** —— 摘出来的三项照样被换掉。也就是说 B3 只是把清单写长了 3 行，没换来任何保护，反而让人以为这问题已经处理过了。出厂内容与用户数据怎么分，本身是个要先定边界再动手的设计题（升级保数据、用户改过的出厂文件怎么办、`SOUL.md` 归谁），已作为 **9.1** 的讨论项，定下来后单独开 PR 做。

### 2.4 关键数字汇总

| 指标 | 改前 | 改后 |
|---|---|---|
| `gateway/` 骨架层 `.py` 文件数 | 26 | **6** |
| `runtime/` 内核包 `.py` 文件数 | 0（不存在） | **11** |
| `server.py` 行数 | 1031 | **610** |
| `server.py` 反向 import 产品符号 | 8 行 | **0 行**（`_oauth_manager` 已搬进 `feishu/`，见 4.1） |
| `runtime → gateway` 代码依赖 | 内核候选有 2 处指向 ToC | **0** |
| `_openapi` | 915 行单文件 | 拆 4 份（装配 58 + 公共 CORE 16 path + ToC 6 + ToB 4），26 个 path key 并集不变 |
| `examples/` 下 workspace 个数 | 12 | **11** |
| `workspace/` 下能力包个数 | 0（不存在） | **2**（`tob` + `toc`） |
| 安装器 `Source:` 行数 | 11 | **11**（B3 的分包改动已撤回，见 7.3 与 9.1） |
| 提交数 / 改动量 | — | 18 次提交（另加本轮 `toc` 抽取） |

### 2.5 `workspace/toc` 抽取（本轮新增）

以 `workspace/tob` 为起点，去掉落到飞书的能力，留下通用能力，抽出桌面版能力包 `workspace/toc`。

| 指标 | 数字 |
|---|---|
| 文件数 | **266**（从 tob 的 486 个在库文件里挑，丢弃 220） |
| 工具 `.py` 文件 / 实际注册工具 | **85 文件 / 93 工具**（tob 是 151 文件） |
| skills | **102**（tob 是 145，少 43 个 ToB 专用的） |
| hook 数 | **6/6**（与 tob 一样全解析，`systems/` 只差 3 处提示词文字） |
| 组装出的系统提示词长度 | **131707 字符**，是 tob 的 66% |
| 提示词里点到但包内不存在的工具 | **0**（第一版是 83 个，见下） |
| 提示词里 `feishu` 出现次数 | **6**（tob 是 398），全是刻意留的交叉引用 |

**丢弃规则**（按区块）：`tests/` 67 个全丢（测的是 ToB 行为），`channel_events/` 41 个全丢（飞书事件落库），43 个 ToB 专用 skill 丢，66 个不在通用闭包里的工具丢。

**判定通用与否，用内核自己当裁判，没用我的静态分析。** 起因是我先后用 AST、正则、精确 import 三种办法算依赖闭包，得出 92 / 95 / 93 / 85 四个互相矛盾的数字，连 5 个 `memory_*` 到底算不算通用都对不上。根因是这个仓库里有**三种 import 写法**并存：普通 `import`、`_load_sibling_module("名字")` 字符串加载、`TOOLS_DIR / f"{名字}.py"` 拼路径后 `exec`。后两种任何静态分析都抓不全。改成直接调内核的 `ToolRegistry.load()` 加载两个包再比集合，才拿到可信的数：`toc` 有而 `tob` 没有的工具 **0 个**，`toc` 里的飞书/派工工具名 **0 个**。

**`memory_*` 5 个工具不带**，因为这条链是硬的：`memory_*` → `_fusion_memory_mcp.py:56` → `_fusion_memory_membership.py:14` → `_feishu_impl.py`。「谁的身份在写记忆」是拿飞书 `open_id` 认的，桌面版没有飞书身份，整条链落不了地。桌面版要长期记忆，需要另设一套本地身份，不是把这条链搬过来 —— 列为 **9.5** 讨论项。

**顺手抓出一个纯拷文件会静默带走的缺陷。** `AGENTS.md` / `TOOLS.md` / `IDENTITY.md` / `BOOTSTRAP.md` 这四个文件是被 `_build_bootstrap_files` **整篇原文塞进系统提示词**的。直接拷过来，提示词里就点着 83 个这个包里并不存在的工具名 —— 模型会照着去调，然后拿到报错。所以这四份文档连同 `systems/prompt_sections.py` 里三处无条件注入的段落都得改：删掉 22 行过期工具表、25 条过期 skill 条目、整节 Fusion Memory 与 Channel events，把 `SEND_FILES_SECTION` 的渠道列表和 `SILENT_REPLIES_SECTION` 的飞书卡片例外改掉。83 → 6 → 3 → **0**，每一轮都是重新组装提示词实测出来的。中间还有一次是我自己写的说明文字漏了 —— 我在「本包没有记忆工具」那节里把 5 个工具名写了出来，这段本身又进了提示词，等于换个地方点名。改成不写名字、只讲原因。

---

## 三、逐项任务与验收

### A 线（骨架拆分，7 项）

| 项 | 做了什么 | 验收判据（实测） |
|---|---|---|
| A1 | 切断内核候选对 ToC 的 2 处依赖，品牌字面量收拢到 `_defaults.py` | 该文件成为 `haitun交付` / `workspace/tob` 的唯一落点 |
| A2 | 10 个 manager、1740 行移出 `gateway/` 建 `runtime/` | `runtime → gateway` 依赖归零 |
| A3 | `_openapi.py` 915 行按 path 拆三份 | 26 个 path key 并集与 schema **逐一不变** |
| A4 | 17 参装配函数拆成骨架 + 两个"贴纸" | 桌面端不再构造飞书管理器 |
| A5 | 12 个产品模块 + 2 棵 SPA 落位 `desktop/` 与 `feishu/` | 骨架层剩 5 个装配件 |
| A6 | ToB 前端脚手架 9 个源文件 + 1 个静态挂载点 | S1–S6 六条全过，后端只多 1 个 `add_static` |
| A7 | 两个 `register_*_routes` 搬进产品包 | 骨架反向 import **7 → 0**；路由表逐条不变 |

### B 线（workspace 与内核，5 项）

| 项 | 做了什么 | 验收判据（实测） |
|---|---|---|
| B1/B2 | `examples/haitun-workspace` → `workspace/tob` | 60 处引用清零；**补回 10 个静默消失的测试** |
| B3 | ~~ToC workspace 分包内 / 包外~~ **已撤回** | 做过并逐条核对过 14 个 `Source`、516 文件落点不变，但**撤回了** —— 单独列 `Source:` 不改变 `SwapComponent('app')` 的整目录换新行为，没换来保护。见 2.3 与 9.1 |
| B5 | hook 契约钉成 12×6 实测表 | 实测 12 个 workspace 里**只有 `tob` 满 6 个，其余 11 个只暴露 2–3 个** |
| B6 | 4 处 `__file__` 推根改为接收传入路径 | 5 个 hook 调用点补上 agent 根 |

**B1/B2 的意外收获**：两个测试文件用 `Path("examples").glob("*/systems/system.py")` 做参数化，`haitun-workspace` 一搬走，它的 10 个用例**不报错地消失了** —— pytest 不认为参数化列表变短是错误。这类"静默丢测试"是搬迁类改动的典型陷阱，已补回。

**B5 的判据纠正**：任务书原写"逐个 workspace 断言 6 个 hook 都非 None"，这是**错的判据**。`turn_context_fn` 与 `compaction_fn` 的 `None` 按内核约定**承载语义**（"这个 workspace 没有易变块"），断言全非 None 会一次红 11 个 workspace，钉的是内核并不存在的契约。

---

## 四、骨架层剩下这 6 个文件，各自为什么留下

拆完之后有人会问：既然分了 ToC 包和 ToB 包，为什么骨架层还剩这几个文件？逐个给判据。

| 文件 | 行数 | 它是什么 | 为什么不能进产品包 |
|---|---|---|---|
| `__init__.py` | — | **总装配入口**。先起骨架，再把 ToC / ToB 两张"贴纸"贴上去 | 它要同时认识两条线才能装配，进任一包都会让那个包被另一条反向依赖 |
| `server.py` | 610 | **公共路由** —— 会话、AI、标题、摘要这些两条线都要的接口 | 两条线共用，不归任何一条 |
| `_defaults.py` | 105 | **品牌字面量的唯一落点**（`haitun交付`、`workspace/tob`） | 内核建 Session 要拿这些路径。放进产品包，内核就得反向 import 产品线 —— 正是 A1 消除的那种依赖 |
| `_state.py` | — | 重启后恢复现场用的状态快照 | 两条线共用 |
| `_openapi.py` | 58 | **说明书装订工**（详见 4.2） | 同时 import 三份章节，进任一包即造成两线互相耦合 |
| `_openapi_core.py` | 700 | **说明书公共章节**，16 条两线都注册的接口 | 不归任何一条线 |

`_oauth_manager.py` 本来是第 7 个，本轮已搬进 `feishu/`，详见 4.1。

### 4.1 `_oauth_manager.py` —— 本轮已搬进 `feishu/`

它做的事很小：浏览器跳到 `/oauth/callback` 时，按一个随机串 `state` 把授权码 `code` 存进带 TTL 的内存信箱；发起方用同一个 `state` 从 `/oauth/code` 取走，一次即删。用途是**免去用户从地址栏手工复制授权码**。

我最初把它留在骨架层，理由是"这段代码不认识飞书"。这一半成立 —— 69 行里零飞书字样，不碰 token 交换，不知道 app_secret，也不知道是哪个用户。

**但漏查了实际消费者。补测结果：取件方全在 ToB 一侧，ToC 零调用。**

```
workspace/tob/tools/_oauth_receiver.py:38    _CALLBACK_PATH = "/oauth/callback"
workspace/tob/tools/_oauth_receiver.py:220   client.get(f"{base}/oauth/code", ...)
workspace/tob/tools/_oauth_setup.py          整个文件都在讲飞书后台怎么登记回调
workspace/tob/tools/feishu_auth.py:16        通过 Gateway 的 /oauth/callback 中继
workspace/tob/tools/_feishu/auth.py:21       import _oauth_receiver

desktop/_auth_manager.py:7                   仅注释, 说命名风格与 OAuthRelay 同级
desktop/_auth_manager.py:16                  仅注释, "跳转留给将来的 OAuth"  ← 将来时
```

**ToC 的登录已经做完了，走手机号 + 验证码，不经过 OAuth 跳转。** 我此前拿 `_auth_manager.py:16` 那句"将来复用"当留在骨架层的论据，是用一句注释里的将来计划去支撑一个当下的位置决定 —— 违背方案自己的"先问存在性、不为假想需求预留"。

**结论：按当前实测它应当搬进 `feishu/`。** 这属于"机制通用但当前只有一个消费者"，方案 §3.2 四条判据里"认识什么概念"中立、"先问存在性"指向产品包，判据本身有冲突，不是一边倒。选择搬走的理由是：消费者单一且明确，等 ToC 真要用 OAuth 那天再往上提，那时是有真实需求驱动的移动，成本比现在为假想需求占位低。

**本轮已做**（提交 `456009d3`）。实际改动面：文件搬到 `gateway/feishu/_oauth_manager.py`，`server.py` 去掉 3 处（`app["oauth"]` 赋值 + 两个 handler）与 2 条路由注册、改由 `feishu/_routes.py` 注册，spec 片段从 `CORE_PATHS` 挪进 `FEISHU_PATHS`，另动 3 个测试文件与 `gateway/AGENTS.md` 3 处。

**实测结果**：骨架层 `.py` 由 7 降到 **6**；`server.py` 1031 → **610** 行；骨架层反向 import 产品符号 **8 行 → 0 行**；`_openapi_core` 公共章节 18 → **16** 条，`FEISHU_PATHS` 2 → **4** 条，**26 个 path key 的并集与 schema 逐一不变**；全量失败集合与基线 57 行逐行相同。

**这件事的方法论收获**：方案 §3.2 那四条判据里，「代码认识什么概念」和「先问存在性」会打架 —— 这个文件 69 行零飞书字样，按前者是通用的；消费者全在 ToB，按后者归产品包。**冲突时以「先问存在性」为准** —— 一句写着「将来」的注释不能支撑当下的位置决定。

### 4.2 两个 `_openapi*` 文件是干什么的

**不是注册路由，是生成一份 API 说明书。** 这两件事要分清：

| | 谁干的 | 作用 | 删掉会怎样 |
|---|---|---|---|
| **注册路由** | `server.py` + 两个 `_routes.py` | 告诉服务器"有人访问 `/sessions` 时执行哪个函数" | **接口不能用了** |
| **OpenAPI** | 四个 `_openapi*` 文件 | 生成一份 JSON 说明书，写着本服务有哪些接口、各收什么参数、返回什么 | 接口照样能用，只是**没人知道怎么对接** |

访问 `http://127.0.0.1:8080/openapi.json` 拿到的就是这份说明书，前端和第三方靠它对接。

那三份是说明书的三个章节，第四份是装订工：

```
_openapi_core.py    700 行  →  16 条公共接口的说明   (/sessions /ais /titles ...)
desktop/_openapi.py 111 行  →   6 条 ToC 专属接口     (/ui/* /workspace/*)
feishu/_openapi.py   98 行  →   2 条 ToB 专属接口     (/feishu/route /feishu/routes)
_openapi.py          58 行  →  把上面三份订成一本
```

18 + 6 + 2 = **26 条**，与改前那个 915 行单文件的 path key 集合一字不差〔实测 `build_openapi_spec()` 与 `OPENAPI_SPEC` 都是 26〕。

**改前的毛病**：26 条说明混在一个大字典里。飞书容器想只发布自己那 2 条做不到，只能把 ToC 那 6 条一起发出去 —— 等于把桌面端的内部接口告诉飞书那边的对接方。

**为什么装订工必须在顶层**：`_openapi.py` 第 25–27 行同时 import 了三份章节。要是把它放进 `desktop/`，`desktop` 就 import 了 `feishu`，两条产品线立刻互相耦合 —— 正是本轮花七步消除的那种依赖。它必须站在两条线之上，那个位置就是骨架层。`_openapi_core.py` 同理：装的是两条线**共有**的 16 条，塞进哪个产品包都会让另一条反向依赖。`/oauth/*` 那 2 条本轮已随 `_oauth_manager` 从这里挪进 `FEISHU_PATHS`，所以公共章节由 18 条降到 16 条。

---

## 五、验证（怎么证明没坏）

### 5.1 全量测试

```
57 failed, 1481 passed, 7 skipped, 653 warnings in 350.67s   ← 含 toc 抽取后的最新一次
```

passed 由 1469 涨到 1481，多出的 12 条是 `toc` 进契约表后 3 个 workspace 遍历测试的参数化用例（`len(WORKSPACES)` 12 → 13）—— 从 junit XML 里数出来的：8 条 `test_compact_history_chaining` + 2 条 `test_compaction_prompt_injection` + 2 条 `test_workspace_hook_contract`。

那 57 条**不是回归**，是 Windows 上的既有失败（asyncio 子进程 `NotImplementedError`）。判据不是"失败数相同"而是**失败集逐条相同**：

```
基线 baseline-failures.txt : 57 行, md5 6af0fceab6945fb18c2f85d4efbf326b
本轮 junit-xml 提取        : 57 条
diff --strip-trailing-cr   : IDENTICAL  ← 参数化标记 [asyncio] 完整保留
```

### 5.2 路由表逐条核对

A7 把装配函数整体搬包，最大风险是漏挂路由。用同一份脚本跑改前（`3d687c37`）与改后两棵树，路由表**逐条字节相同**。当前实测：

```
desktop routes: 46    feishu routes: 5    合计 51
```

> 计数会随参数变化（`authm` 为 None 时少 11 条 `/auth/*`；`add_static` 一次注册产生多条 route 记录），所以"条数"本身是约定依赖的；**实质判据是逐条比对，两种参数组合下都相同**。

### 5.3 三个前端产物实测

| 前端 | 产物 | 挂载点 | 实测 |
|---|---|---|---|
| ToC v1 | 72 文件 / 11M | `/spa/` | index + JS + CSS 全 200 |
| ToC v2（默认） | 74 文件 / 7.3M | `/spa-v2/` | index + JS + CSS + PNG 全 200 |
| ToB 脚手架 | 2 文件 / 189K | `/feishu-web/` | index + JS 200，页面显示 `GET /defaults: HTTP 200` |

三份 `dist/` 均被 `.gitignore` 覆盖，**0 个产物被跟踪**，`git status` 无受跟踪改动。

### 5.4 三向同步

本轮同步更新 **8 份 AGENTS.md**：根、`gateway/`、`runtime/`、`session/`、`workspace/tob/`、`desktop/spa/`、`desktop/spa-v2/`、`feishu/feishu-web/`。

---

## 六、当前启动形态（团队常问）

**一个进程，两条线都贴。** 这是刻意设计，不是遗留问题。

```python
# gateway/__init__.py:246  骨架先起
app = await create_core_app(aim, sm, tm, rm=rm, ...)
# :257  ToC 贴纸
await register_desktop_routes(app, favicon_path=..., app_name=..., attention=..., authm=...)
# :264  ToB 贴纸
register_feishu_routes(app, feishu_ai_id=..., feishu_workspace_root=...)
```

生产上飞书容器起的也是同一个 `psi-agent gateway`（同容器内另起 `psi-agent channel feishu` 连过来），所以两面都必须贴，少贴哪面都是行为回归。

**本轮拆的是装配函数，不是进程入口** —— 收益在于"谁认识什么"现在写在函数签名里：`register_desktop_routes` 收 `favicon_path/app_name/attention/authm`，`register_feishu_routes` 收 `feishu_ai_id/feishu_workspace_root`，两者零交叉。真要一个纯 ToB 进程，代价是少调一行，不需要重构。

启动命令不变：

```
psi-agent gateway --listen http://127.0.0.1:8080 --browser
```

---

## 七、遗留与建议

### 7.1 `workspace/toc` 本轮已落地，Gateway 已能挂上，还差真跑一轮对话

**原来的遗留是**：`workspace/` 下只有 `tob` 一个包，它**一身两职** —— 既是 ToC 桌面端出厂的能力包（安装器 `haitun.iss:79` 打的就是它），又是 ToB 飞书机器人的 workspace。实测其构成：

| 项 | 数量 |
|---|---|
| tools 总数 | 140 |
| 其中 `feishu_*` | 28 |
| 其中 `assignment_*`（ToB 派单） | 8 |
| 其中 `handbook_*` / `haibao_*` | 3 |
| 通用 | 101 |
| skills 总数 | 145 |
| 名字含 feishu/飞书 | 30 |

**这不是命名问题，是 ToC 用户在为 ToB 业务付提示词成本** —— 装机用户拿到的包里含 28 个飞书 API 工具、8 个内部派单工具、30 个飞书技能。

**本轮已做**（提交 `80b54129`）：**从现有包减出 ToC**，不按原方案"以 `openclaw-style` 为基线新建"—— 实测该基线只有 5 个 tools、1 个 skill，从它起步等于把 ToC 出厂能力砍到近零，是回退不是演进。落地数字与判定方法见 **2.5**：266 文件、85 工具文件注册 93 个工具、102 个 skill、hook 6/6、提示词点名不存在的工具 0 个。

两件相关事实：`.iss` 现在打的仍是 `workspace\tob`，抽 `toc` 没改它一行，安装器该打哪个包属 **9.1**；共享层已查清 —— `ToolRegistry.load(cls, tools_dir: Path, session_id: str = "")` 只收**一个** `tools_dir`，不改内核就没有多根目录，`toc` 与 `tob` 之间的重复无法避免，列为 **9.4**。

**已经能挂上**：Gateway 起在 8080，启动日志打出 `Default agent: F:\code\psi-agent\workspace\toc`。能不能接与用哪种启动姿势无关 —— 接是已经通的，姿势才是 9.2 要定的。

**还差最后一步：没在 toc 上真跑过一轮对话。** 已验到的是内核能加载它（6 个 hook 全解析、93 工具、102 skill）、提示词组装正确（点名不存在的工具 0 个）、Gateway 认它作 default agent。**没验到的是发一条真消息走完一轮** —— 工具真调起来、skill 真读进去、回复真产出。这一步要人在界面上点，我没做。

### 7.2 其他已知未修（均非本轮引入）

- **命名管道跨文件污染**：`tests/integration/test_gateway.py` 与 `tests/psi_agent/gateway/test_feishu_manager.py` 共用硬编码前缀 `gw-test`，全量跑时前者留下同名管道，后者绑定被 `[WinError 5]` 拒掉。表现为 `test_route_*` 里随机某条失败，单跑该文件 27/27 全绿。该前缀在改动前逐字相同。**与 9.2 里双进程那条是同一失败模式** —— 一个完整管道名有多个持有者，只是这里撞在前缀段、那里撞在 sid 段。
- **`{app}\app` 升级时整目录换新**：用户数据（`SOUL.md`/`USER.md`/`schedules`）会被遗弃在 `{app}\app.backup`。本轮既不改善也不恶化；修法属 B4，已推后。
- **运行时产物未 gitignore**：`workspace/tob/` 下的 `charts/`（252 个图表 PNG）、`channel_events/`、`.psi/` 无 gitignore 条目。虽从未被跟踪，但已被 Kanban 内部 checkpoint ref 全树暂存扫进去过 —— 证明风险真实存在。建议补 gitignore。

### 7.3 review 提出的改动（本轮已处理）

- **`_oauth_manager.py` 搬进 `feishu/`** —— 已做，`456009d3`，判据与实测见 4.1。骨架层 7 → 6 个 `.py`。
- **撤回 B3 的安装器分包** —— 已做，`b11cda40`，`Source:` 由 14 条回到 11 条。理由是单独列 `Source:` 并不改变 `SwapComponent('app')` 的整目录换新行为，等于只把清单写长 3 行、没换来保护。出厂内容与用户数据怎么分是设计题，定边界后单独开 PR，见 **9.1**。
- **抽出 `workspace/toc`** —— 已做，`80b54129`，见 2.5 与 7.1。

### 7.4 已推后（负责人决定）

- **B4** 升级保数据、**T1** 真装真升实验 —— 属打包部署，落点取决于 workspace 结构。

---

## 八、我明确没验到的

如实交代，不含糊：

| 项 | 状态 |
|---|---|
| `feishu-web` 的 `npm run dev` 独立 dev server（vite proxy 路径） | **未跑**。只验了构建产物经 gateway 静态挂载的路径 |
| 启动日志里 9 个 `ModuleNotFoundError`（`_feishu_impl` / `_assignment_tool_common` 等） | **未深查**。是工具首次加载的相对 import 失败，随后 refresh 全部 `added` 补回，与本轮改动无关 |
| `tool_registry` 是否支持多 tools 根目录 | **已查（本轮）**。`ToolRegistry.load(cls, tools_dir, session_id="")` 只收一个根，不改内核就没有共享层，见 7.1 ③ 与 9.4 |
| Gateway 挂载 `workspace/toc` | **已验（负责人实操）**。启动日志 `Default agent: F:\code\psi-agent\workspace\toc` |
| 在 `toc` 上真跑一轮对话 | **未跑**。工具真调起、skill 真读入、回复真产出这一段没验，要人在界面上点 |
| `toc` 那 93 个工具逐个调用 | **未验**。判据是能注册、依赖闭包完整，不是运行时行为 |
| `toc` 那 102 个 skill 的正文 | **未逐篇读**。只核了不点名本包不存在的工具 |
| ToC 用户实际用不到那 36 个 ToB 工具的比例 | **未查**。只按前缀数了名字，无调用数据 |
| 139 万字符 SKILL.md 里多少真进了提示词 | **未查**。skills 按需读取，启动只加载索引 |
| Linux / macOS 上的测试表现 | **未跑**。仅 Windows 实测 |

---

## 九、需要讨论的细节

### 9.0 讨论结论（2026-08-29 已过会，结论先行）

**7 项里有 5 项已拍定，2 项仍开放。** 已定的 5 项都已落成 Kanban 任务，任务号见下表。下面各小节保留原始的问题陈述、实测事实与可选项，**并在标题后标注最终结论** —— 保留是为了让后来人看得到当时为什么这么选，不是还没定。

| # | 题目 | 结论 | 落到哪 |
|---|---|---|---|
| 9.1 + 9.3 + 9.6 | 用户数据落点、`SOUL.md`/`USER.md` 归属、ToC 用户隔离 | **方向已定：用户数据移出 `{app}`，落到 AppData 下一个 `.haitun` 目录。** 三题合并成一个方向，具体落点与实施**推后**（不属本轮） | `3a79a`、`33fe6`（均已推后，不要在本轮启动） |
| 9.2 | 开发时一进程还是两进程 | **已定：加启动参数分开选**（不是二选一的习惯问题，是补一个参数）。默认值保持现状（两条线都挂） | `88bd2` |
| 9.4 | `toc`/`tob` 的重复成本 | **已定：短期接受重复（选项 A），记为欠账不是终局。** 不在本轮动内核 `load()` 签名 | 无任务，欠账记录在本节 |
| 9.7 | 怎么合并回主干 | **已定：一次性合并（选项 A），改名先落再合并，且必须在远程仓库冻结期内做** | `848e4`（改名，进行中）→ `69225`（合并 main） |
| 9.5 | 桌面版要不要长期记忆 | **仍开放。** 依赖 ToC 身份体系，不是本轮范围 | 无 |

**另有一项本章原本没列、会上追加定下的**：顶层 `workspace/` 改名为 `agents/`，其下 `tob`/`toc` 改名为 `feishu`/`desktop`（与 `gateway/` 的子包命名对齐）。任务 `848e4`，已在跑。**所以本报告里所有 `workspace/tob`、`workspace/toc` 字面路径都是改名前的坐标**，读的时候按 `agents/feishu`、`agents/desktop` 换算。

---

**以下是过会前的原始材料。** 共 7 项，每项都是**动手前必须先定边界**的设计题 —— 边界没定就写代码，会像 B3 那样做完再撤回。每项给的是：问题是什么、实测到的事实、可选项与代价、我的倾向。**倾向仅供参考，请负责人裁决。**

| # | 题目 | 为什么必须先讨论 | 卡住谁 |
|---|---|---|---|
| 9.1 | ToC 出厂内容与用户数据混住 | 牵动升级保数据语义，改错会丢用户数据 | B4、T1、安装器 |
| 9.2 | ToC / ToB 开发启动方式：一进程还是两进程 | 决定 `workspace/toc` 怎么接上去 | 7.1 的收尾 |
| 9.3 | `SOUL.md` / `USER.md` 归谁 | 它既是出厂模板又是用户数据，两种身份互斥 | 9.1 的前置 |
| 9.4 | `toc` 与 `tob` 的重复成本 | 内核只收一个 `tools_dir`，重复是结构性的 | 长期维护 |
| 9.5 | 桌面版要不要长期记忆 | 现有实现认飞书身份，桌面版得另设一套 | ToC 产品能力 |
| 9.6 | ToC 的用户隔离靠什么 | 现有隔离认飞书 id，桌面版单机多用户没有对应物 | 9.1、9.5 的共同前置 |
| 9.7 | 这次重排怎么合并、怎么推给全团队 | 改动跨 18 次提交，动了 13 个 workspace 的加载路径 | 全团队日常开发 |

### 9.1 ToC 出厂内容与用户数据混在一起 —— 已定方向 A，实施推后

> **结论**：走**选项 A**，用户数据移出 `{app}`，落到 AppData 下一个 `.haitun` 目录。9.1 / 9.3 / 9.6 三题**收敛成同一个方向**，不再各自决策。
>
> **但具体落点与实施都推后**，不属本轮架构演进。推后的理由：本轮只做开发架构（包内/包外分类、根目录约定），装到哪、升级怎么保住、存量怎么迁、卸载删不删都属于打包部署，与「不做部署」这条边界同族；而且正确落点取决于 ToC/ToB workspace 的最终结构，结构没定就动它，落完还得再改一遍。任务 `3a79a`，**不要在本轮启动**。
>
> **落点还要比 `{app}\userdata` 再往外一层**：`[UninstallDelete]`（`haitun.iss:88-90`）删 `{app}\*` 和 `{app}`，挪到 `{app}\userdata` 在卸载重装时仍会丢。真正的归宿在 `{app}` 之外 —— 这也是为什么方向定在 AppData。`rollback-state.json`（`:77`）能活下来就是这条的先例：它在 `{app}\` 而不在 `{app}\app`，与 `onlyifdoesntexist` 无关。
>
> **另两件超出架构范围、需单独决定的**：存量用户的数据迁移（改了读取位置后，数据仍在 `{app}\app` 的用户会看起来丢东西，除非写迁移。负责人称「还没人真的在用这个能力」，但已装机用户的实际情况需要核实）；卸载语义（卸载该不该删掉用户养出来的海豚 —— 这是产品甚至法务决定）。

**问题**：安装器 `haitun.iss` 用一条通配 `Source: workspace\tob\*` 整目录拷贝，出厂内容（`systems/` `tools/` `skills/` 与几份提示词模板）和用户数据（`SOUL.md` `USER.md` `schedules/`）落在同一个 `{app}\app` 下，结构上分不出来。

**实测事实**：`{app}\app` 挂的是 `[Code]` 段的 `SwapComponent('app')`，升级时**整目录换新**。B3 试过把三项摘成独立 `Source:`（11 → 14 条），已撤回 —— 因为 `Flags` 不变的话，单独列出来的三项照样被换掉，**清单长了 3 行，保护一点没多**。

**可选项**：

- **A. 用户数据搬出 `{app}`**，放 `{localappdata}` 或 `{userappdata}`，出厂目录保持整目录可换新。代价：workspace 根目录被拆成两个物理位置，`agent_path` 那套推导要能同时认两个根。
- **B. 留在 `{app}` 内，靠 `Flags: onlyifdoesntexist` + 升级前备份还原**。代价：用户改过的**出厂**文件（比如自己调了 `AGENTS.md`）在升级时是保还是覆盖，得逐文件定策略，容易漏。
- **C. 出厂内容与用户数据分成两个 component**，各自换新策略。代价：Inno 的 component 语义要摸清，且不解决"用户改过出厂文件"这一类。

**倾向 A。** 理由：它是唯一让"出厂目录可以无脑整体换新"成立的选项 —— 这条性质一旦成立，升级逻辑就不必逐文件判断，B / C 都要为每个文件回答"这次要不要覆盖"。代价是路径推导要认两个根，但那是一次性的结构成本，而逐文件策略是每加一个文件都要再想一遍的长期成本。

**注意**：定下来之前 **B4（升级保数据）和 T1（真装真升实验）都动不了**，因为它们改的就是这一处。

### 9.2 ToC / ToB 开发时怎么启动：一个进程还是两个 —— 已定：加启动参数分开选

> **结论**：**加启动参数，让开发时能自己选 gateway 与 agent 的组合。** 本节原先把这题写成「A 还是 B 的习惯问题，机制两边都不缺」，**这个判断被否了** —— 真正缺的是一个参数：用飞书 gateway + 飞书 agent 启动时应当**只**挂飞书前端与飞书 agent，而现在 `gateway/__init__.py` 的 257 与 264 行**无条件挂载两条产品线**，起飞书必然顺带把 ToC 前端也起起来。
>
> **默认值必须保持现状**（两条线都挂），否则云上 `launch-gateway.sh` 会静默少挂一条 —— 改默认值等于改生产行为。
>
> 参数粒度倾向枚举（`--gateway feishu|desktop|both`）而非两个独立布尔，因为布尔组合会出现「都关」这种无意义状态。agent 侧接到现有 `--default-agent`/`_defaults.py` 那套上，**不另造一套**。
>
> 一处连带影响：`/oauth/callback`、`/oauth/code` 现挂在 `feishu/_routes.py`，注释明说「ToC 进程照样有这两条 —— 唯一的生产入口两条线都贴，所以行为不变」。**改成可选之后这句注释的前提就不成立了**，要么改注释，要么保证 ToC 单独起时这两条仍在。另有 `desktop/_routes.py` 里 `/` 的 spa-v2→spa 降级链，关掉 ToC 后 `/` 该给谁要明确定。
>
> 任务 `88bd2`，无依赖，可与其他任务并行。

**问题**：现在 `workspace/toc` 和 `workspace/tob` 两个包都在库里了，但开发时怎么起、起几个，没定。

**实测事实**：`workspace/tob` 不是写死的目标，只是 explicit 为空时的仓库内候选（`_defaults.py:66`）。解析链是

```
Gateway.default_agent (__init__.py:79, 真参数)
  → resolve_default_agent(explicit)         _defaults.py:99
  → resolve_agent_package(explicit, repo_candidate="workspace/tob")
      1. explicit 非空 → 就用它                ← 传 workspace/toc 这里就中了
      2. 否则 cwd/workspace/tob 是目录 → 用它    ← “写死”其实只是这一层的候选
      3. 否则 cwd 自带 tools/+skills/ → 用 cwd   ← 装机形态
      4. 否则 "" → agent ≡ workspace
```

**所以 `toc` 今天就能选中，传 `default_agent` 指过去即可，不需要改任何代码。**

三条路径实测（直接调 `resolve_default_agent`）：

```
explicit 空(靠常量)      : F:\code\psi-agent\workspace\toc
explicit=workspace/tob   : F:\code\psi-agent\workspace\tob
explicit=workspace/toc   : F:\code\psi-agent\workspace\toc
```

`--default-agent` 是现成的 CLI flag（`psi-agent gateway --help` 里能看到），所以切产品线是：

```bash
psi-agent gateway --default-agent workspace/toc    # ToC
psi-agent gateway --default-agent workspace/tob    # ToB
```

实操已验证 `toc` 能挂上：启动日志打出 `Default agent: F:\code\psi-agent\workspace\toc`。

> **不要改 `DEFAULT_AGENT_REPO_CANDIDATE` 来切产品线**，三条理由：
>
> 1. `--default-agent` 已经存在，不必改代码，两条线还能同时起。
> 2. **改常量会挂 2 个测试**（`test_resolve_default_agent_soft_haitun_workspace`、`test_resolve_default_agent_repo_layout_wins_over_cwd_tools`），因为 `test_defaults.py:76` 把 `workspace/tob` 写死了。控制实验：`git stash` 掉该行 **13 passed**，恢复后 **2 failed**。这 2 条会混在既有 57 条 Windows 基线失败里，不容易发现。
> 3. **这个常量是发布期决策**，`_defaults.py:14` 写明它是品牌字面量的唯一落点。拿它当开发开关，谁忘了改回来装机包默认就变了。
>
> `agent 包` 与 `workspace` 是两件事：`--default-agent` 换能力包，`--default-workspace` 换用户工作区。切产品线只需换前者。

**可选项**：

- **A. 一个进程，启动时传 `default_agent` 选包**。代价：**零改动，机制已在**。缺点是同一时刻只能是一条线，两条线的路由都注册着但只有一个 workspace 在跑，容易让人误判"两条线都活着"。
- **B. 两个进程各占一个端口**，各自带一个包。代价：两个终端两个端口、前端 dev proxy 要指对，**且必须给第二个进程换一个 `--socket-path`**（下面单列）；但两条线真正独立，最接近生产形态（ToC 是装机的、ToB 是服务器上的，本来就是两个进程）。
- **C. 一个进程同时挂两个 workspace**。**这条按当前内核不成立** —— 见 9.4，`ToolRegistry.load()` 只收一个 `tools_dir`，要做得改内核。

**B 的隐藏前提：第二个进程要换 `--socket-path`。** 实测两个 gateway 同 `socket_path=psi`、不同端口，会在建 Session 时报 `[WinError 5] 拒绝访问`，Session 起不来：

```
ERROR serve_session: Failed to start session server on
      \\.\pipe\psi\channels\<sid>: [WinError 5] 拒绝访问。
ERROR Session '<sid>' crashed: PermissionError(13, '拒绝访问。', None, 5, None)
```

冲突的是**完整管道名**，不是前缀 —— 同前缀不同 sid 的两条管道可以并存（实测两个进程各建一个 Session，零报错）。撞名的来源是 `_scheduler_manager.py:69`：调度 Session 的 id 由 workspace 路径 sha256 派生，两个进程只要 `--default-workspace` 指向同一目录就算出同一个 id，而 `_session_manager.py:131` 的去重只管进程内。

修法两条，实测都成立：给第二个进程换 `--socket-path psi-tob`，或让两个进程用不同的 `--default-workspace`。另外 `--listen` 必须带 scheme，`--listen 127.0.0.1:18081` 会掉进 `_sockets.py:94` 的 Unix socket 分支在 Windows 上直接抛错。

**倾向 B，但它和 A 不是二选一。** 现实路径是**平时用 A**（`--default-agent` 指包，零改动），**验证与联调用 B**（两个进程，跟生产同形）。

~~**所以 9.2 要定的只剩一句**：本地开发的默认姿势是 A 还是 B。机制两边都不缺，是习惯问题，定了写进 `AGENTS.md` 即可。~~

**上面这句已被否**（见本节开头的结论）。机制并非两边都不缺：A 能选 agent 包但**不能只挂一条产品线的路由与前端**，这一层现在是无条件的。所以结论不是「选个习惯」，而是**补一个参数**。

一个进程把两条线路由都注册着是**当前的正确行为**，不必讨论 —— `__init__.py:240–244` 的注释写明 `psi-agent gateway` 是唯一入口，生产上飞书容器起的也是它，少贴哪一面都是行为回归。A6/A7 的收益是让"只贴一面"变得可能（ToB 容器、测试用），而不是让默认进程只贴一面。

### 9.3 `SOUL.md` / `USER.md` 到底归谁 —— 并入 9.1 的方向，实施推后

> **结论**：不单独决策，**并入 9.1 的方向**（用户数据移出 `{app}`，落到 AppData 下的 `.haitun`）。「模板与实例分离」的倾向仍然是那个方向下的正确做法，但**实施随 9.1 一并推后**。
>
> 一条已被推翻的路线要留在这里，免得后来人重走：**「逐条加 `onlyifdoesntexist`」无效。** 该标志只防「文件还在原地时被覆盖」；`SwapComponent` 改名之后原地没有文件，被保护的文件照样重新写入。**这是改名问题，不是覆盖问题** —— B3 试过摘独立 `Source:`（11 → 14 条）已撤回，就是撞在这里。

**问题**：这两个文件**同时**是出厂模板和用户数据。安装器要放一份初始版本进去（否则新装用户没有），可它们又会被 agent 自己改写、被用户积累内容 —— 一旦改过，就不能再当出厂文件覆盖。

**为什么单列**：9.1 的三个选项都绕不开它。A 要求把用户数据搬出 `{app}`，那"初始版本"是谁在什么时候放进去的（安装器？首次启动时生成？）；B 的 `onlyifdoesntexist` 正好为这种文件设计，但它意味着**出厂模板一旦发布就再也改不动了** —— 老用户永远拿不到新版模板。

**可选项**：**模板与实例分离** —— 出厂只发 `SOUL.template.md`，首次启动时拷成 `SOUL.md`，之后模板照常随版本更新，实例归用户。代价是多一层拷贝逻辑和一次"模板变了要不要提示用户"的产品决策。

**倾向：模板与实例分离。** 这是唯一能让"出厂模板可持续更新"和"用户内容不被覆盖"同时成立的做法，别的选项都得牺牲一头。但它引入的"模板更新了怎么告知用户"是产品问题，不是工程问题，得产品一起定。

### 9.4 `toc` 与 `tob` 的重复成本是结构性的 —— 已定：短期接受重复，记为欠账

> **结论**：**短期按选项 A 走（接受重复）**，本轮不动内核 `ToolRegistry.load()` 的签名。**这是欠账，不是终局** —— 终局仍是选项 B（`load()` 收多个根，做成 `_shared` + 各自 `tools/` 两层）。
>
> 现在不动的理由：先让 `desktop`（原 `toc`）在 9.2 定下的参数形态下真跑起来，攒够「哪些文件真的需要共享」的实测，再动内核签名。现在动等于凭猜设计同名工具的覆盖优先级。
>
> **接受重复的代价要明说**：85 个工具文件、整个 `systems/`、102 个 skill 是拷贝，`feishu` 侧改一处通用工具的 bug，`desktop` 不会跟着变。靠约定和 review 守，漏一份就是**静默的行为分叉**。本节没有对应的 Kanban 任务 —— 这条欠账就记录在这里。

**问题**：`workspace/toc` 抽出来了，但它和 `tob` 之间**没有共享层**，85 个工具文件、整个 `systems/`、102 个 skill 都是**拷贝**。tob 那边改一处通用工具的 bug，toc 不会跟着变。

**实测事实**：内核的签名是

```python
ToolRegistry.load(cls, tools_dir: Path, session_id: str = "")
```

只收**一个** `tools_dir`。**不改内核就没有多根目录**，符号链接 / 构建期拷贝那些办法都是在绕这个签名。

**可选项**：

- **A. 接受重复**，靠约定和 review 保持同步。代价：通用工具的修改要手工同步两份，漏一份就是静默的行为分叉。
- **B. 改内核让 `load()` 收多个根**（`tools_dirs: Sequence[Path]`），做成 `_shared` + 各自 `tools/` 两层。代价：动内核公共 API，12 个 workspace 的加载路径都受影响；且同名工具的覆盖优先级要定义清楚。
- **C. 构建期拼包** —— 库里存 `_shared` 与差异部分，打包/启动时合成完整目录。代价：库里的目录不再是运行时的目录，调试时看到的和跑的不是一回事。

**倾向 B，但不是现在。** 理由：这是唯一从结构上消掉问题的选项（A 是靠人守纪律，C 是把问题挪到构建期）。不是现在的理由是：先让 `toc` 在 9.2 定的形态下真跑起来，攒够"哪些文件真的需要共享"的实测，再动内核签名 —— 现在动等于凭猜设计覆盖优先级。**短期先按 A 走，但要明确记下这是欠账，不是终局。**

### 9.5 桌面版要不要长期记忆 —— 仍开放

> **仍未定。** 这题依赖 ToC 的身份体系（见 9.6 的选项 C），而那一层本身依赖一个尚未核过的前提：登录返回里有没有稳定账号 id 可作路径段。不是本轮范围，无对应任务。本节的价值是讲清「为什么 `desktop` 包里没有记忆工具」不是抽包时漏了。

**问题**：`memory_*` 那 5 个工具（跨会话长期记忆）没进 `workspace/toc`。

**实测事实**：这条链是硬的 ——

```
memory_*  →  _fusion_memory_mcp.py:56       _load_sibling_module("_fusion_memory_membership")
          →  _fusion_memory_membership.py:14  from _feishu_impl import list_chat_members_impl
          →  _feishu_impl.py
```

「谁的身份在写这条记忆」认的是飞书 `open_id`。桌面版没有飞书身份，整条链落不了地。所以这不是"少拷了几个文件"，是**桌面版缺一套身份**。

**可选项**：

- **A. 桌面版不做长期记忆**，只有会话内上下文。代价：能力上明显弱于 ToB 版。
- **B. 设一套本地身份**（ToC 登录已有手机号 + 验证码，可以拿它当身份锚点），记忆写本地。代价：要新写一套存储与检索，不是把飞书那条链搬过来。
- **C. 桌面版记忆走云端**，用 ToC 账号体系认身份。代价：涉及数据出本机，是合规与产品决策，不只是工程。

**倾向 B。** 理由：ToC 已经有手机号 + 验证码的登录，身份锚点是现成的，不必新造；写本地也避开了 C 的合规问题。但这是个新增能力，不是本轮范围 —— 这里只负责把"为什么 toc 没有记忆工具"讲清楚，别让人以为是抽包时漏了。

### 9.6 ToC 的用户隔离靠什么 —— 并入 9.1 的方向（B 打底），C 仍开放

> **结论**：**选项 B 并入 9.1 的方向** —— 用户数据落到 AppData 下的 `.haitun`，跨 OS 账号这一层天然就隔开了，与 9.1 是同一个动作，不额外花成本。
>
> **选项 C（按 ToC 登录身份隔离）仍开放**，因为它依赖一个我**没有核过**的前提：`_auth_manager.py` 现在只管拿 token，不产出可作路径段的稳定 id，得先确认云端返回里有没有这样一个字段。**这条要先查再定。**
>
> 所以 9.1 / 9.3 / 9.6 收敛成一句话：**用户数据搬到 AppData 的 `.haitun` 下**，一个动作同时解决升级保数据、模板与实例分离的落点、跨 OS 账号隔离三件事。同一个方向覆盖三题，这是选它的主要理由。

**问题**：ToB 的多用户隔离是完整的，ToC 的没有对应物。桌面版是单机单进程，一台机器上如果有多个使用者（家庭共用、同一台办公机多人登录），谁的会话、文件、记忆归谁，现在没有任何边界。

**实测事实**：ToB 侧隔离全部锚在飞书 id 上，三层都是（`gateway/feishu/_feishu_manager.py`）：

```
session id   私聊 feishu-<open_id>        群聊 feishu-chat-<chat_id>
workspace    <root>/<open_id>            <root>/chat-<chat_id>
管道名        psi\channels\<session id>
```

`_sanitize_open_id` 还专门把私聊 id 里的 `-` 转义，防止某人 open_id 恰为 `chat-oc_x` 时与群 `oc_x` 撞成同一个 session —— 注释直接写明那是"陌生人共享上下文的隐私事故"。**桌面版没有 `open_id`，这三层锚点同时失效**：所有会话落在同一个 `--default-workspace`（当前是 `{Desktop}/haitun交付`），共用同一份 `state/`、`histories/`、`todos/`。

**可选项**：

- **A. 不做隔离，一台机器一个使用者**。代价：等于把"别人别用我电脑"写成产品前提；同机多人时后来者能读到前者全部会话与文件，是隐私问题而非体验问题。
- **B. 靠操作系统账号隔离** —— workspace 与 appdata 都落在 `{localappdata}`，Windows 账号天然分开。代价：同一 OS 账号下多人共用（很常见）仍然不隔离；且要求 9.1 选 A（用户数据搬出 `{app}`），两题绑在一起。
- **C. 靠 ToC 登录身份隔离** —— 用已登录账号（手机号/邮箱换到的账号 id）作 workspace 与 session id 前缀，切换账号即切换数据。代价：要定义"未登录时写哪里"以及登录后怎么归档；`_auth_manager.py` 现在只管拿 token，不产出可作路径段的稳定 id，得先确认云端返回里有没有这样一个字段。

**倾向 B 打底、C 叠加。** B 是几乎零成本就能拿到的一层（跟 9.1 的 A 同一个动作），先把跨 OS 账号这条守住；C 才是真正对齐"谁在用"的那层，但它依赖一个我**没有核过**的前提：登录返回里是否有稳定账号 id 可作路径段。**这条要先查再定。**

**注意**：这题是 9.1 与 9.5 的共同前置 —— 9.1 要决定用户数据放哪，9.5 要决定记忆按谁归档，两者都得先知道"用户"在 ToC 里怎么标识。

### 9.7 这次重排怎么合并、怎么推给全团队 —— 已定：一次性合并，改名先落，冻结期内做

> **结论**：**走选项 A（一次性合并主干）**，且顺序是**先落改名（`848e4`）、再合并 main（`69225`）**，合并**必须在远程仓库冻结期内执行**，冻结通知由负责人发出。
>
> **合并面已于 2026-08-29 重测，下面正文里的旧数字作废，以这组为准**：
>
> | 项 | 实测值 |
> |---|---|
> | merge-base | `64b6273bbc75504e0a0951f3330f436c9a2bd60a` |
> | main 领先 | **15** commits |
> | 本分支领先 | **20** commits |
> | main 动的文件 | **63** |
> | 本分支动的文件 | **1063** |
> | **重叠文件** | **仅 9 个** |
> | `merge-tree` 实测冲突 | **8 个**（1 内容 + 7 file-location） |
>
> 9 个重叠文件：`.github/inno-setup/haitun.iss`、`.github/inno-setup/oss-publish.md`、`.github/workflows/ci.yml`、`.github/workflows/pyinstaller.yml`、`AGENTS.md`、`pyproject.toml`、`src/psi_agent/channel/feishu/__init__.py`、`src/psi_agent/session/AGENTS.md`、`tests/psi_agent/gateway/test_auth_manager.py`。
>
> **唯一的内容冲突**是 `tests/psi_agent/gateway/test_auth_manager.py`，而且它是**新出现的** —— 上一次量的时候只有 file-location 冲突，main 又推进了 1 个 commit 就多出一个内容冲突。**所以开工前必须重量一遍，别把这里的数字当最终值。**
>
> 7 个 `CONFLICT (file location)` 全部同源：main 在 `examples/haitun-workspace/` 里**新增**了文件，而本分支把这个目录搬走了。清单：`tests/test_feishu_leave_query.py`、`tests/test_feishu_sheet_find_columns.py`、`tests/test_feishu_sheet_grid_range.py`、`tests/test_feishu_sheet_truncation.py`、`tests/test_todo_completion_standard.py`、`tools/_feishu/leave.py`、`tools/feishu_leave_query.py`。main 在这个目录里共 **23 文件 / 2997 insertions**，这批改动是合并的主要工作量。
>
> **那 7 个文件必须逐个判断落点，不能照 git 的建议批量搬。** git 建议全搬到 `workspace/tob`（改名后 `agents/feishu`），但**至少 1 条是错的**：`feishu_leave_query.py` 的 docstring 引用 `GET /open-apis/approval/v4/instances`，是飞书专属工具，git 之所以建议它去 desktop 侧是因为 desktop 包里飞书工具数为 0，纯统计错觉。逐个看 docstring 与 import 定落点。
>
> **改名优先这个顺序的已知代价**：会产生 **1 个错建议 / 7 个**；先合并后改名是 0 个错建议。负责人已选改名优先，所以这 7 个由人判断。
>
> **git 的 rename 检测没问题** —— 实测 684 个 rename 全部识别（R100/R097），即便顶层与子目录两次叠加改名也如此，不必担心合并时丢历史。
>
> **最容易静默失败的一处**：那 7 个文件里**没有 `systems/system.py`**。三个测试文件用 glob `Path('agents').glob('*/systems/system.py')` 参数化，所以 **workspace 数会稳定停在 13，不会因为漏搬而变化** —— 测试数量看不出这 7 个文件有没有搬对。正确判据是逐文件核对 `examples/haitun-workspace/` 的 23 个改动各自落到哪，不是看测试数。

**问题**：这次重排是 18 次提交、跨 gateway/runtime/session/workspace 四层的结构性改动，目前只在 `refactor/gateway-workspace-evolution` 一条分支上。怎么并回主干、并回去之后团队里在跑的分支和各自的 workspace 怎么跟上，没定。

**实测事实**：

- **改动面**：`gateway` 骨架层 `.py` 由 12 降到 6，10 个 manager 1740 行移出 gateway 到 `runtime/`，`haitun-workspace` 从 `examples/` 迁到 `workspace/tob`（60 处引用清零），另抽出 `workspace/toc`（266 文件）。**任何在途分支只要 import 过这些路径就会冲突。**
- **workspace 数量**：内核加载路径受影响的 workspace 现有 **13 个**（`examples/` 11 个 + `workspace/tob`、`workspace/toc`，即三个测试文件 `WORKSPACES` glob 的口径），不止 `toc`/`tob` 两个。B5 当时记的 12 是 `toc` 抽出之前的数。
- **测试基线**：Windows 上全量有 57 条既有失败（asyncio 子进程 `NotImplementedError` 等，非本轮引入），且在 57–62 间浮动。**这意味着"合并后跑一遍全绿"不是可用的验收判据**，只能比对失败集合是否与合并前逐条相同 —— 本轮 A7 就是这么验的（`diff` 结果 `IDENTICAL`）。

**可选项**：

- **A. 一次性合并主干**，团队各自 rebase。代价：所有在途分支同一天集中解冲突，冲突集中在 import 路径这类机械改动上，但量大；好处是只痛一次，之后不必维护双形态。
- **B. 分批合并**（先 runtime 抽离、再 workspace 迁移、最后 toc）。代价：中间态存在多次，每次都要各自 rebase 一遍，累计痛感更大；且中间态的三向同步文档要写几份。
- **C. 保留兼容层**（旧 import 路径转发到新位置，给一个过渡期）。代价：兼容层本身是要删的代码，且过渡期内两套路径并存，新人不知道该用哪个 —— 与本轮"消除反向依赖"的目标相反。

**倾向 A。** 理由：这次改动的冲突绝大多数是 import 路径的机械替换，不是语义冲突，集中解一次的实际成本低于分三次；C 引入的兼容层与本轮目标直接冲突，B 的中间态会让三向同步文档写几份又废几份。

**合并要满足的前置**（这几条是判据，不是建议）：

1. **失败集合逐条比对**，不是看全绿 —— 合并前后的失败集合必须 `IDENTICAL`。
2. **13 个 workspace 的加载路径各跑一次**，不只 `toc`/`tob`。
3. **7.1 那一轮真实对话补上** —— 现在 `toc` 只验到"能加载"，没验到"能跑"。

**推给团队要交付的**：`AGENTS.md` 里写清 9.2 定下的启动姿势（`--default-agent` 怎么用、要不要带 `--socket-path`）、新的目录归属（谁该往 `runtime/` 放、谁往产品包放）。**这一条是三向同步的要求，不是可选项** —— 结构变了而 `AGENTS.md` 没变，下一个人还会往骨架层加文件。

### 9.8 顶层目录改名（会上追加决定）

> **结论**：顶层 `workspace/` 改名 `agents/`，其下 `tob`/`toc` 改名 `feishu`/`desktop`，与 `gateway/` 的子包命名对齐。任务 `848e4`，**改名先落、合并 main（`69225`）后跑**。

负责人明确担心「改错改混」，所以这项的重点不是动手快，是**判据先立住**。已量出的规模与陷阱：

- `workspace/tob` + `workspace/toc` 字面路径全库 **306 处**，排除 `docs/` 后 **73 处**，分布在 **31 个文件**。核心一处是 `gateway/_defaults.py:66` 的 `DEFAULT_AGENT_REPO_CANDIDATE = "workspace/tob"`（同文件 docstring 14/25/103 行也提到）。
- **陷阱一：`toc` 在 ToC 前端里是「目录」（table of contents）** —— `desktop/spa-v2/public/legal.css` 有 6 处 `.toc` CSS 类，`privacy.html` 1 处。改了会破坏隐私政策页排版。
- **陷阱二：`ToC`/`ToB` 是产品线称谓，不是路径** —— `src/` 下 **102 处**，必须原样保留。
- **量化后果**：`src/` 下不区分大小写搜 `toc|tob` 命中 **624 处**，其中真正是路径的只有 **21 处**。天真的 `sed s/toc/desktop/g` 会破坏 603 处。**禁止全局 sed，逐处判断。**
- **必须保持不变的**：9 处 REST 路由字面量 `"/workspace/..."`（`cwd`/`places`/`browse`/`file`/`reveal` —— 这是 HTTP 接口，改了前端全 404）、19 处 `default_workspace`、11 处 `workspace_root`、`--default-workspace` flag、`SessionManager` 里的 `workspace=` 形参名。
- **最容易静默失败的一处**：三个测试文件（`test_compaction_prompt_injection.py:26`、`test_compact_history_chaining.py:19`、`test_workspace_hook_contract.py:32`）用 glob `Path('workspace').glob('*/systems/system.py')` 参数化。改名后这个 glob **静默变成 0 命中，测试不报错、只是数量变少**。必须同步改成 `agents` 并确认参数化后仍是 13 个。

---

## 附录：提交序列

```
3fa34a4c merge(gw-ws): a 线 A6/A7 增量集成, 零冲突, 骨架反向 import 由 7 归零
048efdd9 refactor(gateway): A7 两个装配函数搬进产品包, 骨架反向 import 7 行归零, 117 条路由逐条不变
3d687c37 feat(gateway): A6 ToB 前端脚手架 9 个源文件落地, 后端只多 1 个 add_static, S1-S6 全过
24c54bcf docs(tob): code-explainer 技能里 15 处过期坐标改成实测值
b63747da chore(gw-ws): 合并后补三向同步 4 处与 ruff format 2 文件
52e755d3 merge(gw-ws): a 线 A1-A5 与 b 线 B1/B2/B6/B3/B5 集成, 3 处冲突手工消解
3d102482 refactor(gateway): A5 12 个产品模块 + 2 棵 SPA 落位到 desktop/ 与 feishu/
5555475e test(session): B5 hook 契约钉成 12x6 表, 实测 11/12 只暴露 2-3 个而非 6 个
69b19cc8 refactor(installer): B3 ToC workspace 分包内/包外, 14 个 Source 逐条核对, 516 文件落点不变
3f81693f refactor(gateway): A4 17 参装配函数拆成骨架+两个贴纸, 桌面端不再建飞书管理器
8eddfe9f refactor(session): B6 4 处 __file__ 推根改为接收传入路径, 5 个 hook 调用点补上 agent 根
1c8ce23a refactor(gateway): A3 openapi 915 行按 path 分三份, 26 个 path key 并集与 schema 逐一不变
a9099a25 refactor(workspace): haitun-workspace 迁出 examples 为 workspace/tob, 60 处引用清零, 补回静默丢失的 10 个测试
a3077d7d refactor(runtime): A2 10 个 manager 1740 行移出 gateway, runtime 对 gateway 依赖归零
3839acb9 refactor(gateway): A1 切断 runtime 候选对 ToC 的 2 处依赖, 品牌字面量收拢到 1 个文件
e01a70b7 refactor(session): 11 份逐字节相同的 compact_history 收进内核, 12/12 压缩输出逐字节不变
```

设计方案原文：`docs/superpowers/specs/2026-08-26-gateway-workspace-architecture-evolution.md`
