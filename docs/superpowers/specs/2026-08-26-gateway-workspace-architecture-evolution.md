# psi-agent Gateway 与 Workspace 架构演进方案

**状态**：第四版 —— A 线（A1–A5）与 B 线（B1/B2/B3/B5/B6）已落地，本版按**实测代码**回写
**日期**：2026-08-26（第四版回写 2026-08-28）
**基准**：方案基准 `main @ 64b6273b`；落地实测基准为 a 线 `3d102482` + b 线 `5555475e` 的合并树
**本版改了什么**：以落地代码为准推翻了旧文档 6 处 —— 5.2 的升级机制（`ignoreversion` → 整目录改名）、
改名影响面（20 处/10 文件 → 60 处/30 文件）、`_todo_store` 命中数、hook 契约判据（「6 个全非 None」是错的）、
`extra-paths` 不是静默项、`workspace/toc` 的归属。另新增 G1 判据盲区与 worktree import 路径两条坑。
**未落地项**：A6（ToB 前端脚手架，仅有未提交的工作树文件）、`workspace/toc`、B4、T1 —— 见八章。
**标注**：〔实测〕真跑过命令 · 〔推断〕从事实推导，未直接验证 · 〔估算〕带不确定性

---

## 给非技术同事的一页

**我们在卖两个东西**：一个是装在用户电脑上的桌面版海豚（ToC），一个是公司内部用的飞书机器人（ToB）。两者共用同一套底层引擎。

**现在的问题**：底层引擎的关键零件，住在「飞书机器人 + 桌面版」这个混合房子里。想给引擎接第三个门面（比如飞书网页版），只有两个办法 —— 伸手去别人房子里掏零件，或者把整个房子复印一份。

**不改会怎样**：已经发生了。仓库里 PR #736 要给飞书做独立后端，做法是复印了 20 个文件过去，其中 **14 个是纯复制** —— 9 个和原件一个字都不差，另外 5 个唯一的区别是改了 import 里的包名〔实测〕。以后引擎修一个 bug，要在两个地方各修一遍，漏一个就是线上事故。

**改了得到什么**：零件搬进一个中立的公共库，谁都能正常取用。两条产品线各自只保留自己的门面。第三条产品线来的时候，不用再复印。

**另外还有个 ToC 独有的坑**：桌面版升级时，安装包会把整个目录**无条件覆盖**一遍。而海豚被设计成能自己改写自己的性格文件（`SOUL.md`）、记住用户的偏好（`USER.md`）—— 这些文件就住在被覆盖的目录里。用户用了三个月养出来的海豚，一次升级全没了。现在没爆，只是因为还没人真的在用这个能力。

**这份方案分两条线，可以并行做，A 线（引擎搬家）优先。**

**本轮范围就是「架构」，不含任何业务功能。** 飞书那边的前端，本轮只做到「空框架能启动、页面能打开」，页面上一个功能都没有 —— 登录、会话列表、对话收发都由其他同事后续开发。这样做是为了让这次评审只讨论一件事：零件摆的位置对不对。

---

## 一、结论

**拆 gateway 不是目的，把内核能力从 gateway 里搬出来才是。**

`SessionManager`、`AIManager` 这些管理内核进程的代码，现在住在 `gateway/` 包里。这 1740 行代码只认识内核概念，不认识网页界面、不认识飞书、不认识桌面托盘 —— 它们放在这里，纯粹因为当年只有一个 gateway。

workspace 那边**是两个各自独立的问题，别混着治**：

- **有一批代码重复了 11 份**：`compact_history`（历史压缩）这 90 行，12 个 example workspace 里 11 个**逐字节相同**〔实测〕。它是引擎的正确性，不是产品表达，却由每个 workspace 各写一遍。
- **ToC 的 workspace 会被安装器覆盖**：`haitun.iss:67` 整目录覆盖到 `{app}\app`，而这个目录同时是 agent 包根〔实测〕。这个约束 ToB 结构上不存在。

方案由**四条判据**拼成，每条管一层，都能自己跑命令验：

| 层 | 判据 | 结果 |
| --- | --- | --- |
| manager | 代码**认识**哪些概念 | 新增 `runtime/`，10 个 manager 1740 行 |
| 路由 | 谁**调用**这个端点 | 分散进 `gateway/` 顶层 + `desktop/` + `feishu/` |
| systems 函数 | 跨 workspace **字节是否相同** | 256 行回收进内核 `session/_compaction.py` |
| workspace 文件 | 谁有**写权**（安装器 / 用户） | `workspace/{tob,toc}`，ToC 再分包内包外两半 |

**明确不做**：不立 `httpapi/` 中间包，不做 workspace 之间的共享库，不拆 `channel/`。

---

## 二、议程第一件事：#736 与 #738 怎么办

这件事必须先拍，因为它决定后面所有步骤的工作量。**两个 PR 都不合并，只作参考**（团队已定），但它们提供的信息直接改写了本方案。

〔实测 `gh pr view 736`〕该 PR 状态 OPEN，`+29524 / -551`，新增 `src/psi_agent/feishu_gateway/`：

- 新增 22 个 `.py`，其中 20 个与 `gateway/` 下同名〔实测 `git ls-tree pr736`〕。
- **9 个逐字节相同**：`_auth_store` `_chat_manager` `_defaults` `_free_model` `_history_manager` `_manager` `_oauth_manager` `_state` `_workspace_manager`〔实测 md5 比对〕。
- 另有 **5 个唯一的差异就是把 `psi_agent.gateway` 换成 `psi_agent.feishu_gateway`**：`_ai_manager` `_session_manager` `_summary_manager` `_title_manager` `_todo_manager`〔实测：抹平 import 前缀后 diff 为 0 行〕。
- 即 **14 / 20 个文件是纯复制**。剩下 6 个有真实改动：`server.py` 371 行、`__init__.py` 143 行、`_openapi.py` 112 行、`_spa_shell` 12 行、`_router_manager` 9 行、`_scheduler_manager` 4 行〔实测同法〕。
- 含 `feishu_gateway/feishu/index.html` 23761 行前端产物。
- **删掉了 182 行 `tests/integration/test_gateway.py`**。

这恰好是本方案第三章预测的后果的实证：零件不在公共库里，加门面就只能复印。

**#736 的前端只有产物，没有源码**〔实测〕：`feishu_gateway/feishu/` 下只有那一个 `index.html`，没有 `package.json`、没有 `src/`、没有 vite 配置。首行 `<!doctype html>` 后面直接跟 `lucide v1.8.0` 的 license 头，是打包输出。**这份前端无法重新构建**，React / pdfjs / xlsx 出 CVE 也打不了补丁。

### PR #738：源码脚手架，补上了 #736 缺的那半

〔实测 `gh pr view 738`〕OPEN，`+5030 / -0`，分支 `feat/feishu-web-scaffold`，新增 `feishu_gateway/feishu-web/`：

