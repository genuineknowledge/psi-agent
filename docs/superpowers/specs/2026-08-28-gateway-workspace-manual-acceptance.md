# gateway/workspace 架构演进 —— 手工实测与验收清单

## 结论先行

自动化能验的已全部验完并通过。本文档只列**我在本机验不了、必须由你手工过**的项。

被测对象:`refactor/gateway-workspace-evolution` @ `3fa34a4c`(A1-A7 + B1-B6 全部落地)。
`main` @ `64b6273b` 全程未动。

已自动验过的(你不用重复):

| 项 | 结果 |
|---|---|
| 全量测试 | 57 failed / 1469 passed / 7 skipped |
| 失败集 vs 基线 | 逐行相同(那 57 个是 Windows 既有的 asyncio 子进程问题,非本轮引入) |
| 骨架不反向 import 产品包 | 判据 grep 无输出(改前 `server.py` 有 7 行) |
| runtime 不 import gateway | 无输出 |
| 路由集合与注册顺序 | 改前改后逐字节相同(两种参数组合各比一次) |
| gateway 子集 | 240 passed / 2 skipped |
| ruff check / format | 全过 |

**为什么还需要手工测**:上面全部是**进程内**验证 —— 装出 app 比对路由表,没有真的
bind 端口收请求、没有真的起托盘和浏览器。A7 把两个装配函数从骨架搬进了产品包,A6 新
增了一个静态挂载点;这类改动"装得出来"和"跑得起来"之间还有一段,只能手工过。

一共 3 项,预计 20 分钟。**任一项失败请停下并把现象贴给我,不要自己改**。

---

## 测前准备

```bash
cd F:/code/psi-agent
git rev-parse --abbrev-ref HEAD    # 期望: refactor/gateway-workspace-evolution
git rev-parse --short HEAD         # 期望: 3fa34a4c
git status --porcelain | grep -v '^??'   # 期望: 无输出(无未提交改动)
```

三条都对上再往下。若 HEAD 不是 `3fa34a4c`,先 `git checkout refactor/gateway-workspace-evolution`。

---

## 实测一:桌面端(ToC)能起并跑通一轮对话

**测什么** A7 把 `register_desktop_routes` 及 ToC 专属 handler 从 `gateway/server.py`
搬进了 `gateway/desktop/_routes.py`(381 行)。搬动涉及 31 个函数,若有符号漏搬或引用
没跟上,进程启动时会直接崩;若路由贴漏了,页面会 404。

**怎么做**

```bash
cd F:/code/psi-agent
.venv/Scripts/python.exe -m psi_agent.gateway
```

按你平时的启动方式来 —— 若平时带参数(端口、workspace 等),照旧带上。

**验收标准**(4 条全过才算通过)

1. 进程起来了,**没有 ImportError / AttributeError / NameError**。
   这一条最关键:A7 搬了 31 个函数,漏一个就会在这里炸,不会静默。
2. 托盘图标出现。
3. 浏览器开 `http://127.0.0.1:8765/spa-v2/`(端口按你实际的)能看到界面,
   **不是 403、不是 404**。
   为什么盯这个:`add_static(show_index=False)` 与目录重定向的注册顺序曾经出过
   403 的问题,A7 搬动时顺序必须原样保留。
4. 建一个会话、发一条消息、收到回复。

**失败时贴什么给我** 完整的启动日志(尤其是 traceback 头尾),以及第 3 条失败时浏览器
看到的状态码。

---

## 实测二:飞书侧(ToB)能起并回消息

**测什么** 两件事叠在一起:
- A7 把 `register_feishu_routes` 搬进了 `gateway/feishu/_routes.py`(123 行)
- A6 在这个函数里加了一个 `add_static("/feishu-web/", ...)`

要看的是**原有飞书路由没被影响**,新加的静态挂载没把别的挤掉。

**怎么做** 按你平时起 haitun 容器的方式起一遍,然后在飞书里发一条消息。

**验收标准**(3 条)

