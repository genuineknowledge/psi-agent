# feishu-web —— ToB 前端

## 这是什么

飞书侧 (ToB) 的 Web 前端 —— 「海豚一号」自建应用的**网页应用**能力, 与同一个应用的机器人
能力共用后端。

技术栈与 ToC 的 `spa-v2` 保持一致: Vite + React 19 + TypeScript。

## 为什么有它: 机器人开不了新会话

飞书机器人侧的 session 是**确定性派生**的 (`_feishu_manager.py` 的 `session_id_for`):
私聊永远是 `feishu-<open_id>` 一条, `route()` 幂等复用 + adopt, 所以同一个人**无法开第二
个会话**。历史落 `{appdata}/histories/{session_id}.jsonl`, 一个 session 一个文件 → 会话
内容与压缩一直往同一份文件里写, 上下文只增不分。

网页应用就是为解决这个: 「新建任务」走 `POST /feishu/sessions` **不传 id**, 后端发新 uuid,
于是新 session + 新 jsonl。

## 三条产品决定 (已拍定, 别再改方案)

1. **同一个人的多个会话共享同一个 workspace** —— session 各自独立 (各自一份 jsonl),
   workspace 共用一个目录。否则每开一个会话就多一个空目录、交付物散落。workspace 由后端
   `FeishuManager.workspace_for(open_id)` 派生, **前端不传 workspace**。
2. **IM 那条 session 在网页里正常显示、可续聊**, 打「来自飞书对话」角标, 双向可见。上下文
   将满的提示只挂在这一条上 (只有它会一直长)。
   - **它显示成「海豚一号」, 不是「未命名任务」。** 这条 session 就是与机器人对话本身
     (`feishu-<open_id>`), 身份固定, 不该跟着生成式标题走; 后端对它没有标题, 原先落到
     「未命名任务」, 列表里看不出它是谁。名字由 `taskModel.IM_SESSION_TITLE` **单点**给出,
     列表与顶栏都走 `displayTitle()` —— 两处各判一次 `from_im` 的话必然有一处先漏, 表现是
     同一个会话在列表和顶栏显示两个名字。
   - **它不可删除。** 删掉等于把机器人那侧的上下文一起扔掉, 而用户在 IM 里还会继续用到它。
     `tasks-view.tsx` 的两个删除入口(列表行内、详情面板)都对 `fromIm` 加闸。**这是显示层
     的闸, 不是硬闸** —— 底下的 `DELETE /sessions/{id}` 是骨架路由, 按约定语义一字不改
     (ToC 在用), 所以直打接口仍能删; 要硬闸得新开一条带鉴权的 `/feishu/...` 删除路由, 那是
     另一件事。
3. **第一版只做私聊**, 群聊 session (`feishu-chat-*`) 不显示。过滤精确到只滤群聊 ——
   用 `!startsWith('feishu-')` 会把私聊一起滤掉, 与决定 2 冲突。

## 品牌标: 只走 `brandMark()`, 不要自绘

侧栏左上角那个标**必须**用 `brandMark("sidebar")`(→ `.brand-logo-art` → 海豚 PNG)。它原先
是 `desktop-shell.tsx` 里的 `<span className="ht-app-mark" />`, 样式是一个蓝绿渐变方块
(`linear-gradient(135deg, #3370ff, #12a594)`)—— 与页面其余位置的品牌标不一致, 在飞书客户端
里看起来就是「图标不对」。色块那条规则已删, 别加回来。

尺寸在 `.brand-logo-sidebar`(36px, 沿用色块原来的尺寸, 免得侧栏布局跟着挪)。图片 URL 写在
`.brand-logo-art` 里, 是 `/haitun-dolphin.png` —— **Vite 会按 `base` 改写成
`/feishu-web/haitun-dolphin.png`**, 所以两种写法都能用, 但别手写成 `../` 之类相对路径。

判据: `tests/psi_agent/gateway/test_feishu_web_im_session_ui.py`(静态核对上面三处 + 品牌标,
删任一条即红)。

## 模型: 用机器人那一个, 网页应用不选

**网页应用没有「模型」这个概念。** 建会话挂哪个 AI 由后端 `GET /feishu/defaults` 给唯一
答案 —— 值就是 gateway 启动时的 `--feishu-ai-id`, 与机器人侧 `FeishuManager` 的缺省 AI 同
一个字段。机器人与网页应用本来就是**同一个 gateway 进程**, 于是两侧模型必然一致。