- 有 `package.json` / `package-lock.json` / `src/` / `vite.config.ts` / `tsconfig.json` / `.gitignore` / `README.md`。
- 技术栈与 C 端一致：Vite + React 19 + TypeScript + `lucide-react` + `marked`。
- `src/` 四个文件：`App.tsx` 1002 行、`styles.css` 2391 行、`api.ts` 185 行、`main.tsx` 35 行。

**它满足「源码进 git、`dist/` 不进」这条约定，#736 那种只提交产物的做法不满足。** 对照 main 上 `spa-v2` 的既有做法：`package.json` / `src/` / `vite.config.js` / `index.html` 都在 git 里，`dist/` 不在〔实测 `git ls-files`〕。

### 两个 PR 之间有一处隐藏耦合

**`/auth/feishu` 在 main 上不存在**〔实测 `git grep 'auth/feishu' -- src/` 无命中〕，只在 #736 里（`pr736:server.py:229`）。而 #738 的前端**依赖这个端点登录**。

也就是说 **#738 单独合进 main 跑不起来**，它有一个尚未落地的后端依赖。两个 PR 是一对，被拆成了两次提交。

### 结论

- **两个都不合**（已定）。#736 的价值是**证明了问题真实存在**：不拆公共层，加门面就只能复印 14 个文件。#738 的价值是**给出了 ToB 前端的目录约定和技术栈选型**，本方案采纳这两样，**但不照搬它的代码** —— 它的 `App.tsx` / `styles.css` / `api.ts` 共 3578 行是已做完的业务，超出本轮边界（见 3.6）。
- A 线做完后，ToB 后端重做成「只写飞书特有的部分」，能砍掉 #736 绝大部分新增行数。
- `/auth/feishu` **本轮不碰**（它在 main 上不存在）。等后续 ToB 业务开发时再作为新端点正式纳入 `register_feishu_routes`。

---

## 三、gateway：搬什么、为什么

### 3.1 现状

〔实测 `wc -l src/psi_agent/gateway/*.py`〕26 个文件 6103 行：

| 归属 | 文件 | 行数 |
| --- | --- | --- |
| **骨架**（要拆） | `server.py` 1031 · `_openapi.py` 915 · `__init__.py` 379 | 2325 |
| **内核 manager**（搬走） | `_todo_manager` 254 · `_session_manager` 243 · `_router_manager` 243 · `_history_manager` 237 · `_ai_manager` 218 · `_scheduler_manager` 146 · `_chat_manager` 116 · `_summary_manager` 112 · `_title_manager` 97 · `_manager` 74 | 1740 |
| **ToC 专属** | `_auth_manager` 486 · `_auth_store` 254 · `_state` 204 · `_workspace_manager` 185 · `_defaults` 119 · `_webview` 109 · `_attention` 108 · `_tray` 100 · `_ui_prefs` 86 · `_free_model` 80 · `_oauth_manager` 69 · `_spa_shell` 29 | 1729 |
| **ToB 专属** | `_feishu_manager` 209 | 209 |

### 3.2 判据：这段代码**认识**谁

归属看的是**代码引用的最高层概念**，不是「当前谁在调用」。查法：grep 每个文件的 `psi_agent.*` import。

〔实测〕10 个 manager 候选，认识的最高层概念分别是：`_manager` 无（纯 socket 工具）、`_session_manager` / `_scheduler_manager` → `session`、`_ai_manager` → `ai`、`_router_manager` → `router`、`_title_manager` / `_summary_manager` → `protocol` + `_sockets`、`_history_manager` → `session` + `_appdata`、`_chat_manager` → `channel._core`、`_todo_manager` → `gateway._defaults`。

**没有一个认识网页界面、飞书、桌面托盘、登录。**

对照 ToC 那 12 个〔实测〕：`_tray` / `_webview` 认识 `pystray` / `pywebview` / `PIL`；`_workspace_manager` 认识 Windows 盘符；`_defaults` 认识 workspace 路径字面量（搬迁后为 `workspace/tob`）；`_auth_manager` 认识云端认证服务。分界线干净。

**为什么不能用「ToB 用不用」当判据**：那测的是当前用量快照，会把暂时只有一个消费者的通用代码误判成专属。反例就在仓库里 —— `channel/_file_bytes.py:8-9` 的注释〔实测〕：

> 这里谁都不知道任何一个平台的上传 API。把它放进 `feishu/` 会保证出现一份逐字复制。

写这句话时 telegram 也没在用它。按用量判据它会被错判进 `feishu/`，然后在下一个 channel 出现时被复制一遍。

### 3.3 路由要按消费者分，但理由不是「ToB 用得少」

**这一节的论证在第三版被推翻过一次，值得记下来。**

第二版写的是：channel 侧调 gateway 只有 `/feishu/route` 和 `/defaults` 两个端点〔实测 `channel/feishu/client.py:157,182`，此事实仍成立〕，因此「ToB 容器里那批端点全是死的」。

**PR #738 证明这个推论是错的。** 它的 ToB 前端 `src/api.ts` 调的正是〔实测〕：

```text
/auth/feishu   /ais   /sessions   /sessions/{id}/history
/titles        /sessions (POST/DELETE)   /sessions/{id}/chat
```

`vite.config.ts` 把 `/auth` `/ais` `/sessions` `/titles` `/feishu` `/defaults` 全代理到 `feishu_gateway`〔实测〕。#736 的 `server.py:200-223` 也确实为 ToB 注册了这 20+ 条。

**「当前用量」正是 3.2 批评过的那种判据 —— 我自己在这一节踩了同一个坑。** 一个新前端出现，快照就失效了。

**正确的理由**：两条产品线的端点集合**不同，且各自独立演化**。ToB 不需要托盘、webview、Windows 盘符枚举、桌面登录；ToC 不需要 `/auth/feishu`。共有的那批（`/sessions` `/ais` `/titles`）**两边都要**，所以它们背后的 manager 必须在公共层 —— 这恰好加强了 A 线的必要性，而不是削弱。

**论证边界**：这证明的是**路由注册**要分开，**不能**推出 manager 不该共享。`_feishu_manager` 只用了 `SessionManager` 三个方法〔实测 `_feishu_manager.py:171-195`〕—— 用得少不改变 `SessionManager` 是内核能力这个性质。

**结论**：同一个能力，manager 放公共层，路由注册放产品层，两条线各自 `register_*_routes` 时按需挑。

### 3.4 真正的难点是 `create_app()`

〔实测 `server.py:200-231`〕难的不是那 54 条路由（平的，谁都能剪），是装配函数**17 个参数把两条产品线搅在一起**：