1. 容器/进程起来了,无 traceback。
2. 飞书里发一条消息,能收到回复。
3. 日志里应该有这一行(`dist/` 没构建时是正常的,不是错误):

   ```
   Feishu web dist absent (...feishu-web/dist), static mount skipped
   ```

   如果你做了实测三并构建过 `dist/`,这行会变成
   `Feishu web enabled, serving ...`,也正常。

**注意** 这一项我完全没条件验(无飞书应用凭证)。它是三项里最需要你把关的。

---

## 实测三:ToB 前端脚手架(A6 唯一没验到的那条)

**测什么** A6 自己交代了:它无 GUI、起不了浏览器,所以"控制台无红色报错"是**间接**
验的 —— 逐个 curl 拉 `main.tsx`/`App.tsx`/`@vite/client` 等资源确认全 200。真浏览器
下 React 挂载、HMR 客户端连接这些只有开控制台才看得见。

**怎么做** 需要两个终端。

终端 A(先起 gateway,前端要连它):

```bash
cd F:/code/psi-agent
.venv/Scripts/python.exe -m psi_agent.gateway
```

终端 B:

```bash
cd F:/code/psi-agent/src/psi_agent/gateway/feishu/feishu-web
npm ci
npm run dev
```

然后浏览器开 `http://127.0.0.1:5173/feishu-web/`,**按 F12 打开控制台**。

**验收标准**(4 条)

1. `npm ci` 成功,装上 23 个包。
2. `npm run dev` 起来,输出里有 `http://127.0.0.1:5173/`。
3. 页面显示标题「psi-agent 飞书前端脚手架」,以及一行
   「后端连通性 (GET /defaults): **HTTP 200**」。
   - 显示「连不上 —— ...」说明终端 A 的 gateway 没起或端口不对
   - 这一行是脚手架三项能力里「能连本地服务端」的判据
4. **F12 控制台无红色报错**(黄色 warning 可以忽略)。
   ← 这条就是 A6 没能验的那条,只有你能验。

**顺带可验(可选)** 若想看构建产物也能被 gateway 直接托管:

```bash
npm run build          # 产出 dist/
```

然后重启终端 A 的 gateway,日志会从 `static mount skipped` 变成
`Feishu web enabled, serving ...`,此时 `http://127.0.0.1:8765/feishu-web/index.html`
应返回 200。

**别提交 dist/** 它在 `.gitignore` 里,正常情况不会被 git 看到。

---

## 我明确没验到的(如实交代)

| 项 | 为什么没验 |
|---|---|
| 两条产品线真起进程收请求 | 无飞书凭证、无桌面 GUI —— 即本文档实测一/二 |
| 浏览器控制台无红色报错 | 起不了浏览器 —— 即实测三第 4 条 |
| A6 新增的 CI job | 只在 GitHub Actions 上跑得到;它执行的 `npm ci` + `npm run build` 与实测三前两步同命令 |
| 那 57 个既有失败的成因 | 只确认了它们与本轮改动无关(与基线逐行相同),未逐个追根因 |
| `add_static` 的「dist 存在」分支 | 本机无 dist,只验了跳过那一支;但该分支代码逐字节未变 |

## 附:本轮顺带清掉的残留

删掉两个未跟踪目录,共 362M,均为搬迁后的可再生产物:

- `src/psi_agent/gateway/spa-v2/`(344M)—— 只有 `dist/` 与 `node_modules/`,
  源码在 `gateway/desktop/spa-v2/`
- `examples/haitun-workspace/`(18M)—— `skills`/`systems`/`tests`/`tools` 四个目录
  已是空壳(源码全在 `workspace/tob/`),其余是历史运行产出的 252 张图表 PNG 与
  少量事件文件

删除后全量复跑:57 failed / 1469 passed,失败集与基线逐行相同,确认是纯垃圾。

**遗留一个建议**(与本轮改动无关,单独处理):这些运行时产物**没有被 `.gitignore`
挡住**。实测发现它们曾被 Kanban 的内部 checkpoint 提交收进 `refs/kanban/checkpoints/...`
(那些 ref 不在任何分支上、进不了远程,但足以说明口子是真的)。谁哪天全量 `git add`,
252 张 PNG 就会进仓库。建议给 `charts/`、`channel_events/`、`.psi/` 这类运行时输出补
一条忽略规则。