- **前端不打 `GET /ais`, 也不该有 AI 列表的概念。** 原先的写法是取 `ais[0].id`: 生产上恰好
  只有一条 AI 所以看着没错, 但 appdata 里存了多条时数组顺序无保证, 网页应用会**静默**用上
  一个和机器人不同的模型 —— 会话照样能建能聊, 没有任何报错。`listAis` 已从前端彻底删掉
  (`vite.config.ts` 的 `/ais` 代理也一并删了), 目的是让这件事在结构上不可能, 不是靠纪律。
- **端点只下发 id**, 不给 `api_key`/`base_url`/`provider`/`model` 任何一项。
- **没有兜底。** 拿不到就报错: 兜底触发就意味着悄悄换了模型, 静默走偏比直接报错难查。
- **不要做配置模型的页面或引导。** 飞书这条线是 ToB, AI 由部署者定死, B 端用户不该看见也
  不该改。ToC 的 `spa-v2` 那边用户自带 key、有配置页, 是另一件事, 别把那套搬过来。
- `--feishu-ai-id` 默认空。空的时候**不能建会话是正确行为**, 页面显示的是「本次部署未配置
  AI 实例…请联系管理员为 Gateway 配置 `--feishu-ai-id`」—— 指向部署配置, 不是让用户自己去
  配模型。页面不崩、会话列表照常显示。

判据在 `tests/psi_agent/gateway/test_feishu_defaults.py`(含「多条 AI 且指定的那条不是第一
条」这个唯一能暴出原缺陷的形状)。

### 本地怎么造一个 AI 实例

本机起 gateway 时 `/ais` 是**空的** —— 生产那份 appdata 在服务器上, 本机没有。所以本地开发
要自己造一份: AI 实例持久化在 `{appdata}/state/latest.json` 的 `ais` 数组, gateway 启动时从
`--appdata` 复原, `--feishu-ai-id` 只是**指名用哪一个, 它不创建 AI**。

照着敲(三步, `<...>` 全是占位符, 别把真 key 写进仓库或文档):

```bash
cd <repo root>
export PYTHONPATH=src
export DEV_APPDATA="$PWD/.tmp-dev/appdata"   # 随便一个本地目录, 别用真实 AppData

# 1. 起 gateway (第一次会建空 appdata)
PSI_FEISHU_DEV_OPEN_ID=ou_devtest_001 \
  python -c "from psi_agent.cli import main; main()" \
  gateway --gateway feishu --listen http://127.0.0.1:8765 \
  --appdata "$DEV_APPDATA" --feishu-ai-id dev-ai \
  --feishu-workspace-root "$PWD/.tmp-dev/ws"

# 2. 另开一个终端, POST 一条 AI 进去。id 必须与 --feishu-ai-id 一致。
curl -X POST http://127.0.0.1:8765/ais -H 'Content-Type: application/json' -d '{
  "id": "dev-ai",
  "provider": "<provider>",
  "model": "<model>",
  "api_key": "<your-own-key>",
  "base_url": "<https://your-endpoint>"
}'

# 3. 核对: 这里回的必须是 dev-ai
curl http://127.0.0.1:8765/feishu/defaults
```

AI 落进 `$DEV_APPDATA/state/latest.json` 后就**持久**了 —— 之后每次带同一个 `--appdata`
启动, 第 2 步不用再做。想复现「多条 AI」的场景就多 POST 几条不同 id, 再确认
`/feishu/defaults` 回的仍是 `--feishu-ai-id` 那一条。

**生产那把真 key 一个字符都不许进仓库。** 上面全是占位符, 本地用你自己的 key; 也不要在文档
或代码里写死具体模型名。

## 身份与免登

- 免登走官方 JSSDK: `index.html` 同步引 `h5-js-sdk-1.5.48.js` → `h5sdk.ready` →
  `tt.requestAccess({appID, scopeList: [], ...})` 拿 code → `POST /feishu/auth/login`。
  两级退路见 `src/services/feishuAuth.ts` 模块头 (JSSDK 旧 / 客户端旧 `errno===103`)。
- **appID 从后端 `GET /feishu/app-id` 取, 不写死在前端。**
- **open_id 由后端向飞书换回来**, 前端传什么都不看。登录态是 HttpOnly cookie
  `psi_feishu_sid`。