```python
async def create_app(aim, sm, tm, rm=None, favicon_path=None, app_name=..., attention=None,
                     feishu_ai_id="", feishu_workspace_root="", default_agent="", ...):
    app["fm"] = FeishuManager(...)      # 第 228 行，无条件 —— 桌面端也建
    app["wm"] = WorkspaceManager()      # 第 230 行，无条件 —— 飞书容器也建
```

桌面端容器里建飞书管理器，飞书容器里建 Windows 盘符枚举器。

切法：从「一个函数收所有参数、内部判断」改成「共同骨架 + 各自往上贴」：

```python
# ToC
app = await create_core_app(runtime); register_desktop_routes(app, ...)
# ToB
app = await create_core_app(runtime); register_feishu_routes(app, ...)
```

`_openapi.py` 915 行反而最简单：整个文件是一个 dict 加一个渲染函数，**零控制流**〔实测：全文唯一的 `def` 在 `:914`〕，按 path key 分份即可。

### 3.5 `channel/` 不用拆，它已经是目标形状

〔实测〕顶层 928 行里 `feishu` 只出现 8 处，全在注释里做举例；`telegram` / `cli` / `repl` 三个实现里 grep `feishu` 为零。四个实现互不 import。

`feishu/` 占 65% 不等于糅合：telegram 只有 238 行是因为 Telegram 本身简单，飞书要处理卡片交互、卡片快照、跨容器附件透传，天然就重。**体量不均衡不是结构问题。**

**gateway 现在的问题恰恰是「没做 channel 已经做完的这件事」。**

### 3.6 ToB 前端：本轮只落脚手架，零业务

**范围定义（硬边界）**：本轮的验收就是 **`npm run dev` 能起、浏览器打开有页面、`npm run build` 出得来产物、gateway 能把它挂上**。页面内容是一个占位。**前端业务开发由其他同事后续做，本轮不碰。**

**做**：

1. `feishu-web/` 骨架：`package.json` / `tsconfig.json` / `vite.config.ts` / `index.html` / `.gitignore` / `src/main.tsx` / `src/App.tsx`（占位）/ `AGENTS.md`。
2. `create_core_app()` 里 ToB 的静态挂载点，参照 `server.py:253,261` 现有两个 `add_static` 的写法。
3. `register_feishu_routes()` 的注册位（空壳，路由由后续业务往上贴）。

**不做**：任务 / 会话 / 交付物任何 UI、`api.ts` 的业务端点封装、`/auth/feishu` 登录流程、成品样式。

**#738 参考什么、不参考什么**

**它的目录约定和技术栈选型可以直接采纳，代码不要照搬** —— 因为 #738 是「脚手架 + 已做完的业务」，按本轮边界其中大部分超范围〔实测〕：

| 文件 | 行数 | 本轮 |
| --- | --- | --- |
| `src/App.tsx` | 1002 | **超范围**：21 个 `useState`、3 个 `useEffect`、4 个 `useRef`，「任务/交付物/会话」出现 71 处，是做完的产品页面 |
| `src/styles.css` | 2391 | **超范围**：成品视觉 |
| `src/api.ts` | 185 | **超范围**：封装了 8 个业务端点 |
| `src/main.tsx` | 35 | 可参考：入口 + ErrorBoundary，纯样板 |
| `vite.config.ts` | 18 | 可参考（proxy 只留连通性所需） |
| `package.json` / `tsconfig.json` / `index.html` / `.gitignore` | 少量 | 可参考 |

**`api.ts` 建议整个不要。** 一旦留它就得决定封装哪些端点，而端点集合是业务决策。连通性验证用一行 `fetch('/defaults')` 就够 —— `/defaults` 在 main 上已存在。

**`/auth/feishu` 本轮不碰。** 它在 main 上不存在（只在 #736 里），正好落在本轮边界外。这样 ToB 前端脚手架**没有任何未落地的后端依赖**，可以独立跑通。

**源码进 git，`dist/` 不进** —— 与 main 上 `spa-v2` 的既有做法一致（`git ls-files` 确认 `dist/` 未入库）。#738 满足，#736 不满足（只有压缩产物、无法重建）。

〔实测〕本机 node v24.19.0 / npm 11.17.0，构建环境可用。

### 3.7 脚手架的三项能力

本轮要落的就是这三样，**不含部署**（怎么上云是后续的事，不在本方案范围）：

1. **能构建** —— `npm ci` 装得上、`npm run build` 出得来 `dist/`、`tsc` 无错。
2. **能起开发服务器** —— `npm run dev` 起 vite，浏览器打开有页面。
3. **能连本地服务端** —— vite dev proxy 把请求转到本机 gateway，页面上一次 `fetch('/defaults')` 拿到 200。

第 3 条是「不是纯前端」的落点：`vite.config.ts` 里的 proxy 要指向本机 gateway，且后端侧 `create_core_app()` 有对应的静态挂载点。**验证用 `/defaults`**（main 上已存在），不引入任何业务端点。

CI 侧照 `spa-v2` 的既有模式加一段 `npm ci && npm run build`〔实测 `ci.yml:43-49` 有现成范式，`working-directory` 指向前端目录〕—— 这样脚手架的可构建性由 CI 持续保证，不会悄悄坏掉。

### 3.8 命名

`runtime` 管的正是「别的进程的实例」：`AIManager` 拉起 `ai` 进程，`SessionManager` 拉起 `session` 进程。它和 `protocol.py`（消息格式）、`_sockets.py`（传输）同类：跨进程基础设施。

不叫 `common`（垃圾桶名字），不叫 `gateway_core`（名字里带 gateway，下一个人加第三条产品线还是会犹豫）。

**已知顾虑**：`session/runtime_context.py` 已经在用 "runtime" 这个词指别的东西〔实测〕。备选 `instances/`。改名成本只在 import 行，**留给团队定**。

---

## 四、目标目录结构

```text
src/psi_agent/
  runtime/          # 新增：10 个 manager，1740 行。不认识任何产品概念
  gateway/          # 瘦身后只剩共同骨架 create_core_app()
    desktop/        # ToC：托盘、webview、登录、盘符、spa-v2 静态挂载
    feishu/         # ToB：飞书路由 + FeishuManager + /auth/feishu
      feishu-web/   # ToB 前端（源码进 git，dist/ 不进）—— 形态采纳 #738
  session/ ai/ router/ channel/ protocol.py    # 不动
  session/_compaction.py                        # 新增：收回 256 行共享逻辑

examples/  →  workspace/
  tob/      # 飞书机器人 workspace
  toc/      # 桌面版 workspace（内部再分包内 / 包外，见 5.2）
```

## 五、workspace：两个独立问题

### 5.1 问题一：有 256 行逻辑被复制了 11 份

〔实测〕12 个 example workspace 的 `systems/system.py`，按 AST 逐函数取顶层定义算哈希：

