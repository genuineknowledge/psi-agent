# `deploy/haitun/` —— 生产部署脚本的版本控制副本

这里的文件分两类, **性质完全不同**:

| 文件 | 性质 |
| --- | --- |
| `Dockerfile` `Dockerfile.overlay` 两份 `*.dockerignore` `build-image.sh` | **准本**。构建时直接被用, 改这里就是改构建 |
| `oauth-proxy.py` `launch-gateway.sh` `.env.example` | **副本**。运行中的是目标机上那份, 改这里不生效, 要人工同步 |

---

## 构建资产(`Dockerfile` 一族)

### 为什么它们在 2026-09-10 才进 git

此前**只存在于目标机的构建目录里**, 全库无副本。搬机时只搬了运行目录
`/srv/haitun/psi-agent`(compose + `restart-stack.sh` + `workspace*`), 没搬构建目录 —— 实测
境内 A 机 `47.100.84.197` 上 `find / -maxdepth 5 -iname 'Dockerfile*'` 只有 psi-cloud /
psi-auth-impl / fmbuild 三份。

后果是双重的:

1. **A 机只能做 overlay 构建**(换 `/app/src`), 一旦 `pyproject.toml` / `uv.lock` 变了就没法
   全量 build, 新依赖装不进去 —— 而表现是运行期 ImportError, 不是构建期报错。
2. 发布文档写的「用**仓库里的** `Dockerfile` 全量 build」指向一个不存在的文件。

### 用法

```bash
# 在仓库根。overlay: 只换 src, 秒级
deploy/haitun/build-image.sh overlay <commit>

# full: 依赖变了必须用这个
deploy/haitun/build-image.sh full <commit>
```

`build-image.sh` 顺手把两条原先靠人记的规则变成了闸门: HEAD 必须等于目标 commit 且工作树
干净(发布硬规则 1, 8-18 事故背书); overlay 模式下会拿基础镜像里的 `pyproject.toml` /
`uv.lock` 与当前树比对, 不同就拒绝并提示改用 full。

### ⚠️ 镜像源的默认值按**境内**取, 境外构建必须显式覆盖

这是本目录里唯一一处「同一决策在两地结论相反」的地方, 所以默认值不是中立的。

境内 A 机 `47.100.84.197` 实测(2026-09-10):

| 源 | trixie InRelease | `simple/aiohttp/`(3.95 MB) |
| --- | --- | --- |
| `mirrors.aliyun.com` | 200 · 3.58 MB/s · 0.039s ← 默认 | 200 · 14.5 MB/s · 0.235s ← 默认 |
| `mirrors.cloud.aliyuncs.com` | 200 · 2.79 MB/s · 0.050s | 000(该机无此 pypi 路径) |
| `deb.debian.org` / `pypi.org` | 200 · 126 KB/s · 1.112s | 200 · **33 KB/s · 120s 未下完** |
| tuna | 200 · 95 KB/s · 1.483s | 200 · 1.57 MB/s · 2.168s |

境外 B 机(新加坡)实测(2026-09-01)是**反过来的**: `deb.debian.org` 比 aliyun 快 146 倍
(20.2 MB/s vs 138 KB/s)、tuna 直接 **403**、`pypi.org` 2.70s 优于 tuna 3.72s。

所以境外构建要这样传:

```bash
APT_MIRROR= PIP_INDEX_URL=https://pypi.org/simple \
NPM_REGISTRY=https://registry.npmjs.org \
deploy/haitun/build-image.sh full <commit>
```

`APT_MIRROR=` 是**空值, 表示不换源**, 与「没设」不是一回事 —— `build-image.sh` 用
`${VAR+x}` 区分这两者。

`pypi.org` 那条尤其要留意: 境内它不是 404 也不是超时, 而是 200 之后以 33 KB/s 涓流, 单个索引
120 秒下不完。写死任一边, 换机器时 build 都会挂在装依赖那层, 而报错长得像网络抽风。

基础镜像同理: A 机实测 `registry-1.docker.io` 直连 **15 秒无响应**, daocloud 加速器与 daemon
里配的 `registry-mirrors` 都能拉, 所以默认值写死加速器域名而不是裸 `docker.io`。

### 前端产物由构建阶段 1 生成, 不再手工