- 会话一族走 `/feishu/sessions`(服务端按身份过滤), **不走裸 `/sessions`** —— 后者不过滤,
  在浏览器里 filter 只是显示过滤, 谁都能直接打裸路由拿全量。
- **聊天流走 `POST /feishu/sessions/{id}/chat`, 不走裸 `POST /sessions/{id}/chat`。** 裸的那条
  一行身份校验都没有, 而它是**能驱动 agent 执行工具**的那条(跑 bash、读公司表格、往飞书发
  消息) —— 上公网后打它等于任何知道一个 session id 的人都能让公司 agent 干活。带鉴权那条的
  判定与 `/feishu/sessions/{id}/history` **同一套** `owns_session`: 未登录 401、会话不存在
  404、别人的/群聊的 403。实现不复制: handler 只做三段判定, 正文转给骨架的 `_serve_chat_sse`
  (见 `feishu/_routes.py` 的 `_web_chat`)。判据在
  `tests/integration/test_feishu_web_chat_auth.py`, 含一条把归属校验打成恒真的变异复核。

## 已知敞口

骨架的 `GET /sessions` / `GET /sessions/{id}/history` / `POST /sessions/{id}/chat` 在本进程里
**仍然无鉴权可达**(那条 chat 在容器内回环服务本机, 是它的合理用途)。做到的是「前端不再用它
+ `/feishu/*` 那一族默认拒绝」, 真正封堵要靠 Gateway 前面的反代白名单或骨架中间件, 是另一件事。

**跨身份隔离在真实飞书环境下的表现本地测不到** —— 那要真 open_id、真 `tt.requestAccess` 换回
来的 code、真容器拓扑。本地能验的只有用例层面那三条。

### 云上还剩两条裸路由没换(已报备, 未做)

路径清单里另有 5 条仍打**骨架的裸路由**, 在云上过不了白名单, 表现与上面那族一样是静默失败:

| 路径 | 云上表现 |
| --- | --- |
| `POST /titles` · `POST /titles/generate` | 补标题失败 → 列表里一直是「未命名任务」 |
| `DELETE /sessions/{id}` | 删除按钮点了没反应 |

没跟着换的原因是它们**不是只读**: 补标题要在服务端跑一次模型, 删除要先把跨进程状态摘干净
(骨架的 delete 逻辑目前在 `server.py` 里, 抽出来才能给对等路由复用)。共享会话那条也已经
不允许删。要做的话是**单独一轮**, 别顺手塞进只读那一族 —— 那是把写路径混进已经审过的只读面。

## 常用命令

```bash
npm ci        # 按 package-lock.json 装依赖 (可复现)
npm run dev   # http://127.0.0.1:5173/feishu-web/
npm run build # tsc --noEmit 后 vite build → dist/
```

dev 期间要连的 gateway 默认是 `http://127.0.0.1:8765`, 用环境变量 `GATEWAY_ORIGIN`
覆盖。

## 本地开发怎么起

**两个进程**, 缺一个都跑不起来。

**1. gateway** —— 开开发旁路, 不配 app_id:

```bash
cd <repo root>
PSI_FEISHU_DEV_OPEN_ID=ou_devtest_001 PYTHONPATH=src \
  python -c "from psi_agent.cli import main; main()" \
  gateway --gateway feishu --listen http://127.0.0.1:8765 \
  --appdata "$PWD/.tmp-dev/appdata" --feishu-ai-id dev-ai
```

- `--appdata` 与 `--feishu-ai-id` 是**建会话必需**的: 少了它们 `/feishu/defaults` 回空串,
  页面会说「本次部署未配置 AI 实例」。先按上面「本地怎么造一个 AI 实例」那三步造一条。

- `--listen` **必须带 `http://`**。裸 `127.0.0.1:8765` 会掉进 Unix-socket 分支, 在 Windows 上
  直接 `ValueError`(见 `_sockets.py` 的 `create_site`)。
- 不带 `--listen` 时监听的是**随机高位端口**, 启动日志末行的 `Gateway listening on ...` 才是
  真实地址。这时 vite 那边要把 `GATEWAY_ORIGIN` 指到那个端口:
  `GATEWAY_ORIGIN=http://127.0.0.1:<随机端口> npm run dev`。固定 8765 省掉这一步。
- 同时起两个 gateway 要给不同的 `--socket-path`, 否则 scheduler Session 撞同一个管道名。

**2. vite dev server**:

```bash
cd src/psi_agent/gateway/feishu/feishu-web
npm ci && npm run dev   # → http://127.0.0.1:5173/feishu-web/
```