- `compact_history`：**11 份逐字节相同**（各 90 行），只有 `haitun-supervisor-workspace` 是另一份（71 行）。
- 另有 15 个函数（`_build_datetime_section` 等）在多个 workspace 间同样重复，合计 256 行。

**判据是「字节是否相同」，而不是「看起来像不像通用逻辑」**：字节相同意味着没有任何一个 workspace 需要它不同，那它就不是产品表达，是引擎行为放错了位置。

**收益要说准 —— 这里有一个必须纠正的说法。**

早先版本称「另外 10 个没有防护的 workspace 会自动获得防护」。**这个说法不成立**〔实测〕：

- 抗注入的真实防线在**内核侧**：`session/agent.py:110-143` 的 `_summary_looks_hijacked`（长度下限 + `HIJACK_ECHO_PREFIXES` 前缀匹配，`:81`）。它对所有 workspace 已经生效，与本次搬迁无关。
- 那 90 行共享函数**自己带**的防护是 `TRANSCRIPT_IS_DATA` 标记 + 把指令放在末尾（`a-serper-mcp-workspace/systems/system.py:143-146`），而这个 12 / 12 全都有。
- haitun 那套四层守卫（`strip_heartbeat_token` → `_has_meaningful_text` → `has_meaningful_conversation_content` → `is_real_conversation_message` → `_contains_real_conversation_messages`）**唯一的调用点在 `:1285`，位于 `System.compact_history()` 类方法内 —— 而这个方法根本没接线**。`AGENTS.md:383-392` 明确写了它是「future-extension hook，不要当死代码清掉」，且模块级同名函数与它不相通。

**所以 B1 该做，但理由只有一条：消除 11 份重复。不要拿「补上防护」当卖点，那是假收益。**

### 5.2 问题二：ToC 的 workspace 在升级时被**整目录改名换新**

**本节已按 `[Code]` 段重写。早先版本只读了 `[Files]` 段，把机制说成「`ignoreversion` 逐文件覆盖」，那是错的** —— 升级路径上 `ignoreversion` 根本没起作用。

〔实测 `.github/inno-setup/haitun.iss`〕真实机制是**整目录改名换新**，分三步：

1. `:427` —— `PrepareToInstall` 里的 `InstallsApp` 分支调 `SwapComponent('app')`。
2. `:277` —— `SwapComponent` 把 `{app}\app` **整个目录 `RenameFile` 成 `{app}\app.backup`**。改名前 `:263` 先 `DelTree` 掉上一次遗留的 `app.backup`。
3. `[Files]` 段随后把本版内容**铺进一个全新的空 `{app}\app`**。

关键推论：**铺文件的时候原地已经没有旧文件了**，所以

- `ignoreversion`（不比版本、无条件覆盖）在升级路径上**没有对象可覆盖**，是个空转标志；
- `SOUL.md` / `USER.md` / `schedules` **不是「被覆盖」，而是被遗弃在 `app.backup` 里** —— 数据还在盘上，但新版海豚不会去读它；
- `:263` 的 `DelTree` 意味着**下一次升级会删掉上一次的 `app.backup`**〔推断，未实测〕。即用户连续升两次，第一次遗弃的性格与偏好在第二次被真正删除。