`feishu-web/dist/` 被 gitignore 排除, 而后端 `_routes.py` 的 `add_static` 在目录不存在时
**静默跳过** —— 页面 404, 日志只有一行 INFO, 容器状态一切正常。此前的补法是镜像外手工
`npm run build` 再叠一层 `Dockerfile.fw`(B 机 `/srv/haitun/build-34c73c65/Dockerfile.fw`),
纯人工步骤, 忘了做没有任何东西拦得住。

现在 `Dockerfile` 的阶段 1 用 `npm ci` + `npm run build` 产出 dist, 阶段 2 `COPY --from` 取过来。
两处硬要求:

- **拷 dist 必须排在 `COPY src` 之后** —— dist 在 src 子树里, 顺序反了会被盖掉, 而**构建仍然
  成功**。由 `test_dist_copied_after_src_so_it_is_not_overwritten` 钉住。
- **`*.dockerignore` 里的 `dist/` 必须带前导斜杠。** 无锚点写法在任意层级匹配, 会把三棵前端
  (`desktop/spa`, `desktop/spa-v2`, `feishu/feishu-web`)的产物全部排出上下文, 触发同一个静默
  404。由 `test_dist_ignore_rule_is_anchored` 钉住。

overlay 模式下 dist 完全来自基础镜像(它不跑 npm), 所以两份 Dockerfile 都在构建期
`test -f .../dist/index.html` 自检一次。

### `*.dockerignore` 这个文件名不是笔误

BuildKit 支持 `<Dockerfile 路径>.dockerignore`, 且它**优先于**上下文根的 `.dockerignore`。
2026-09-10 在 A 机(docker 29.7.2 / buildkit v0.32.2)实跑验证过生效, 不是照文档推断。这样排除
规则能跟 Dockerfile 放在一起, 不必往仓库根塞一份 `.dockerignore` 去影响同机其他项目的构建。

⚠️ 未启用 BuildKit 的 legacy builder **不认这个文件名**, 那种环境下要么升级 builder, 要么把
文件手工拷成上下文根的 `.dockerignore`。

### 判据

`tests/deploy/test_build_assets.py`(20 条)静态解析 Dockerfile 文本。本仓 CI 没有 docker, 而这
些缺陷都在文本层面: 源写死了、COPY 顺序反了、锚点丢了。真构建的验证在目标机做。

```bash
# 在仓库根跑。PYTHONPATH=src 与 -o testpaths= 都是必须的, 见 AGENTS.md
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -o testpaths= --no-cov tests/deploy/ -q
```

### 已知没验到的

- **全量 build 在 A 机实跑过一次通过**(镜像 `psi-agent-gateway:probe-46566`, 见 PR 正文),
  但**没有拿它替换过任何在跑的容器** —— 镜像能起、页面能开都没验。
- 境外机的 build-arg 组合(`APT_MIRROR=` 空值那条路径)**没在境外机上跑过**, 只有本地判据。
  B 机当前 `running=0`, 而那些数字是 9-01 量的。

---

## `oauth-proxy.py`

**这份是生产机 `/srv/haitun/psi-agent/oauth-proxy.py` 的版本控制副本, 不是运行中的那份。**

改了这里**不会**对生产产生任何影响。生效需要人工同步:

1. 由负责人批准改动;
2. 拷到生产机 `/srv/haitun/psi-agent/oauth-proxy.py`;
3. 重启栈。**必须连带重建 `oauth-proxy` 容器** —— 它在 compose 里是
   `network_mode: "service:gateway"`, 只重建 `gateway` 时它会显示 `Up` 但网络命名空间
   已经失效。

收进仓库的原因: 它是**公网唯一入口**、决定了哪些路径能从外网打到 Gateway, 而此前只存在
于那一台机器上 —— 一个没有版本控制、没有 review、没有判据的安全关键文件。

### 它在链路里的位置

```
浏览器 / 飞书客户端
      │  443
      ▼
   Caddy (占 80/443, TLS 终止)
      │  反代到 127.0.0.1:8090
      ▼
   oauth-proxy.py  ← 本文件。白名单反代, 白名单外一律 404
      │  转发到 127.0.0.1:8080
      ▼
   Gateway 容器 (与本代理共享 netns, 故上游是 127.0.0.1)
```

Gateway 端口**不对外暴露**, 这一跳是唯一的入口。

### 它为什么必须是白名单

Gateway 上有一批**一行鉴权都没有**的路由, 与飞书网页应用的接口同住一个进程:

| 路由 | 危害 |
| --- | --- |
| `POST /sessions/{id}/chat` | 直接驱动 agent 执行工具, 含 bash。**带鉴权的对等物 `/feishu/sessions/{id}/chat` 是放行的**, 裸的这条不放行 |
| `POST /sessions` | 建 Session |
| `GET /sessions` `GET /sessions/{id}/history` | 读任意会话历史 |
| `GET /workspace/file` | 读 workspace 里的文件 |
| `POST /chat/completions` | 直接用掉模型额度 |

挡住它们的只有这个白名单一层, 所以 `ALLOWED_PATHS` / `ALLOWED_PREFIXES` **只列前端真的
会打的路径**, 多放一条就是白送一份公网暴露面 —— 而多放行**没有任何症状**, 直到有人从
公网打过来。

### 改白名单前先看判据

`tests/deploy/test_oauth_proxy.py`(20 条)双向钉住:

- 该放行的没放行 → 红(清单来自 `feishu-web/api-paths.json`, 前端加端点会被发现);
- 不该放行的放行了 → 红(`test_core_routes_stay_blocked` 逐条列了上表那些);
- 头没双向转发、多条 `Set-Cookie` 丢了、路径穿越能过 → 各有一条。

```bash
# 在仓库根跑。PYTHONPATH=src 与 -o testpaths= 都是必须的, 见 AGENTS.md
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -o testpaths= --no-cov tests/deploy/ -q
```

### 已知没验到的

**生产真机一次没验。** 本轮只在仓库里出代码 + 本地判据, 假上游不是真 Gateway。同步到生产
后至少要量三件事(前两件本地量不到, 见 `feishu-web/AGENTS.md` 的「本地与云上的分叉点」):

1. 真免登能拿到 cookie 并保持登录 —— 本机没有 JSAPI, 整条 `code → open_id` 换取链没跑过;
2. 放行清单逐条可达:
   ```bash
   python scripts/feishu_web_paths.py --print-shell > check-feishu-web-paths.sh
   bash check-feishu-web-paths.sh http://127.0.0.1:8090
   ```
   注意这份清单含 `/sessions` `/titles` `/workspace` 一族 —— 那几条**在这一跳报 FAIL 是
   预期的**(刻意不放行), 不要照着把它们加进白名单。
3. `/feishu-web/` 的静态产物能加载(依赖 Gateway 侧 `dist/` 存在, 不存在时 `add_static`
   静默跳过)。

### 放行范围里有 SSE, 转发层因此是流式的

`POST /feishu/sessions/{session_id}/chat`(带鉴权的聊天流, 能驱动 agent 执行工具)**在放行
范围内**, 它是一条 SSE。

它不是被单独加进白名单的, 而是**被前缀捎带进来的**: `ALLOWED_PREFIXES` 里的
`/feishu/sessions/` 原本是为 `GET /feishu/sessions/{id}/history` 加的, `startswith` 把同一
前缀下的 chat 一起放行了。这一点值得留意 —— 往那个前缀下加路由**不需要动白名单就会自动
对公网可达**, 加的时候要自己判断该不该暴露。

于是转发层用 `web.StreamResponse` 边收边转(`_relay`), 不是把 body 读完再回。三处硬要求,
每处都有判据:

| 要求 | 写错的表现 | 判据 |
| --- | --- | --- |
| 逐块转发, 不自己攒缓冲 | 打字机效果消失, 长回答疑似卡死 | `test_sse_chunks_arrive_before_upstream_finishes` |
| 不设 `Content-Length`, 交给 chunked | 截断, 或客户端等永远补不齐的字节 | `test_sse_response_has_no_content_length` |
| 响应头在 `prepare()` **之前**写完 | `Set-Cookie` 静默丢失, 登录不上 | `test_set_cookie_survives_streaming` |

超时也跟着改了: `ClientTimeout` **不设 `total`**。`total` 管的是「从发出到响应体读完」的
整段时间, 对 SSE 就是一条硬性寿命 —— 原先的 `total=15` 会让生成超过 15 秒的长回答从中间
断掉(实测: 客户端收到前几个 event 后拿到 `ClientPayloadError`, 等不到 `[DONE]`), 而短回答
一切正常, 所以这个缺陷很容易漏。改用 `sock_connect` + `sock_read` 两个闸, 它们量的都是
**间隔**而非总时长, 上游真卡死时仍能断开。由
`test_upstream_timeout_has_no_total_deadline` 钉住。