浏览器开 **`http://127.0.0.1:5173/feishu-web/`**(带 `base` 前缀, 少了它 302)。改前端文案
不用刷页面, HMR 会自己更新。

**启动日志末行的 `Local:` 必须是 `5173`。** 不是就别往下走 —— 见下面第三个静默坑。
`strictPort: true` 已经让端口被占时**启动即失败**(`Error: Port 5173 is already in use`),
这时不要换端口凑合, 先把占着 5173 的进程收掉:

```bash
# Windows: 找出占用者(常是另一个 worktree 里忘关的 npm run dev)
netstat -ano | grep ":5173" | grep LISTEN   # 末列是 PID
powershell "Get-CimInstance Win32_Process -Filter 'ProcessId=<PID>' | %{\$_.CommandLine}"
taskkill /F /PID <PID>
```

真要同时开两棵树, 用 `npm run dev -- --port 5273` 并把浏览器地址一起改掉,
**别**把 `strictPort` 改回 `false`。

`psi_feishu_sid` 是 `HttpOnly; SameSite=Lax; Path=/`, 经 proxy 后**能**带上, 不需要额外配
`cookieDomainRewrite`: 5173 与 8765 同为 `127.0.0.1`, 只有端口不同, 而 cookie 不按端口隔离,
`SameSite=Lax` 也只管站点不管端口。实测过。

不想起 vite 也行: `npm run build` 后直接开 `http://127.0.0.1:8765/feishu-web/index.html`,
gateway 自己 `add_static` 服务 `dist/`。代价是每改一行都要重新 build —— 这正是本地开发要
vite 的原因。

## 本地能验什么、不能验什么

**能验**:

- 开发旁路进得去(直接进会话列表)。**提示在 gateway 启动日志里, 不在页面上** —— 启动时那条
  `FeishuAuth dev bypass is ENABLED at startup via PSI_FEISHU_DEV_OPEN_ID=ou_xxx` 就是它。
  页面上原先那条常驻通栏已撤: 旁路只在本机开发时开着, 而开发者就是启动 gateway 的人, 启动
  时喊一声就够, 不必占每个用户的一条通栏。每次旁路登录另有一条 WARNING(旁路**实际被用了**
  的痕迹), 与启动那条并存。
- 多会话互不串味: 建多个会话各发一句, 切换与刷新后各自只显示自己那句。
- 不设 `PSI_FEISHU_DEV_OPEN_ID` 时页面显示「请在飞书客户端内打开」而不是静默进入。
- proxy + cookie + HMR 这条链路。

**不能验**(别假装验过):

- **飞书客户端内的 `tt.requestAccess`**。本机浏览器没有 JSAPI, `window.h5sdk` 不存在,
  `code → user_access_token → open_id` 整条真免登链路一次都没跑。控制台那句
  `【H5-JS-SDK】: cannot find pc bridge` 就是它不在的证据。只能上云在真机验。
- **跨身份隔离**。旁路身份由后端的一个环境变量决定, 且 `POST /feishu/auth/login` 忽略 body
  里的 `open_id`(那是安全前提), 所以本机造不出第二个身份。这条靠
  `tests/psi_agent/gateway/test_feishu_identity.py` 与
  `tests/integration/test_feishu_web_sessions.py`(两个 sid 两个身份)加云上真机。
- 助手真的回话。本机注册的是假 `api_key`, 发消息后助手侧会报错 —— 不影响上面几条, 那些
  判据只看**用户自己那句话**落在哪个会话里。

## 本地与云上的分叉点(逐条)

上面那节说的是「本地这套东西能验到什么」。这一节说的是**两套拓扑本身的差别** —— 本地全通
但云上 404 这类失败就出在这里, 不在任何一处代码里。

云上是 Caddy 占 80/443 → 反代 `127.0.0.1:8090` 的 `oauth-proxy.py`(**白名单**反代) →
gateway 容器。本地是浏览器 → vite dev server(proxy) → gateway, **直连, 没有白名单**。