`rollback-state.json`（`:102`）能活下来，**不是因为它带 `onlyifdoesntexist`**，而是因为它装在 `{app}\` 而不是 `{app}\app\` —— 被改名的只有 `app` 子目录。这一点直接推翻了八章第 4 项的选项 (a)，详见该节。

**判据仍是「谁有写权」**：安装器写的（代码、tools、prompt 模板）放包内；用户和 agent 自己写的（SOUL / USER / schedules / 历史）必须放包外，且安装器不碰。ToB 没有安装器，结构上不存在这个问题 —— 这是 ToC 独有的。

**未验证**：仍然**没有真跑一次安装升级**（属推后的 T1）。本节机制来自 `[Code]` 段与 `[Files]` 段的完整静态阅读，比早先版本的证据强，但「`app.backup` 在下一次升级被删」这一条只有代码路径、没有实验。

### 5.3 一个搬迁时会踩的坑：内核和 workspace 各自算根目录

这决定了「把 SOUL / USER 移出包根」这个修法能不能直接生效。**答案是不能。**

〔实测〕内核调 workspace 的入口 `session/system_prompt.py:111`：

```python
await self._builder(user_message) if self._accepts_message(self._builder) else await self._builder()
```

**一个路径参数都没传。** 而 haitun 的 `systems/system.py` 在 4 处用 `anyio.Path(__file__).parent.parent` **自己推**根目录〔实测〕，其中 3 处（`:1468, :1686, :1697`）是**无条件**这么推的：

```python
agent_dir = anyio.Path(__file__).parent.parent      # :1468 / :1686 / :1697
raw = workspace_raw or _runtime_workspace() or str(anyio.Path(__file__).parent.parent)   # :1396
```

只有 `:1396` 留了外部来源优先、`__file__` 兜底。**另外 3 处没有任何外部注入口。**

也就是说：文件一挪位置，`__file__` 推出来的根就跟着变，内核无从纠正。**要么同时改这 4 处 + 内核传参约定，要么这一步做不成。** 早先版本把这条写成「顺手就能改」，不对。

### 5.4 hook 契约是靠名字对上的，写错了不报错

〔实测 `session/system_prompt.py:258-263`〕内核用 `getattr(module, name, None)` 找 6 个 hook。名字拼错 → 返回 `None` → 静默跳过。

`agent.py:812-814` 只在缺 `compact_history` 时打一条 `warning`，其余 hook 缺失连日志都没有〔实测〕。搬迁重命名期间这是最容易静默失效的地方。

**验收写法要纠正 —— 早先版本写的「逐个 workspace 断言 6 个 hook 都非 None」是错的判据。** B5 实测 12×6 全表：**12 个里只有 `workspace/tob` 满 6 个，其余 11 个只暴露 2-3 个**。这不是缺陷：`builder` / `checker` / `before` / `after` 有内核默认值，而 `turn_context_fn` 与 `compaction_fn` 的 `None` 按 `AGENTS.md`「契约与容错」**承载语义**（「这个 workspace 没有易变块」）。断言 6 个全非 `None` 会一次红 11 个 workspace，钉的是**内核并不存在的契约**。

**正确判据**：把 12×6 的实测结果钉成 `EXPECTED` 表逐格断言，少了多了都失败（单向下限拦不住改名后又冒出新名字），另加一条「glob 命中数恒为 12」防止用例被静默摘掉。已落地为 `tests/psi_agent/session/test_workspace_hook_contract.py`（25 个用例）。

---

## 六、落地顺序

两条线可并行，**A 线优先**（它是 #736 的前置）。

### A 线：gateway 拆分

| 步 | 做什么 | 验收 | 风险 |
| --- | --- | --- | --- |
| A1 | 切断 `runtime` 候选对 ToC 的依赖 | 见下方「A1 的真实工作量」 | 中 |
| A2 | 建 `runtime/`，移入 10 个 manager | `git grep -n 'from psi_agent.gateway' -- src/psi_agent/runtime/` **输出为空** | 中高 |
| A3 | `_openapi.py` 按 path 分份 | 各份 path key 的**并集**等于原 spec（不是字节比对，见下） | 中 |
| A4 | `create_app` 拆成骨架 + 两个 register | 全量 gateway 测试通过 | 中 |
| A5 | 目录落位、更新 import | 全量测试 + 手工起两条产品线 | 中 |
| A6 | ToB 前端脚手架 + 静态挂载点 + CI 构建段（3.6 / 3.7，**零业务、不含部署**） | 7.5 六条（S1–S6） | 低 |

**A1 的真实工作量**〔实测〕。早先版本把这处依赖记在 `_todo_store.py:23`，**该文件不在 `src/` 下**，所以 A1 的结论（真实依赖是下面 2 处）不变。但早先那句「全仓 grep `_todo_store` 无命中」**是错的，必须纠正**：〔实测 `git grep -n _todo_store`〕全仓 **3 处代码命中 + 1 处文档提及**，全在 ToB workspace 自己的 todo 工具里，与 `gateway/` 无关 ——

- `workspace/tob/tools/todo.py:15`（`import _todo_store as _store`）
- `workspace/tob/tests/test_todo.py:21`
- `workspace/tob/AGENTS.md:229`、`src/psi_agent/gateway/AGENTS.md:114`（文档提及）

路径按 B2 搬迁后的新位置写（原 `examples/haitun-workspace/` → `workspace/tob/`）。真实的 `runtime` 候选 → ToC 依赖是 **2 处**：

- `_session_manager.py:12` → `from psi_agent.gateway._defaults import ensure_workspace_dir`，用在 `:125`
- `_todo_manager.py:23` → `from psi_agent.gateway._defaults import` 三个 appdata 函数

两处指向的都是同一个文件 `_defaults.py`，所以本质是**一个**要解开的结。

并且 `_defaults.py` **不是纯转发**：它自己定义了 `resolve_default_workspace`（`:76`）、`ensure_workspace_dir`（`:93`）、`resolve_default_agent`（`:105`），这三个在 `_appdata.py` 里**不存在**〔实测 grep 未命中〕。其中 `:112` 硬编码：

```python
candidate = cwd / "examples" / "haitun-workspace"
```

**所以 A2 不是「只改 import 行」，A1 要先给这三个函数找到中立归宿。风险从「低」上调到「中高」。**

**A3 的验收条件要改**：原写法是「拆分前后 `openapi.json` 字节相同」。一份 spec 拆成三份之后不可能字节相同 —— 应改为「三份的 path key 并集 == 原 spec 的 path key 集合，且每个 key 下的 schema 不变」。

**A4 的验收有个已知脆弱点**：`tests/integration/test_gateway.py:291,388` 直接调 `create_app(...)`，能守住「飞书路由不串到桌面端」这条线〔实测：234 passed, 2 skipped，命令见附录〕。但它一旦改签名就会连带失败 —— 而 **#736 正好删掉了这个文件 182 行**。合 #736 前要先确认这批断言的去向。

### B 线：workspace 整理

**顺序已按依赖修正：B6 前置于 B3。** 见表下说明。

| 步 | 做什么 | 验收 | 风险 |
| --- | --- | --- | --- |
| B1 | 256 行共享逻辑收进 `session/_compaction.py` | 12 个 workspace 各跑一次真实压缩，输出逐字节一致 | 中 |
| B2 | **只搬 ToB**：`examples/haitun-workspace` → `workspace/tob`；其余 11 个示范件留在 `examples/` | 见下方「改名的影响面」 | 中高 |
| B6 | 内核与 workspace 的根目录约定统一（5.3） | 4 处 `__file__` 推导改为接收传入路径 | 中高 |
| B3 | ToC workspace 内部分包内 / 包外 | 逐条核对 `.iss` 里每个 `Source` 的落点 | 中 |
| B4 | **已推后** —— `.iss` 给用户数据加保护 | 原定真装一次 + 真升一次 | 高 |
| B5 | hook 契约钉成 12×6 实测表（原还含「以 `openclaw-style` 为基线新建 `workspace/toc`」，**未落地**）| 见 7.3-B5 | 低 |

**范围澄清：`workspace/toc` 不是 B2 的产物。** B2 只搬 `haitun-workspace`（即 ToB 那一个），其余 11 个示范 workspace **留在 `examples/`** 不动。早先任务书写成「`examples/` 改名为 `workspace/{tob,toc}`」是错的 —— 那会把 11 个示范件也一起搬走，B2 已按本方案纠正。

`workspace/toc` 原定是 **B5** 以 `openclaw-style` 为基线**新建**的产物，但**本轮没有落地**〔实测：`workspace/` 下只有 `tob`；全库无任何 commit 创建过 `workspace/toc`；`openclaw-style-workspace` 仍在 `examples/` 原地〕。B5 实际只交付了 hook 契约测试。**这一项仍未做，见八章「已知未修问题」。**

〔实测当前树〕`examples/` 下 11 个 workspace + `workspace/tob` 1 个，glob 合计 **12** 个 —— 与 B5 那条「命中数恒为 12」的断言一致。

**为什么 B6 必须在 B3 之前**：B6 不先做，B3 把 SOUL/USER 挪出包根之后，haitun 那 3 处**无条件**的 `__file__` 推导会指到错地方（`:1468, :1686, :1697`，无外部注入口），B4 的升级实验就验不到正确行为。

**改名的影响面 —— 早先版本的 20 处 / 10 文件是错的，B2 实测已推翻。**

早先那个数字用的量法是 `git grep 'examples/'` **只量 4 种扩展名（`.py` / `.ts` / `.tsx` / `.iss`）且只量正斜杠**，因此漏掉了 **CI / 打包脚本里的反斜杠写法** —— `.iss` / `.ps1` / `.yml` 里的 Windows 路径全部写成 `examples\haitun-workspace`，一处都没被那条命令看见。

〔实测，量法见附录〕按 `haitun-workspace` 这个名字量（排除 `docs/` 与 workspace 自身），真实影响面是 **30 个文件 60 处**，其中：

- **16 处是反斜杠写法**（旧量法完全漏掉）：`.yml` 10 · `.iss` 4 · `.ps1` 3 等 CI / 打包脚本；
- 按扩展名分布：`.py` 18 · `.md` 16 · `.yml` 10 · `.iss` 4 · `.toml` 3 · `.ps1` 3 · `.tsx` 2 · `.c` 2 · `.js` 1 · `CODEOWNERS` 1。

**量法必须用 `git grep`，且必须同时量正斜杠与反斜杠两种写法。** 只量一种就会漏掉整条 Windows 打包链路 —— 这条链路恰恰是改错了 CI 才会发现的地方。

**`pyproject.toml:109` 的 `extra-paths` 不是静默项 —— 早先版本说「改错不报错」，B2 做了负向对照，这条是错的。** 实测把它指向不存在的目录时，`ty` **直接硬失败**（报 `ty failed` / `does not point to a directory`）。所以这一处会响亮地失败，不需要靠人工核对。

测试里那 14 处会**响亮地失败**，不可怕。真正危险的是**唯一那处静默的** —— `gateway/spa-v2/src/App.tsx:16`：

```typescript
function isLegacyWorkspacePath(path: string): boolean {
  const n = path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  if (/\/examples\/[^/]+-workspace$/i.test(n)) return true
  if (n.endsWith('/haitun-workspace')) return true
  return false
}
```

前端拿路径**做正则匹配**判断是不是旧路径。改名后这个判断静默走错分支，不报错、不崩、只是行为变了。**B2 的验收里必须包含手工点一遍 SPA 的 workspace 切换。**

**B1 的验收条件要换掉**：原写法是「`grep -c HEARTBEAT_OK` 前后一致」。这是个假指标 —— 搬迁本身就会让它从 2 变成 0。应改为「12 个 workspace 各跑一次真实压缩，输出与搬迁前逐字节一致」。

---

## 七、验收标准

**前提：全量测试在本基准上不是绿的。** 〔实测 `main @ 64b6273b`，2026-08-27〕：

```text
57 failed, 1427 passed, 7 skipped in 343s
```

这 57 条是 Windows 上的**既有基线**（`test_channel_adapter` / `test_server` / `test_schedule_registry` 那批，asyncio 子进程 `NotImplementedError`），**不是回归**。

所以验收判据**不是「测试全过」**，而是**「失败集合逐行不变」**。必须比对失败的**节点 ID** 而非数量 —— 否则修好一条又新坏一条会互相抵消，看不出来。

### 7.1 起分支前先存基线

```bash
git switch -c <branch>

# 先确认 import 来源！见下方「在 worktree 里量必须先掰 import 路径」
.venv/Scripts/python.exe -c "import psi_agent; print(psi_agent.__file__)"

.venv/Scripts/python.exe -m pytest -q --no-cov 2>&1 \
  | grep -E '^(FAILED|ERROR)' | sort > baseline-failures.txt   # 应为 57 行
# 同时记下 passed 总数 —— G1(b) 要用（本基准实测 1427 passed / 7 skipped）
```

`baseline-failures.txt` 不要提交，留在工作区即可。

#### 在 worktree 里量必须先掰 import 路径（否则所有数字作废）

**这条是本轮踩过的坑，代价是一整轮测量全部作废。** `.venv` 里
`site-packages/_editable_impl_psi_agent.pth` **写死了主检出的 `F:\code\psi-agent\src`**。在
`.kanban/worktrees/*/psi-agent` 之类的第二棵树里直接跑 pytest，会变成
**测试文件来自本树、被测代码来自主检出** —— 不报错、不警告，只是数字没有意义。主检出的 HEAD
还会被别的任务推着走，所以同一条命令前后两次可以测到两份不同的代码。

```bash
# 判定：必须落在当前工作树下，不是 F:\code\psi-agent\src
.venv/Scripts/python.exe -c "import psi_agent; print(psi_agent.__file__)"

# 修法：每条验收命令都带上
PYTHONPATH="$PWD/src" .venv/Scripts/python.exe -m pytest -q --no-cov
```

实测后果：不带 `PYTHONPATH` 时量到 **59 failed / 1467 passed**，并 `diff` 出 2 条「新增失败」
（`test_feishu_manager` 的两条）；带上之后是 **57 failed / 1469 passed**，失败集与基线逐行相同。
**那 2 条回归是假的** —— 是拿 A2/A5 重构后的测试去跑主检出的旧代码撞出来的。`-o testpaths=`
只管收哪些测试、不影响 import 来源，两件事要分开处理。

### 7.2 四条全局不变量：每一步 commit 前都要过

```bash
# G1 失败集合逐行相同（核心判据）
.venv/Scripts/python.exe -m pytest -q --no-cov 2>&1 \
  | grep -E '^(FAILED|ERROR)' | sort | diff baseline-failures.txt -      # 必须无输出

# G2 runtime 不许反向依赖 gateway
git grep -n 'from psi_agent.gateway' -- src/psi_agent/runtime/           # 必须无输出

# G3 gateway 子集（-o testpaths= 必须写在路径之前，否则静默只跑 10 个）
.venv/Scripts/python.exe -m pytest -o testpaths= \
  tests/psi_agent/gateway tests/integration/test_gateway.py -q --no-cov  # 234 passed, 2 skipped

# G4 两条产品线都能起（手工，见 7.4）
```

### 7.3 分步闸门

| 步 | 通过条件 |
| --- | --- |
| A1 | `_defaults.py` 里那三个自有函数已迁到中立位置；G1 + G3 |
| A2 | **G2 输出为空**；`runtime/` 下 10 个 manager 齐全；G1 |
| A3 | 三份 spec 的 path key **并集 == 原 spec 的 path key 集合**，且每个 key 下 schema 不变（**不是**字节比对，拆分后不可能字节相同）；G1 |
| A4 | `create_core_app` + 两个 `register_*_routes` 就位；`test_gateway.py:291,388` 那两条飞书路由隔离断言仍在且通过；G1 + G3 |
| A5 | G1 + G2 + G3 + G4 全过 |
| A6 | 见 7.5 六条（S1–S6）；G1（后端只多一个静态挂载点，不应影响任何测试）|
| B6 | 4 处 `__file__` 推导改为接收传入路径；内核侧传参约定落地；G1 |
| B1 | 12 个 workspace **各跑一次真实压缩，输出与搬迁前逐字节一致**；`haitun-supervisor` 那份 71 行的处置已明确（**不是** `grep -c HEARTBEAT_OK`，那是假指标，搬迁本身就让它 2→0）|
| B2 | G1；**且手工点一遍 SPA 的 workspace 切换**（`App.tsx:16` 正则匹配路径，改名后静默走错分支，测试抓不到）|
| B3 | 逐条核对 `.iss` 里每个 `Source` 的落点；包内 / 包外划分与 5.2 判据一致 |
| B4 | **已推后**（见八章「本轮范围边界」）。原定：真装一次 + 真升一次，确认 `SOUL.md` / `USER.md` / `schedules` 存活 |
| B5 | 12×6 hook 解析结果钉成 `EXPECTED` 表逐格断言 + glob 命中数恒为 12（**不是**「6 个全非 `None`」，见 5.4）|

**顺序修正：B6 必须排在 B3 之前。** B6 不先做，B3 把 SOUL/USER 挪出包根后，haitun 那 3 处无条件 `__file__` 推导会指到错地方，B4 的升级实验就验不到正确行为。

### 7.4 最终闸门：链路跑通的定义

**原本八条，现改为七条。** 删掉的是原第 6 条（装一次 → 改 `SOUL.md` / `USER.md` → 升一次 → 未被覆盖）：**本轮验不了，因为本轮不改它** —— 该行为的修法属 B4，B4 与 T1（真装真升实验）已随打包部署一并推后（见八章「本轮范围边界」）。本轮既不改善也不恶化升级时的用户数据存活情况，所以没有可验的新行为。

全部做完、约评审之前，这七条要全绿：

1. **G1** 失败集合与 `baseline-failures.txt` 逐行相同，**且 passed 总数不得低于动手前实测值**（两条判据，见下方「G1 的判据盲区」）。
2. **G2** `runtime/` 零 gateway 依赖。
3. **G3** gateway 子集 234 passed / 2 skipped。
4. **ToC 端到端**：起桌面版 → 建 session → 发一轮对话拿到回复 → 托盘和 webview 正常 → SPA 切 workspace 正常。
5. **ToB 端到端**：起飞书 gateway → `/defaults` 和 `/feishu/route` 通 → 走一遍飞书消息收发。
6. **12 个 workspace** 压缩输出与搬迁前逐字节一致；hook 契约按 **12×6 实测全表**逐格核对（**不是**「6 个全非 `None`」，见 5.4 修正）。
7. **ToB 前端脚手架** 7.5 六条（S1–S6）全过。

#### G1 的判据盲区：只比对失败集会漏掉「用例被静默摘掉」

**这条是 B2 实测挖出来的，必须写进判据。** 只比对「FAILED / ERROR 节点 ID 逐行不变」是不够的：

- 两个测试文件用 `Path("examples").glob("*/systems/system.py")` 做**参数化**。`haitun-workspace` 搬成 `workspace/tob` 之后，它的用例**不报错地消失了** —— 参数化列表少一项，pytest 不认为这是错误。
- 结果：`failed` **恒定 57**，而 `passed` 从 **1427 悄悄掉到 1417**，静默少跑 10 个用例。
- 其中一条正是 **`HEARTBEAT_OK` 注入防护**的用例 —— 真出过事那个 workspace 的覆盖。