| 分叉点 | 本地 | 云上 | 本地能不能验 |
| --- | --- | --- | --- |
| 路径可达性 | 直连 gateway, 前端打什么都通 | 过白名单反代, `ALLOWED_PATHS` 少一条即 404 | **不能**。本地永远碰不到, 只能靠清单核对(见下) |
| 身份 | `PSI_FEISHU_DEV_OPEN_ID` 旁路, 后端直接发 sid | 真免登: JSAPI `code` → `user_access_token` → `open_id` | **不能**。本机没有 JSAPI, 整条换取链一次没跑 |
| 静态资源 | `npm run dev`, vite 服务源码 + HMR | gateway `add_static` 服务 `dist/` | 能验 dev 一侧; `dist/` 一侧要 `npm run build` 后开 `:8765/feishu-web/index.html` |
| 挂了哪几面 | 文档里的起法是 `--gateway feishu` **单挂** | `launch-gateway.sh` **两面全挂** | 能验, 但**默认起法与云上不同**, 见下面那条 |
| 跨身份隔离 | 造不出第二个身份(旁路只认一个环境变量) | 真实多用户 | **不能**, 靠 `test_feishu_identity.py` + 云上真机 |

### 会话级只读一族走 `/feishu/sessions/{id}/…`, 不打骨架的裸路由

任务进度、历史子任务、交付物下载、对话历史导出这四件事, 前端打的是**带鉴权的对等物**
(`_routes.py` 里的 `_web_todos` / `_web_todo_segments` / `_web_todo_segment` /
`_web_download_file` / `_web_export_history`), 不打骨架的 `/sessions/{id}/todos` 那一族。

两条理由, 任一条都足够:

- **云上**那几条不在 `oauth-proxy.py` 的 `ALLOWED_PATHS` 里, 恒 404(而前端只显示一个
  笼统的失败)。表现是左侧任务上下文永远停在「待继续」、进度恒 0。
- **本地**它们一行鉴权都没有 —— 下载那条与 `/workspace/file` 一样能按任意路径读服务器上的
  文件。对等物走 cookie 身份 + 归属校验, 且落在**已在白名单里的** `/feishu/sessions/`
  前缀下, 所以加这几条**不需要动 oauth-proxy**。

下载(`/feishu/sessions/{id}/files?path=`)的边界是**那条会话自己在 history 里声明过的文件**
(`sends` / `recvs` / `files[].path`), 不是"落在 workspace 下" —— 交付物完全可能落在 workspace
之外(agent 写到别的目录、用户上传的附件被 channel 下到 `~/Downloads/.psi/`)。归属校验 +
这份白名单已经封住了越权面。行为判据在 `tests/integration/test_feishu_web_peer_routes.py`
(未登录 401 / 别人的 403 / 不存在 404 / 非本会话交付物 403 / 缺 `path` 400 / 导出与磁盘逐字节相同)。

导出(`/feishu/sessions/{id}/export`)回的是磁盘上的**原始 jsonl**, 不是 `/history` 那种投影:
投影丢了工具调用参数与 `thinking_ms`, 用户拿回去与原始记录对不上。前端那一侧对应
「宝箱」(挑交付物下载)与「导出对话历史」(挑会话下 jsonl)两个入口 —— 它们**不是同一件事**,
所以是两个按钮。

### `/workspace/*` 归 desktop 那面 —— 单挂时本地就 404

`GET /workspace/file` 与 `POST /workspace/reveal`(交付物抽屉在打)的 handler 住在
`gateway/desktop/_routes.py`, **不在** `feishu/_routes.py` 里。于是:

- `--gateway feishu` 单挂(上面「本地开发怎么起」里的起法): 这两条**路由不存在**, 实测
  `404 text/plain`。
- `--gateway desktop feishu` 两面全挂(**生产就是这样**): 实测 `400`(缺 `path` 参数),
  路由在。

危险在于这两种 404 长得不一样但都是 404: 一个是本地少挂一面, 一个是云上白名单缺条, 排查
时容易认错。想让本地拓扑贴近生产就两面都写:

```bash
psi-agent gateway --gateway desktop feishu --listen http://127.0.0.1:8765
```

归属由 `test_workspace_paths_need_the_desktop_surface` 钉住 —— 哪天 handler 搬了家,
那条会红, 提醒回来改这张表。

## 路径清单: 挡「本地全通、云上全 404」

前端会打的后端路径有一份**从源码提取**的清单: `api-paths.json`(22 条), 生成与消费都走
`scripts/feishu_web_paths.py`。

**不人手维护**是关键: 前端加一个端点没人会想起来更新清单, 而漂移的表现恰好就是云上 404。