**只看失败集，这一步会显示全绿。** 所以 G1 是两条判据，都必须过：

- **(a)** FAILED / ERROR 节点 ID 与基线逐行 `diff` 无输出；
- **(b)** **`passed` 总数不得低于动手前实测到的值。** 动手前先记下 `passed` 数，改完再量一次，两个数字都写进 commit body。

`passed` 掉了要先查是不是有 **glob / 参数化 / 自动发现**的用例被静默摘掉，不要直接放过。**凡是靠 glob 自动发现的测试，搬迁时都要同步改 glob 的根**（B5 已把 hook 契约测试的 glob 改成同时扫 `examples/` 与 `workspace/`，并加了一条「命中数恒为 12」的断言把这个坑钉住）。

### 7.5 ToB 前端脚手架闸门（A6）

**这是本轮 ToB 前端的全部验收 —— 六条（S1–S6）过了就算完，不多做。**

**路径更正**：脚手架落点不是 `src/psi_agent/feishu_gateway/feishu-web`（那是 #738 的路径，而 #738 不合并）。按 A5 的落位，ToB 门面在 `src/psi_agent/gateway/feishu/`，脚手架应落在 `src/psi_agent/gateway/feishu/feishu-web/`。下面命令里的路径按此更正。

**状态：A6 未落地**〔实测〕。该脚手架目前只以**未提交的工作树文件**存在（`.gitignore` / `AGENTS.md` / `index.html` / `package.json` / `src/{App.tsx,main.tsx}` / `tsconfig.json` / `vite.config.ts`），全库**没有任何 commit** 创建过它。因此 S1–S6 本轮**无可验对象**，见八章「已知未修问题」。

```bash
cd src/psi_agent/gateway/feishu/feishu-web

# S1 依赖装得上（锁文件可复现）
npm ci

# S2 类型检查 + 构建出得来
npm run build          # 产出 dist/，且 tsc 无错

# S3 开发服务器起得来，页面打得开
npm run dev            # 浏览器开 http://127.0.0.1:5173 → 有页面、控制台无红色报错

# S4 dist/ 没被提交
git status --porcelain src/psi_agent/feishu_gateway/feishu-web/dist   # 必须无输出
git check-ignore -q src/psi_agent/feishu_gateway/feishu-web/dist && echo ignored-ok
```

**S5 本机 gateway 挂载生效**：起 gateway，`curl` 静态挂载路径拿到脚手架页面（后端只多一个 `add_static`，不新增任何业务路由）。

**S6 连得上本地服务端**：`npm run dev` 期间，页面里那次 `fetch('/defaults')` 经 vite dev proxy 打到本机 gateway，拿到 **200**。这是「不是纯前端」的判据。

**明确不在本轮范围内**：

- **部署**（上云、Caddy、oauth-proxy 白名单、`dist/` 下发方式）—— 后续单独做，本方案不覆盖。
- **业务**：登录、会话列表、对话收发、任务/交付物 UI、`/auth/feishu`。

---

## 八、待团队决定（本轮已拍的标注结论）

1. **#736 / #738 的处置**（第二章）—— **已定：两个都不合并，只作参考。** 本方案自己实现，不基于这两个 PR 的代码。
2. **`runtime` 这个名字** —— **已定：保留 `runtime`，不改 `instances`**（负责人拍）。与 `session/runtime_context.py` 的用法撞车这一点已知并接受。
3. **上会前是否补做一次真实安装升级实验**（5.2）—— **已推后（T1）**，理由见下方「本轮范围边界」。5.2 的机制已按 `[Code]` 段读完，证据强度比早先版本高，但仍无实验。
4. **B4 的用户数据保护策略** —— **整项已推后**，且候选方案被 5.2 的发现改写：
   - **(a) 逐条加 `onlyifdoesntexist` —— 已排除。** 升级走的是「整目录改名 + 铺新目录」，铺文件时原地没有同名文件，该标志不起作用。`rollback-state.json` 能活是因为它在 `{app}\` 而非 `{app}\app`，与该标志无关。
   - **(b) 把用户数据移出 `{app}`（`{app}\app` 之外）—— 唯一可行方向**，但**具体落点未拍**（`{app}\` 下另立目录，还是走 AppData）。
5. **`examples/` 改名要不要与 B3 拆包一起做** —— **已定：分两次做。** B2 先搬 workspace，B3 再分包内包外。
6. **6 个 hook 的契约要不要从 `getattr` 名字查找改成显式注册** —— **仍未拍。** 本轮 B5 只把现状钉成测试表（12×6），没改机制。
7. **A 线做完后 `gateway/` 顶层保留哪些路由** —— **仍未拍**（A5 落地后骨架层剩 5 个装配件，路由归属按实际落地，未另行拍板）。

另外，**B1 的 71 行处置 —— 已定：留着，走覆盖机制。** `haitun-supervisor-workspace` 那份 71 行的 `compact_history` 不删、不合并，由 workspace 侧同名定义覆盖内核默认实现。

### 本轮范围边界

**本轮只做开发架构。** 以下两项已由负责人推后，不在本轮：

- **B4**：用户数据移出被换掉的目录、`.iss` 保护策略、存量迁移、卸载语义。
- **T1**：真装真升实验。

推后的理由：这两项属于**打包部署**，而它们的正确落点取决于本轮定下来的 workspace 结构 —— 结构没定就改 `.iss`，等于把同一处改两遍。

### 已知未修问题

- **`workspace/toc` 未落地。** B5 原定以 `openclaw-style` 为基线新建它，实际只交付了 hook 契约测试〔实测：`workspace/` 下只有 `tob`〕。四条判据表里「`workspace/{tob,toc}`」目前只实现了 `tob` 一半。
- **测试用例存在跨文件的命名管道污染（既有问题，非本轮引入）。** `tests/integration/test_gateway.py` 与 `tests/psi_agent/gateway/test_feishu_manager.py` 共用硬编码管道前缀 `gw-test`，全量跑时前面的测试留下同名管道，后面 `serve_session` 绑定被 `[WinError 5] 拒绝访问` 拒掉。表现为 `test_route_*` 里**随机某一条**失败（哪条失败取决于执行顺序与时序），单独跑该文件 27/27 全绿。该前缀在改动前的基准上逐字相同，本轮未引入也未修复。
- **`{app}\app` 会被 `SwapComponent('app')` 整目录换掉**，用户数据（`SOUL.md` / `USER.md` / `schedules`）在升级时会被遗弃在 `{app}\app.backup` 里，且疑似在下一次升级被 `DelTree` 删除〔推断〕。**本轮既不改善也不恶化**：B3 只把包内 / 包外的落点分清楚，没有改变升级时的目录换新行为。修法属 B4，已推后。判据与证据见 5.2；`.iss:85-87` 已就地留下注释。

---

## 附录

### 复现命令

```bash
# 行数表（3.1）
wc -l src/psi_agent/gateway/*.py

# 路由数（3.4）
grep -cE 'app\.router\.add_(get|post|delete|put|patch)' src/psi_agent/gateway/server.py   # 54

# runtime→ToC 依赖（A1）
grep -rn 'from psi_agent.gateway' src/psi_agent/gateway/_session_manager.py \
  src/psi_agent/gateway/_todo_manager.py src/psi_agent/gateway/_todo_store.py

# 改名影响面（B2）—— 用 git grep，否则会把未追踪的构建产物一起数进来（会得到 1784 处）
# 改名影响面 —— 下面这条旧量法是错的，只保留作反例：
#   它只量 4 种扩展名、且只量正斜杠，漏掉 .ps1/.yml 与所有 examples\haitun-workspace 反斜杠写法
# git grep -n 'examples/' -- '*.py' '*.ts' '*.tsx' '*.iss' ':!docs/*' ':!examples/*' | wc -l   # 20 处（错）

# 正确量法：按名字量，不限扩展名，正反斜杠一并覆盖（'haitun-workspace' 两种写法都含这一段）
git grep -n 'haitun-workspace' -- . ':!docs' ':!examples/haitun-workspace' | wc -l          # 60 处
git grep -l 'haitun-workspace' -- . ':!docs' ':!examples/haitun-workspace' | wc -l          # 30 文件
# 反斜杠那一半单独确认（务必用 grep -F，shell 里 '\h' 的转义极易把命令写成永远返回 0）
git grep -n 'haitun-workspace' -- . ':!docs' ':!examples/haitun-workspace' \
  | grep -cF 'examples\haitun-workspace'                                                     # 16 处

# extra-paths 是硬失败项，不是静默项（负向对照）：指向不存在的目录时 ty 直接失败
# 改 pyproject.toml [tool.ty.environment] extra-paths 为不存在的路径后：
uv run ty check    # → ty failed / does not point to a directory
git grep -n 'examples/' -- '*.py' '*.ts' '*.tsx' '*.iss' '*.md' | wc -l                      # 280 处（含 docs 与 examples 自身）

# 测试基线（A4）—— 注意 -o testpaths= 必须写在路径之前，否则 pytest 静默只跑 10 个
.venv/Scripts/python.exe -m pytest -o testpaths= \
  tests/psi_agent/gateway tests/integration/test_gateway.py -q --no-cov
# → 234 passed, 2 skipped
```

`compact_history` 去重（5.1）复现脚本：

```python
# .venv/Scripts/python.exe -c "..."  —— 只遍历顶层定义，见下方说明
import ast, hashlib, pathlib, collections
h = collections.defaultdict(list)
for p in sorted(pathlib.Path('examples').glob('*/systems/system.py')):
    src = p.read_text(encoding='utf-8'); lines = src.splitlines()
    for n in ast.parse(src).body:                      # 关键：只看 .body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'compact_history':
            seg = '\n'.join(lines[n.lineno-1:n.end_lineno])
            h[hashlib.md5(seg.encode()).hexdigest()[:8]].append((p.parts[1], n.end_lineno-n.lineno+1))
for k, v in sorted(h.items(), key=lambda x: -len(x[1])):
    print(k, 'n=%d' % len(v), 'lines=%d' % v[0][1], [w for w, _ in v])
# → 一组 n=11 / 90 行，一组 n=1 / 71 行（haitun-supervisor-workspace）
```

该脚本需要 Python **3.9+** 解释器（用到 `ast` 的 `end_lineno`），且必须只遍历 `ast.parse(src).body` 的顶层定义 —— haitun / openclaw / hermes-style / fusion-flow 这 4 个 workspace 里有**两个**同名 `compact_history`（模块级 + `System` 类方法），按名字建字典会让类方法覆盖掉模块级的那个，得出错误结论。

### 明确没有验证的

1. **真实安装升级行为** —— 只有 `.iss` 的 `ignoreversion` 静态声明，没跑过升级；`[Code]` 段（`:95` 起）未读。
2. **12 个 workspace 的压缩实际输出** —— 只做了字节级哈希比对，没有各跑一次压缩。
3. **`prompt_sections.py` 里 184 个「未被引用的常量」** —— 未逐个核对，不作为本方案依据。
4. **`.ua/knowledge-graph.json`（926 节点 / 2024 边）落后基准 4 个 commit** —— 跨层依赖计数用它做过交叉参考，但所有进入本文的数字都以直接 grep 为准。