```bash
python scripts/feishu_web_paths.py --check         # 清单与源码是否一致
python scripts/feishu_web_paths.py --regenerate    # 前端改了端点, 重新生成
python scripts/feishu_web_paths.py --probe http://127.0.0.1:8765   # 逐条打, 找 404
python scripts/feishu_web_paths.py --print-shell > check-feishu-web-paths.sh
```

判据在 `tests/psi_agent/gateway/test_feishu_web_api_paths.py`, **双向**绑住:

- 前端多打一条而清单没更新 → 红。
- 清单多一条而前端已不打 → 也红(少了这条, 从清单里删一条不会被发现, 而被删的那条恰好就是
  不会去核对白名单的那条)。
- 有人在**第三个文件**里直接 `fetch(` → 红。提取器只读 `src/api.ts` 与
  `src/services/chatStream.ts`, 多一个发请求的文件它不报错、只是少提一条: 清单齐全、测试
  全绿、云上照旧 404。判据匹配的是**调用形状**(`fetch(` / `new EventSource(` / `axios.`),
  不是构造名本身 —— 本产品有个工具就叫 `fetch`, 进度文案表里写着字符串 `"fetch"`, 只认词
  会让这条判据永远是红的噪音。

**判据是路由存在性, 不是状态码为 200。** `/feishu/*` 一族未登录是 **401**, 写成 `== 200`
会因为没带身份而假红; 拿哨兵 id 打 `/sessions/{id}/todos` 回的是 handler 自己判出的 404
(`application/json`), 与「路由不存在」的 404(aiohttp 的 `text/plain` `404: Not Found`)
必须分开 —— `classify()` 就是这条判据。**只看状态码会让 `/sessions/<id>/*` 一族全报假
FAIL**, 实测踩过(5 条)。

### 上云前怎么用

`--print-shell` 生成的脚本路径**内联**, 部署机上不需要 python 或 jq:

```bash
python scripts/feishu_web_paths.py --print-shell > check-feishu-web-paths.sh
# 拷到部署机, 对着 oauth-proxy 那一跳跑
bash check-feishu-web-paths.sh http://127.0.0.1:8090
```

FAIL 的行就是要和 `oauth-proxy.py` 的 `ALLOWED_PATHS` 逐条比对的路径。部署卡(`c6e60`)里
「放行路径 200/4xx、未放行必须 404」那个 for 循环**复用这一份清单**, 不要另写第二份。

**这份东西只读不改。** 白名单该放行哪些是负责人拍的方案, 不在这里决定。裸
`POST /sessions/{id}/chat` 那条已经拍了: **不暴露**, 网页应用改打带鉴权的
`POST /feishu/sessions/{id}/chat`(清单里因此只有后者), 裸的那条行为一字不改、继续在容器内回环。

## 三个静默坑(都实测踩过)

- **`vite.config.ts` 的 proxy key `'/feishu'` 是前缀匹配, 会把 `/feishu-web/` 一起吞掉。**
  本应用的 `base` 恰好也以 `/feishu` 开头, 于是前端路径连 `/@vite/client` 一起被代理到
  gateway: 打开 5173 拿到的是 gateway 里**上一次 build 的 dist**, 热更新永远不生效, 而
  `/feishu-web/` 带斜杠时 aiohttp 的 `show_index=False` 还会回 **403**。两个表现都不像
  「代理配错了」。现在那条 key 是正则 `'^/feishu(?!-web)'`, **别改回字符串**。
- **`strictPort: false` 会让 dev server 静默换端口, 于是 5173 上是别人的代码。** vite 的默认
  值就是 `false`: 端口被占**不报错**, 自己挪到下一个空闲端口(5173 → 5174 → 5175 ...), 而
  文档、书签、本文件里写的都还是 5173。5173 上活着的那个**别的** dev server(最常见来源:
  另一个 worktree 里忘关的 `npm run dev` —— Windows 上关终端不一定收走 node 进程)照旧应答:
  **页面能开、功能能用、改前端永远不生效**, 因为你看的是另一棵树的源码。唯一线索是 vite 日志
  里 `Port 5173 is in use, trying another one...` 那行, 常被 npm 的输出刷掉。
  与上一条的表现几乎一模一样, 成因完全不同 —— 上一条错在**服务什么内容**, 这条错在
  **服务在哪个端口**。现在 `strictPort: true`, **别改回 `false`**。
  判据: `tests/psi_agent/gateway/test_feishu_web_dev_strict_port.py`。
  确认自己连的是哪棵树(编译产物里带绝对路径, 一眼看出):

  ```bash
  curl -s http://127.0.0.1:5173/feishu-web/src/components/tasks-view.tsx | grep -o '_jsxFileName = "[^"]*"'
  ```

- **旁路的类型判据**: `requestFeishuCode()` 里 `sdkReady()` 必须先于 app_id 检查。反过来写
  时「app_id 为空 + 不在飞书客户端内」(正是本地开发的默认组合)抛的是普通 `Error`, 而
  `useAuth` 的退路只认 `FeishuAuthUnavailable`, 于是旁路整段被跳过, 页面停在「登录失败:
  后端未配置飞书 App ID」。只有这一个组合会踩, 所以它藏得住。

## 流式渲染: 四处刻意为之的性能约束(勿"优化"回去)

助手回复是**逐 delta 落到 `msg.text`** 的(见 `hooks/useChatTurn.ts` 的 `onText`), 而
`ChatThread` 不做窗口化、`ChatMessageItem` 也没有 memo —— 那里的 props 全是内联回调, 每次
渲染都是新引用, 加了 memo 也恒失效。于是「每个增量重渲染整棵树」是既定事实, 渲染侧**唯一**
的防线只剩下面四处:

- **`MarkdownBubble` 必须 `memo`, 且解析结果必须 `useMemo`**(`components/markdown.tsx`)。
  memo 的作用是让**历史消息整条跳过**(它们的 `text` 没变)。没有它时, 每一轮增量都会把会话
  里**每一条**助手消息重新解析一遍(marked + highlight.js + KaTeX), 开销 O(历史条数 × 平均
  篇幅)。改回普通函数组件 = 长会话下主线程被打满, 表现是**页面操作卡顿** —— 飞书开发者后台的
  远程调试工具还要在主线程上做元素拾取, 抢不到时间片。
- **正在生长的那条用 `useDeferredValue`**: memo 挡不住它(`text` 每个 delta 都变), 而整篇
  解析是同步的纯主线程开销, 「每 delta 全量重解析」在长回复下是 O(n²)。`useDeferredValue`
  让 React 在繁忙时跳过中间值 —— 渲染结果不变, 只是可能慢半拍。**别为了让气泡"更跟手"把它删掉**。
- **`renderMd.ts` 的 `highlightAuto` 必须带语言子集 `AUTO_LANGS`**: 不传子集时它会遍历
  `highlight.js/lib/common` 全部 30+ 种语法、各扫一遍全文, 是解析里最贵的一步, 而流式期间
  每个 delta 都要跑一遍。收窄**只影响着色**, 不影响内容渲染; 需要新语言就往 `AUTO_LANGS` 里加。
- **贴底滚动合并到下一帧**(`chat-thread.tsx` 的 `requestAnimationFrame` + cleanup 里的
  `cancelAnimationFrame`): 那个 effect 的依赖里有 `messages.at(-1)?.text`, 触发频率就是
  delta 频率, 而 `scrollIntoView` 每次都强制一次布局。一帧最多滚一次。

判据: `tests/psi_agent/gateway/test_feishu_web_stream_render.py`(静态核对上面四处, 删任一条即红)。

**另注**: `ChatMessage.interimText` 是**死字段** —— 全库只有读、没有写(增量全写进 `text`)。
`chat-message-item.tsx` 里那个 `interimText` 分支因此永不执行; 留着是为了不动无关代码。谁要
清理它, 记得连带删掉 `types.ts` 的字段与 `chat-thread.tsx` 依赖数组里的那一项。

## 与 ToC(spa-v2)对齐的功能: 移什么、不移什么

2026-09-15 把 ToC 的**输入与任务体感**五项移植了过来。移植的是**功能**, 不是 ToC 的组件树 ——
本文件开头说过, PR 版把 ToC 整棵组件树拷进来正是被去掉的「死重量」。

**移过来的五项**(判据: `tests/psi_agent/gateway/test_feishu_web_tob_parity.py`):

| 功能 | 文件 | 移植时的适配 |
| --- | --- | --- |
| 拖拽 / 粘贴文件进输入框 | `services/clipboardFiles.ts` · `services/composerFileDrop.ts` | 无(原样) |
| 排队发送(回合中 Enter 攒一条, 回合结束自动发) | `services/queuedSend.ts` | 无(原样); 触发点从 ToC 的「卡片回合」改成 `turn.sending` 的**下降沿** |
| 任务置顶 | `services/pinnedTasks.ts` | **不走 ToC 的 `appdataScope`** —— 见下 |
| 思考耗时「思考过程 · N秒」 | `services/messageTiming.ts` | 数据源两处: 历史 `thinking_ms` + 本回合前端计时 |
| 顶部状态区首次提示 | `components/task-status-tip.tsx` | 锚点改成 `.cend2-quick`; 「已看过」落 **localStorage** |

### 两处刻意偏离 ToC(别改回去)

- **置顶不按 appdata 分桶。** ToC 的 `appdataScope` 拿 `GET /defaults` 下发的 appdata 路径算指纹,
  而 ToB 的 `/feishu/defaults` **只回 `{ai_id}`** —— 那个端点刻意不下发部署者的路径与凭证。
  拿不到指纹就不分桶, 而这个顾虑在 ToB 侧本来也不成立: 网页应用的 origin 是部署域名(或固定端口的
  `127.0.0.1:8848`), 不像 ToC 装机版那样每次启动换随机端口、把同一份记忆根拆成多个偏好桶。
  **别为了「和 ToC 一致」去给 `/feishu/defaults` 加 appdata 字段** —— 那是把部署者信息下发给每个
  B 端用户。
- **提示的「已看过」落 localStorage**, ToC 用的是内存标记(每次刷新都再弹一遍)。ToB 是天天用的
  业务页面, 每次刷新都弹会变成噪音。代价是清掉浏览器存储才会再看到这条提示。

### 明确**不**移(产品决定, 见「三条产品决定」一节)

模型配置页 / AI 列表 / 用户中心 / 登录 OTP / workspace 选择器 / 首次使用引导。ToB 是另一种产品:
AI 由部署者用 `--feishu-ai-id` 定死、身份由飞书免登给定、workspace 由后端派生且前端不传。
`test_feishu_web_tob_parity.py` 的最后一条判据就是「这些文件不许出现」—— 要把它们做进来, 那是新的
产品决定, 不是「补功能」。

## 任务总览: 四件事的结论(2026-09-16)

判据在 `tests/psi_agent/gateway/test_feishu_web_task_overview.py`, 逐条对着这里的结论:

- **首屏落在与机器人共用的那条会话上**, 且它在列表里排最前(`from_im` 优先; 用户自己置顶的
  仍压过它)。用户从飞书工作台点进来, 想接着说的是刚才在 IM 里那句, 而 `list[0]` 常常是网页
  新建的别的会话。
- **新建对话不继承机器人那条的历史** —— 那是 bug, 不是设计。后端给新会话发新 uuid、写新
  jsonl, 不复制任何东西; 前端 `useSessionHistory` 曾把上一次的结果留在 state 里, 切会话那一瞬
  被铺进新会话, 而真正的空结果回来时又被 `messages.length > 0` 守卫挡住。修法是让历史行
  **与它所属的会话 id 绑在一起存**(陈旧数据在结构上不可见), 不是靠 effect 先后。
- **左下「任务上下文」的进度能到了** —— 根因是端点(见上一节), 另有两处同源毛病一并修了:
  换会话时 `selectedSegment` 必须收回 `live`(否则面板一直停在只读历史态), 交付物列表要把
  「本轮流式刚收到、还没写进 history」的那几个一起算(否则刚交付完的会话显示「0 份」而右侧
  抽屉显示 1 份)。
- **四个统计口径**都是真算出来的: 进行中 / 待处理 / 新交付物 / **本月执行**(本自然月跑过
  todo 的会话数, 按会话去重 —— 口径写在 `taskModel.countMonthlyRuns`)。此前那一格写死
  `"128"`, 而「导出」是个没有 `onClick` 的死按钮。

## 两条容易踩的约定

- **`dist/` 不进 git**(`.gitignore` 已挡), 与 `spa-v2` 的既有做法一致。源码进 git,
  这样产物永远能从源码重建。
- **`vite.config.ts` 的 `base` 必须与后端挂载前缀一致**, 都是 `/feishu-web/`。后端
  挂载点在 `gateway/server.py` 的 `register_feishu_routes()` 里 (`add_static`)。改一边
  忘了另一边, 页面能开但资源全 404 —— 而且 aiohttp 侧 `dist/` 不存在时连 static 都不
  注册, 是静默 404, 不报错。

## 相关位置

- 后端挂载点与飞书路由: `../_routes.py` 的 `register_feishu_routes()`
- ToC 前端(技术栈参照): `../../desktop/spa-v2/`
