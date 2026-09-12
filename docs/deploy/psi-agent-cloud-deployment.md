# psi-agent 云服务器部署流程

把 psi-agent 从本仓部署到一台空云服务器的可复用手册。**不是** 2026-08-20 那次搬迁的施工记录
（那份是云端 `/srv/haitun/docs/migration-log.md`，一次性的过程日志）。

本文的事实基础：2026-08-20 新加坡节点 `8.222.255.23` 的现网状态，与该次搬迁九条验收
（A1–A9）的实测结论。文中每条命令都在现网实跑过，输出为真实粘贴。

> **本文档的边界**：全文只覆盖 haitun（ToB）栈 —— `gateway` / `luolin` / `oauth-proxy`
> 三容器 + fusion-memory。**不涉及 ToC**（`psi-cloud`、`psi-litellm`、`account.` vhost）。
> 两者同机但完全隔离，改本栈不应触碰 ToC 的任何配置。

---

## 0. 先读这一节：哪些编排文件在本仓，哪些不在

> 🔵 **2026-09-10 更新：`Dockerfile` 一族已收进本仓 `deploy/haitun/`。** 本节初版写的
> 「本仓没有 `Dockerfile`」当时是对的，现在只对 `docker-compose.yml` 成立。
>
> 促成这次入库的是一个实测：境内 A 机 `47.100.84.197` 上
> `find / -maxdepth 5 -iname 'Dockerfile*'` 只有 psi-cloud / psi-auth-impl / fmbuild 三份，
> **没有任何 psi-agent 的 Dockerfile**。2026-09-09 搬机时只搬了运行目录
> `/srv/haitun/psi-agent`（compose + `restart-stack.sh` + `workspace*`），没搬构建目录 ——
> 而构建目录此前是唯一的存放地。于是 A 机一度只能做 overlay 构建，依赖一变就没法全量 build。
> 这正是本节初版说的「最大的落地风险」真的发生了一次。

**`docker-compose.yml` 仍不在本仓** —— 从未提交过：

```console
$ git log --all --oneline --diff-filter=A -- docker-compose.yml
(无输出)
```

所以「从本仓部署」的准确含义是：

| 来源 | 内容 |
|---|---|
| 本仓（git） | `src/`、`pyproject.toml`、`uv.lock`、`README.md` —— 镜像里 `pip install -e .` 装的那部分 |
| 本仓（git，后补入库） | `deploy/haitun/oauth-proxy.py`、`README.md` |
| 本仓（git，2026-09-10 入库） | `deploy/haitun/Dockerfile`、`Dockerfile.overlay`、两份 `*.dockerignore`、`build-image.sh` |
| 目标机（不在 git） | `docker-compose.yml`、`launch-gateway.sh`、`config.yml`、`restart-stack.sh`、`workspace/` |

最后一组仍是运维资产，无版本控制。第一次部署到全新机器时这批文件要从既有目标机拷来，或按
本文附录重建 —— **这仍是本文档最大的落地风险，只是范围比原先小了一半。**

`oauth-proxy.py` 原本也在第三组，本文初版据此写成「不在 git」；后来它已入库到
`deploy/haitun/`，所以**仓库里的这份才是准本**，改白名单改这份再拷上机，不要反过来。
拷的时候只单文件 `cp`，不要目录同步 —— 目标机 `workspace/` 下有 agent 自建的
工具与技能，任何形式的目录同步都会覆盖掉它们。

---

## 1. 前置条件

### 1.1 服务器规格

现网实测（`nproc` / `free -h` / `df -h /`）：

```console
$ nproc && free -h | head -2 && df -h / | tail -1
4
               total        used        free      shared  buff/cache   available
Mem:           7.1Gi       3.3Gi       1.3Gi        48Mi       2.8Gi       3.8Gi
/dev/vda3        40G   13G   25G  35% /
```

- **CPU/内存**：4C / 7.1G。当前用 3.3G，含同机 ToC 两容器。
- **磁盘**：40G，用 13G 余 25G。镜像 1.66G + 数据 0.57G ≈ 2.23G 是迁入增量。
- **⚠️ 无 swap**：`Swap: 0B 0B 0B`。内存打满是直接 OOM kill，不是变慢。评估容量时按此计。

### 1.2 操作系统与预装

```console
$ . /etc/os-release && echo "$PRETTY_NAME"
Ubuntu 24.04.4 LTS
$ docker --version && docker compose version --short && caddy version | head -1
Docker version 29.7.2, build a7dcaa6
5.4.0
v2.11.4 h1:XKxkMTgNSizEvKG6QHue6cAsFOteU2qA61w2tKkCWi0=
```

需预装：`docker`（含 compose 插件）、`caddy`、`rsync`、`git`。
`python3`（宿主 3.12.3）只有 fusion-memory 的 venv 用到，psi-agent 自己跑在容器里。

> **userns-remap**：`docker-compose.yml` 里三个服务都带 `userns_mode: "host"`。
> 这是为源端（214）开了 `userns-remap` 的 daemon 准备的 —— 不开该特性的机器上此项无副作用，保留即可。

### 1.3 外部凭据

只列键名与取值来源，**任何真实值都不进本文档、不进仓库、不进日志**。

| 键名 | 来源 |
|---|---|
| `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` | 飞书开放平台 → 应用凭证 |
| `PSI_AI_PROVIDER` / `PSI_AI_MODEL` / `PSI_AI_API_KEY` / `PSI_AI_BASE_URL` | AI provider 控制台 |
| `DASHSCOPE_API_KEY` | 阿里云百炼（embed proxy 用） |
| `SERPER_API_KEY` | serper.dev |
| `XFYUN_STT_*` / `XFYUN_TTS_*`（各 3 个键） | 讯飞开放平台 |
| `FUSION_MEMORY_TOKEN_PEPPER` / `FUSION_MEMORY_PG_DSN` | 自行生成 / 按 postgres 配置拼 |

### 1.4 DNS 与证书前置

回调子域必须先有 A 记录，**否则 Caddy 的 ACME 签不下来**。这是搬迁时的
BLOCKER-DNS，当时 `NXDOMAIN` 直接卡住 A3 验收。

```console
$ dig +noall +answer lark.oauth.genuineknowledge.cn
lark.oauth.genuineknowledge.cn.	9 IN	A	8.222.255.23
```

- A 记录指向服务器公网 IP。
- **80 端口必须可从公网入站**：Caddy 走 `http-01` 挑战。
- **TTL 建议 300s**。搬回境内要改指向，TTL 越低等待越短（当前实测剩余 9s，记录 TTL 已按 300 配）。
- DNS 一生效 Caddy 会自动重试签发，无需人工 reload。

---

## 2. 镜像获取

`psi-agent-gateway:local` **一个 tag 被三个容器共用** —— gateway、luolin、oauth-proxy：

```console
$ docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
NAMES                    IMAGE                                             STATUS
psi-agent-oauth-proxy    psi-agent-gateway:local                           Up 2 hours
psi-agent-gateway        psi-agent-gateway:local                           Up 2 hours
psi-agent-luolin         psi-agent-gateway:local                           Up 2 hours
fusion-memory-postgres   pgvector/pgvector:pg16                            Up 2 hours (healthy)
psi-cloud                psi-cloud:latest                                  Up 5 days (healthy)
psi-litellm              ghcr.io/berriai/litellm:v1.83.14-stable.patch.3   Up 5 days (healthy)
```

**后果**：重建这个 tag 会同时影响三个容器。只有 `gateway` 服务带 `build:` 段，
另两个直接引用 tag —— 所以 `docker compose build gateway` 之后，luolin 与 oauth-proxy
要在下次重启时才用上新镜像。

`pgvector/pgvector:pg16`（621MB）云端可直拉，不需要导入。

### 2.1 路线 A：本地 build → save/load 传输（**当前采用**）

搬迁实测走的是这条。原因见 2.3。

```bash
# 1) 在境内机器上 build
docker compose build gateway

# 2) 导出（实测 1.22G 镜像，gzip -1 约 20MB/s 级别的压缩开销）
docker save psi-agent-gateway:local | gzip -1 > /tmp/psi-agent-gateway-local.tar.gz

# 3) 传到目标机（两台服务器间无直连时经中转机；能直连则直传）
scp /tmp/psi-agent-gateway-local.tar.gz root@<目标机>:/tmp/

# 4) 目标机导入
gunzip -c /tmp/psi-agent-gateway-local.tar.gz | docker load
```

**验证镜像同一性要看 layer digest，不看 image ID** —— `docker save`/`load` 会重写
image config JSON，ID（= config 的 sha256）必然变化，`.Size` 统计口径也会差：

```console
$ docker inspect psi-agent-gateway:local --format "{{len .RootFS.Layers}} layers"
11 layers
$ docker inspect psi-agent-gateway:local --format "{{index .RootFS.Layers 0}}"
sha256:6f94328331290cbd81edab450664d42da7b64c191416c9346cd5d28c84f76035
```

11 层与源端一致即判定同一镜像。

### 2.2 路线 B：直接在目标机 build（**2026-09-10 起推荐**）

构建资产入库后，在目标机上从干净 clone 直接构建：

```bash
cd <干净 clone 目录>
deploy/haitun/build-image.sh full <commit>       # 依赖/前端变了用这个
deploy/haitun/build-image.sh overlay <commit>    # 只改了 src，秒级
```

境内 A 机 `47.100.84.197` 实测（2026-09-10，commit `64bac517`）：full **5m11s**，镜像 1.88 GB，
镜像内 `feishu-web/dist` 7.0 MB；overlay 秒级。

不再走 `docker compose build gateway` —— compose 的 `build:` 段指向目标机上那份不受版本控制的
`Dockerfile`，而 `build-image.sh` 用的是仓库里那份，两者不是同一个文件。

### 2.3 ⚠️ 镜像源在境内境外**结论相反**，所以它必须是 `ARG`

本节初版记的是境外机的坑（tuna 对境外 IP 返回 **403**，卡 7c367）：

```text
=> ERROR [gateway 2/7] RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' ...
2.150 E: Failed to fetch http://mirrors.tuna.tsinghua.edu.cn/debian/dists/trixie/InRelease
        403  Forbidden [IP: 101.6.15.130 80]
```

**搬回境内后这些数字全部反转，2026-09-10 在 A 机重量过**：

| 源 | trixie InRelease | `simple/aiohttp/`（3.95 MB 索引） |
|---|---|---|
| `mirrors.aliyun.com` | 200 · 3.58 MB/s · 0.039s | 200 · 14.5 MB/s · 0.235s |
| `mirrors.cloud.aliyuncs.com` | 200 · 2.79 MB/s · 0.050s | **000**（该机无此 pypi 路径） |
| `deb.debian.org` / `pypi.org` | 200 · 126 KB/s · 1.112s | 200 · **33 KB/s，120s 未下完** |
| `mirrors.tuna.tsinghua.edu.cn` | 200 · 95 KB/s · 1.483s | 200 · 1.57 MB/s · 2.168s |

对照境外 B 机（2026-09-01 实测）：`deb.debian.org` 比 aliyun **快 146 倍**（20.2 MB/s vs
138 KB/s）、tuna 403、`pypi.org` 2.70s 优于 tuna 3.72s。

`pypi.org` 那一格值得单独说：境内它**不是 404、不是超时**，而是 200 之后以 33 KB/s 涓流，单个
索引 120 秒下不完。这种「通但等于不通」最难归因 —— build 卡在装依赖那层，报错长得像网络抽风。

**当前做法**：三个源都提成 `ARG`（`APT_MIRROR` / `PIP_INDEX_URL` / `NPM_REGISTRY`），默认值按
**境内**取，境外构建显式覆盖：

```bash
APT_MIRROR= PIP_INDEX_URL=https://pypi.org/simple \
NPM_REGISTRY=https://registry.npmjs.org \
deploy/haitun/build-image.sh full <commit>
```

`APT_MIRROR=` 是**空值，表示不换源**，与「没设」不是一回事（`build-image.sh` 用 `${VAR+x}` 区分）。
`Dockerfile` 里那句 `if [ -n "${APT_MIRROR}" ]` 是配套的：少了它，空值会把 sources 里的域名 sed
成空串，`apt-get update` 报一个语法层面的怪错。判据见 `test_apt_mirror_empty_value_means_no_rewrite`。

基础镜像同理是 `ARG BASE_IMAGE`，默认仍走 DaoCloud 加速器 —— A 机实测
`registry-1.docker.io` 直连 **15 秒无响应**（curl 000），daocloud 与 daemon 里配的
`registry-mirrors` 都能拉。

> 本节初版的「长期建议」（把镜像源提成 ARG）已落地。当时那句「默认留空走官方源」按现在的实测
> 是错的：境内留空 = 走 `pypi.org` = 实质不可用。

### 2.4 前端产物由构建阶段产出，不再手工

`feishu-web/dist/` 被 gitignore 排除，而 `_routes.py` 的 `add_static` 在目录不存在时**静默跳过**
—— 页面 404、日志只有一行 INFO、容器状态一切正常。此前靠镜像外手工 `npm run build` 再叠一层
`Dockerfile.fw`（B 机 `/srv/haitun/build-34c73c65/Dockerfile.fw` 就是那一层），纯人工步骤。

现在 `Dockerfile` 阶段 1 用 `npm ci` + `npm run build` 产出，阶段 2 `COPY --from` 取过来，且在
构建期 `test -f .../dist/index.html` 自检。**overlay 不跑 npm**，它的 dist 完全来自基础镜像 ——
拿 9-10 之前的 `psi-agent-gateway:local` 叠会在构建期直接失败并打印补救办法（实测过），不会
静默产出一个没有前端的镜像。

---

## 3. 编排与目录布局

### 3.1 目录

```text
/srv/haitun/
├── psi-agent/
│   ├── docker-compose.yml      三服务编排（不在 git，见 0 节）
│   │                           ⚠️ Dockerfile 已挪进仓库 deploy/haitun/，此处不再有
│   ├── launch-gateway.sh       gateway 容器入口：双进程
│   ├── oauth-proxy.py          白名单反代
│   ├── config.yml              agent 配置
│   ├── restart-stack.sh        重启栈（顺序正确）
│   ├── workspace/              主 workspace，bind mount 进 gateway
│   │   ├── .env                0600
│   │   └── .psi/appdata/       histories / state / todos / auth.enc.json
│   └── workspace-luolin/       罗霖专用，独立 workspace
│       └── .env                0600
├── fusion-memory/
│   └── deploy/docker-compose.postgres.yml
├── fm-secrets/.config/fusion-memory/{mcp.env,embed-proxy.env}   0600
├── caddy/lark.oauth.caddyfile  vhost 源文件
└── docs/                       交付文档（不进 git）
```

### 3.2 三个服务

| 服务 | 容器名 | 网络 | 卷 |
|---|---|---|---|
| `gateway` | `psi-agent-gateway` | 发布 `127.0.0.1:8090` | `./workspace:/workspace` |
| `private-luolin` | `psi-agent-luolin` | 仅 compose 网内 `8081` | `./workspace-luolin:/workspace` |
| `oauth-proxy` | `psi-agent-oauth-proxy` | `network_mode: "service:gateway"` | `oauth-proxy.py:ro` |

**容器名 `psi-agent-luolin` 不可改** —— `PSI_FEISHU_EXTERNAL_SESSIONS` 按容器 DNS 名硬绑定。

gateway 用 `launch-gateway.sh` 起**双进程**：`psi-agent gateway`（监听容器内
`127.0.0.1:8080`）+ `psi-agent channel feishu --gateway-url`，后者实现按 open_id 独立 session。
Linux 下 `--listen` 必须带 `http://` 前缀，裸地址会被当成 unix socket。

同一飞书 app **只能维持一条 WebSocket**，所以 WS 只留主容器，luolin 不连飞书：

```console
$ docker logs psi-agent-gateway 2>&1 | grep -ciE "connected to wss://"
1
```

### 3.3 ⚠️ oauth-proxy 共享网络命名空间的后果（运维踩过）

`oauth-proxy` 用 `network_mode: "service:gateway"` 借用 gateway 的 netns。好处是它的
上游 `127.0.0.1:8080` 就是 gateway，gateway 不必改成监听 `0.0.0.0`。代价是：

> **重启或重建 gateway 会静默打断 oauth-proxy 的网络栈。容器状态仍显示 `Up`，
> 但 8090 不通（`curl` 返回 `000`）。**

因为共享 netns 即共享端口空间，这也是 proxy 监听 8090 而非 8080 的原因（用 8080 会撞）。

**所以永远不要裸 `docker compose restart gateway`**，用 `./restart-stack.sh`
（它在动过 gateway 后自动跟一句 `docker compose restart oauth-proxy`），
或手工补那一句。

### 3.4 fusion-memory

**postgres**（`/srv/haitun/fusion-memory/deploy/docker-compose.postgres.yml`）：

```bash
cd /srv/haitun/fusion-memory/deploy
docker compose -f docker-compose.postgres.yml up -d
```

镜像 `pgvector/pgvector:pg16`，卷 `fusion_memory_pgdata`，带
`pg_isready` healthcheck。**端口必须绑回环**：

```console
$ docker inspect fusion-memory-postgres --format "{{json .HostConfig.PortBindings}}"
{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5432"}]}
```

> **与代码不符之处**：同目录下 `docker-compose.postgres.yml.orig-from-haitun1`
> 第 11 行是 `"5432:5432"`（监听所有网卡）。**现网跑的是已改好的 `127.0.0.1:5432:5432`**。
> 别误用 `.orig-from-haitun1` 那份，会把数据库暴露到公网。

**MCP 与 embed proxy 是宿主 systemd 服务**，不在 docker 里：

```bash
systemctl enable --now fusion-memory-embed-proxy fusion-memory-mcp
```

> **⚠️ 现网这两个单元是 `disabled` 状态 —— 进程在跑，但重启机器不会自动拉起：**
> ```console
> $ systemctl list-unit-files "fusion-memory*" --no-legend --no-pager
> fusion-memory-embed-proxy.service disabled enabled
> fusion-memory-mcp.service         disabled enabled
> ```
> （第一列 `disabled` 是开机自启状态，第二列是 vendor preset。）
> 部署新机器时务必带 `enable`，别只 `start`。这是现网的一处待收口项，
> 本卡只写文档未改服务器配置。

- `fusion-memory-mcp`：`EnvironmentFile=/root/.config/fusion-memory/mcp.env`，
  `ExecStart=.../fusion-memory mcp-server --host ${FUSION_MEMORY_MCP_HOST} --port ${FUSION_MEMORY_MCP_PORT} --path /mcp`。
  `After=` 依赖 embed-proxy 与 docker。
- `fusion-memory-embed-proxy`：DashScope 前置代理，`EnvironmentFile=.../embed-proxy.env`。

MCP 监听在 compose 网络的桥接地址上，容器侧才能连到：

```console
$ ss -lntp | grep 8700
LISTEN 0  2048  172.19.0.1:8700  0.0.0.0:*  users:(("fusion-memory",pid=804807,fd=7))
```

---

## 4. 配置项清单

四个活跃 `.env`，**权限必须 0600**。只写键名与取值来源。

### 4.1 `workspace/.env`（主容器，21 个键）

```text
PSI_FEISHU_APP_ID                          PSI_PRIVATE_OPEN_IDS
PSI_FEISHU_APP_SECRET                      PSI_FEISHU_EXTERNAL_SESSIONS   ← 环境相关
PSI_AI_PROVIDER                            PSI_OAUTH_CALLBACK_BASE        ← 环境相关
PSI_AI_MODEL                               FUSION_MEMORY_ORGANIZATION_ID
PSI_AI_API_KEY                             FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID
PSI_AI_BASE_URL                            PSI_APPDATA
FUSION_MEMORY_MCP_URL          ← 环境相关   SERPER_API_KEY
FUSION_MEMORY_TOKEN_MAP_FILE               XFYUN_STT_APP_ID / _API_KEY / _API_SECRET
                                           XFYUN_TTS_APP_ID / _API_KEY / _API_SECRET
```

### 4.2 `workspace-luolin/.env`（18 个键）

同上，去掉 `PSI_PRIVATE_OPEN_IDS`、`PSI_FEISHU_EXTERNAL_SESSIONS`、`PSI_APPDATA`。
**仍需飞书凭据**（`PSI_FEISHU_APP_ID` / `_SECRET`）—— 它虽不连 WS，但工具侧要调飞书 API。

### 4.3 `fm-secrets/.config/fusion-memory/mcp.env`

```text
FUSION_MEMORY_PG_DSN               ← 环境相关（指向 postgres）
FUSION_MEMORY_TOKEN_PEPPER
FUSION_MEMORY_MCP_HOST             ← 环境相关（docker 桥接地址）
FUSION_MEMORY_MCP_PORT
FUSION_MEMORY_MCP_PUBLIC_URL       ← 环境相关
FUSION_MEMORY_MCP_SESSION_IDLE_SECONDS
FUSION_MEMORY_EMBEDDING_PROVIDER / _ENDPOINT / _MODEL / _DIMENSION
FUSION_MEMORY_EMBEDDING_ENCODING_FORMAT / _TIMEOUT_SECONDS
FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID / _APP_ID / _APP_SECRET
FUSION_MEMORY_ORGANIZATION_ID
```

### 4.4 `fm-secrets/.config/fusion-memory/embed-proxy.env`

```text
DASHSCOPE_API_KEY    EMBED_PROXY_HOST    EMBED_PROXY_PORT
EMBED_PROXY_UPSTREAM EMBED_PROXY_TIMEOUT
```

### 4.5 换机器时要改的环境相关值

搬迁演练实测：**不改代码，只改 5 个 `.env` 值**即可复现全栈。

| 键 | 为什么随机器变 |
|---|---|
| `PSI_OAUTH_CALLBACK_BASE` | 公网回调域名。必须是**浏览器可达的公网 HTTPS**，内网 IP 会让回调永远到不了取件箱 |
| `FUSION_MEMORY_MCP_URL` | **docker 桥接地址在不同宿主上不同**。现网是 `http://172.19.0.1:8700` —— 是 compose 网络（`psi-agent_default`, `172.19.0.0/16`）的网关，**不是** `docker0` 的 `172.17.0.1` |
| `FUSION_MEMORY_MCP_HOST` | 同上，MCP 要监听在那个桥接地址 |
| `FUSION_MEMORY_MCP_PUBLIC_URL` | 同上 |
| `FUSION_MEMORY_PG_DSN` | postgres 可达地址 |

查当前宿主的 compose 网关：

```console
$ docker network inspect psi-agent_default --format "{{range .IPAM.Config}}{{.Gateway}} {{.Subnet}}{{end}}"
172.19.0.1 172.19.0.0/16
```

### 4.6 权限

```bash
chmod 600 /srv/haitun/psi-agent/workspace/.env \
          /srv/haitun/psi-agent/workspace-luolin/.env \
          /srv/haitun/fm-secrets/.config/fusion-memory/{mcp.env,embed-proxy.env}
```

搬迁 A9 实测四个活跃 `.env` 均 0600。**遗留待收口**：`workspace/.env.bak-*` 9 个是 `664`，
`workspace/.bak-memory-deploy/.env` 是 `644` —— 非活跃但含历史密钥，建议一并 `chmod 600`。

---

## 5. 反向代理（Caddy）

### 5.1 用 import 隔离，不动主 Caddyfile

主 `Caddyfile` 只加一行 import，ToB 的 vhost 全部落在独立目录：

```console
$ grep -n "import" /etc/caddy/Caddyfile
46:import /etc/caddy/tob.d/*.caddyfile
```

这样 ToC 的 `account.` vhost 与本栈零交集。搬迁 A5 实测：主 Caddyfile 仅增这一行 import，
`psi-cloud` / `psi-litellm` 的 `RestartCount=0`。

> **⚠️ `/etc/caddy/tob.d/lark.oauth.caddyfile` 是 `/srv/haitun/caddy/` 那份的副本，不是符号链接：**
> ```console
> $ readlink -f /etc/caddy/tob.d/lark.oauth.caddyfile
> /etc/caddy/tob.d/lark.oauth.caddyfile
> $ diff /srv/haitun/caddy/lark.oauth.caddyfile /etc/caddy/tob.d/lark.oauth.caddyfile && echo IDENTICAL
> IDENTICAL
> ```
> 改了 `/srv/haitun/caddy/` 那份必须手工 `cp` 过去，否则改动不生效。

vhost 要点（全文见 `/srv/haitun/caddy/lark.oauth.caddyfile`）：

- `reverse_proxy 127.0.0.1:8090` → oauth-proxy。
- **不在 Caddy 里按路径分叉**：放行清单归 `oauth-proxy.py` 的 `ALLOWED_PATHS`，
  两处各写一份会漂移。
- 安全头照 `account.` 规格：HSTS / nosniff / X-Frame-Options DENY / Referrer-Policy。
- 日志独立文件，不与 ToC 混写。
- 另有 `http://` 块显式 301 跳 HTTPS（比主文件的 `:80` catch-all 更具体，优先命中）。

### 5.2 只 reload 不 restart

`restart` 会中断 ToC 的在线连接（同一个 Caddy 进程同时服务 `account.`）。改配置后：

```bash
caddy validate --config /etc/caddy/Caddyfile   # 先验证
systemctl reload caddy                          # 再热载
```

```console
$ caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -1
Valid configuration
```

### 5.3 ACME http-01 前置条件

- A 记录已生效（`NXDOMAIN` 会让签发失败，报
  `DNS problem: NXDOMAIN looking up A for ...`）。
- **80 端口公网可达**。
- 签发成功后：

```console
$ echo | openssl s_client -connect lark.oauth.genuineknowledge.cn:443 \
    -servername lark.oauth.genuineknowledge.cn 2>/dev/null | \
    openssl x509 -noout -subject -issuer -dates
subject=CN = lark.oauth.genuineknowledge.cn
issuer=C = US, O = Let's Encrypt, CN = YE1
notBefore=Aug 20 06:39:16 2026 GMT
notAfter=Nov 18 06:39:15 2026 GMT
```

### 5.4 为什么只有两条路径对外

**gateway 没有跨用户鉴权，却能驱动 agent 执行工具。** `/sessions`、`/chat/completions`
拿到就能让 agent 跑任意工具，绝不能暴露。所以：

- gateway 的 SPA 与 API 只在容器内 `127.0.0.1:8080`，不发布。
- 宿主只发布 `127.0.0.1:8090`（oauth-proxy），公网入口唯独 Caddy。
- oauth-proxy 是**白名单**反代，`oauth-proxy.py:24`：

```python
ALLOWED_PATHS = frozenset({"/oauth/callback", "/oauth/code"})
# 只转发 OAuth 流程真正用到的查询参数, 其余丢弃。
ALLOWED_QUERY = frozenset({"code", "state", "error", "error_description"})
```

不在清单内的路径一律 404（`oauth-proxy.py:35-36`），查询参数也做白名单过滤。

---

## 6. 启动顺序与验证判据

### 6.1 顺序

```bash
# 1) postgres 先起（MCP 依赖它）
cd /srv/haitun/fusion-memory/deploy
docker compose -f docker-compose.postgres.yml up -d

# 2) 宿主 systemd：embed-proxy → mcp
systemctl enable --now fusion-memory-embed-proxy fusion-memory-mcp

# 3) psi-agent 三容器
cd /srv/haitun/psi-agent
docker compose up -d

# 4) Caddy 热载
systemctl reload caddy
```

先验证编排语法（`-q` 无输出即通过）：

```console
$ cd /srv/haitun/psi-agent && docker compose config -q && echo "psi-agent compose: valid"
psi-agent compose: valid
$ cd /srv/haitun/fusion-memory/deploy && docker compose -f docker-compose.postgres.yml config -q && echo "postgres compose: valid"
postgres compose: valid
```

### 6.2 逐项验证

**① 容器与进程存活**

```console
$ docker ps --format "table {{.Names}}\t{{.Status}}"
NAMES                    STATUS
psi-agent-oauth-proxy    Up 2 hours
psi-agent-gateway        Up 2 hours
psi-agent-luolin         Up 2 hours
fusion-memory-postgres   Up 2 hours (healthy)
```

**② postgres 与 pgvector**

```console
$ docker exec fusion-memory-postgres psql -U fusion -d fusion_memory -tAc \
    "select count(*) from information_schema.tables where table_schema='public'"
33
$ docker exec fusion-memory-postgres psql -U fusion -d fusion_memory -tAc \
    "select extname from pg_extension where extname='vector'"
vector
```

期望 33 表 + `vector` 扩展存在。

**③ MCP 监听在 compose 桥接地址**

```console
$ ss -lntp | grep 8700
LISTEN 0  2048  172.19.0.1:8700  0.0.0.0:*  users:(("fusion-memory",pid=804807,fd=7))
```

**④ 飞书长连接已建立**

```console
$ docker logs psi-agent-gateway 2>&1 | grep -ciE "connected to wss://"
1
$ docker logs psi-agent-gateway 2>&1 | grep -oE "open_id=ou_[a-z0-9]{8}" | head -1
open_id=ou_1c926abf
```

必须**恰好 1 条** WS。多于 1 条意味着双端在线 → 消息重复投递。

**⑤ 公网只放行两条路径**（核心安全判据）

```console
$ for p in /oauth/callback /oauth/code /sessions /chat/completions / /health; do
    printf "%-20s -> %s\n" "$p" \
      "$(curl -s -o /dev/null -w '%{http_code}' -m 8 https://lark.oauth.genuineknowledge.cn$p)"
  done
/oauth/callback      -> 400
/oauth/code          -> 400
/sessions            -> 404
/chat/completions    -> 404
/                    -> 404
/health              -> 404
```

判据：

- `/oauth/callback` 与 `/oauth/code` → **400**（放行了，缺 code/state 参数报错）。
- 其余全部 → **404**（被白名单拦下）。
- **`/sessions` 返回 404 才表示 gateway 未暴露。**返回 200 是严重安全事故。

**⑥ 8090 不监听公网**

```console
$ ss -lntp | grep 8090
LISTEN 0  4096  127.0.0.1:8090  0.0.0.0:*  users:(("docker-proxy",pid=814968,fd=8))
$ curl -s -o /dev/null -w "public 8090 -> %{http_code}\n" -m 8 http://8.222.255.23:8090/sessions
public 8090 -> 000
```

必须只有 `127.0.0.1`，不含 `0.0.0.0`。从公网访问应 `000`（连不上）。

**⑦ gateway → luolin 容器网络连通**

```console
$ docker exec psi-agent-gateway curl -s -o /dev/null \
    -w "luolin:8081 -> %{http_code}\n" -m 8 http://psi-agent-luolin:8081/
luolin:8081 -> 404
```

> **404 是正常的** —— 它证明容器 DNS 解析 + TCP + HTTP 三层都通，只是根路径无路由。
> **`000` 或超时才是故障。** 这条判据反直觉，别当 bug 修。

**⑧ 一键自检**

`./restart-stack.sh` 内置 ⑤ 的两条判据：轮询 `/oauth/callback` 到 400（最多 90s，
gateway 冷启要装 channel_events / 触发器 / 工具表，云端实测 20–40s 才应答），
再确认 `/sessions` 为 404，任一不符即 `exit 1`。

从别处探测时可覆盖基址：`HEALTH_BASE=http://x.x.x.x:8090 ./restart-stack.sh`。

---

## 7. 数据迁移与回滚

### 7.1 ⚠️ 顺序必须是「停旧 → 拷数据 → 起新」

**飞书 channel 是外发 WebSocket 长连接，同一 app 只能有一条。两端同时在线会导致
消息重复投递。** 所以两端绝不能有重叠时段。

搬迁实测：源端 WS 18:27 断，云端 18:33 建连，无重叠期。

```bash
# 1) 停源端（只 stop，不 down / 不 rm / 不 prune / 不 down -v）
docker compose stop           # 源端 psi-agent 栈
# 确认源端 8090/8700 已无监听

# 2) 拷数据（见 7.2 / 7.3）

# 3) 起新端
docker compose up -d
```

停机后确认源端无监听，再动数据。

### 7.2 pg_dump 逻辑备份优先于卷 tar

逻辑备份跨 PG 小版本安全、体积小（实测 dump 7.1MB vs 卷 tar 31MB）：

```console
$ docker exec fusion-memory-postgres pg_dump -U fusion -d fusion_memory -Fc -f /tmp/probe.dump
$ docker exec fusion-memory-postgres ls -la /tmp/probe.dump
-rw-r--r-- 1 root root 7482187 Aug 20 12:43 /tmp/probe.dump
```

（上面是本文档写作时的实跑验证，probe 文件已删除。）

恢复：

```bash
docker exec -i fusion-memory-postgres pg_restore -U fusion -d fusion_memory --clean --if-exists < fusion_memory.dump
```

卷 tar 只作为第二重保险 —— 打包 `pgvector/pgvector:pg16` 卷时注意：源端曾因
`lookup registry-1.docker.io: no such host` 拉不到 helper 镜像，改用本地已有镜像才成。

### 7.3 workspace 用 rsync，两个坑

`.psi/appdata/` 下有 root 属主目录（`feishu-card-snapshots` 等），以普通用户跑 rsync
读不到，会报 `Permission denied` 并**静默放弃 `--delete`**：

```text
rsync: [sender] opendir ".../feishu-card-snapshots" failed: Permission denied (13)
IO error encountered -- skipping file deletion
```

危险的不是报错，是 `skipping file deletion` —— 「与源端一致」的保证没了却仍返回数据。
改用 `sudo` 后又撞第二个坑：**`sudo` 会清掉 `SSH_AUTH_SOCK`**，转发的 agent 丢失，
目标机 `Permission denied (publickey)`。

### 7.4 ⚠️ 同步脚本要在中转机上跑

`sync-data.sh` 的设计是：从**中转机** `ssh` 到源端，再由源端直连目标机。
源端地址是内网 `192.168.63.174`，**目标机到源端根本不通**。

在目标机上跑会得到**假的成功**：脚本把 ssh 报错接进 `grep -iE "统计行|^rsync:|^ERROR"`，
而 ssh 的错误是小写 `ssh: connect to host ...` 开头，两个 grep 都不匹配，
过滤后一行不剩，管道末端 `sed` 成功退出 → 函数返回 0 → 打印「同步完成」。
实际一个文件都没传（源端 166M/4150 文件 vs 目标端 32M/1139 文件）。

**静默成功是最坏的失败形态。** 脚本已补两条硬判据：退出码非 0，或没打出
`Number of regular files transferred` 统计块，一律 `exit 1`；
`skipping file deletion` 也提成硬失败。

传输路径本身可以直连（源端→目标机实测 50MB/8.1s ≈ 50Mbps，比经中转机快约 30 倍），
用 `ssh -A` agent 转发即可，源端不落私钥。**「路径直连」与「脚本在中转机上调用」两件事不矛盾。**

### 7.5 完整性核对

```console
$ find /srv/haitun/psi-agent/workspace/.psi/appdata -name "*.jsonl" | wc -l
259
```

搬迁实测 259 个 jsonl / 89444 行 0 损坏。回填后应逐字节核对总字节数与文件数
（实测源端与目标端均 164164541 字节 / 4150 文件）。

### 7.6 回滚

源端**只 stop，不删任何东西**，容器、卷、备份、systemd 单元全部原地保留，
随时 `docker start` 拉回。异地备份实测 280M：

```text
~/haitun-migration-backup/20260820/
  SHA256SUMS.txt / appdata.tar.gz 20M / fusion_memory.dump.gz 6.9M
  pgdata.tar.gz 31M / workspace-luolin.tar.gz 124M / workspace.tar.gz 100M
```

四个 tar.gz 用 `tar -tzf` 验证可列表（不解包）。目标机侧有 `rollback.sh`。

**回滚同样受 7.1 约束**：拉回源端前必须先停目标机的飞书 channel。

---

## 8. 常见故障与排查

### 8.1 8090 返回 000，但容器显示 Up

**共享 netns 被打断**（3.3）。gateway 被重启/重建后 oauth-proxy 网络栈失效。

```bash
docker compose restart oauth-proxy    # 或直接 ./restart-stack.sh
```

### 8.2 `Exited (137)`

137 = 128 + 9 = **SIGKILL**。两种来源，必须区分：

```console
$ docker inspect psi-agent-gateway --format "{{.Name}} OOMKilled={{.State.OOMKilled}} restarts={{.RestartCount}}"
/psi-agent-gateway OOMKilled=false restarts=0
```

- `OOMKilled=false`：`docker stop` 优雅期超时后被强杀。搬迁时源端两个容器就是这样，
  **属正常停机，不是故障**。gateway 有常驻子进程，10 秒优雅期内退不干净。
- `OOMKilled=true`：真 OOM。**本机无 swap**，内存打满直接被杀而非变慢。

### 8.3 工具裸 import 加载失败

```text
ModuleNotFoundError("No module named '_runtime_paths'")      <- find_files.py
ModuleNotFoundError("No module named '_assignment_delivery'") <- assignment_send_card.py
```

`rsync` 重建 `tools/` 后目录项顺序变了，`find_files.py` / `assignment_send_card.py`
落到 glob 首位，其**裸 import** 在任何兄弟文件把 `tools/` 插进 `sys.path` 之前执行。
源端靠目录序侥幸成立（它们排在 43/68 位）。

修法是在 compose 里显式声明搜索路径，消除这个偶然依赖：

```yaml
environment:
  - PYTHONPATH=/workspace/tools
```

两个服务都要加。**这两个文件本身仍是脆的** —— 换成相对 import 才是根治。

### 8.4 `restart-stack.sh` 硬编码 IP

原脚本自检基址硬编码源端内网 `http://192.168.63.174:8090`，搬到新机器必然连不上并
`exit 1`，连基本重启都断。已改为可覆盖变量，默认走回环：

```bash
HEALTH_BASE=${HEALTH_BASE:-http://127.0.0.1:8090}
```

**换机器时全仓搜一遍硬编码地址。**

### 8.5 云端 build 失败 403

见 2.3，清华源拒境外 IP。改走导入路线。

### 8.6 证书签不下来

`NXDOMAIN` → 先加 DNS A 记录，等生效。Caddy 会自动重试，无需人工 reload。
也要确认 80 端口公网可入站。

### 8.7 授权回调跳不过去 / 要求手工贴 code

`PSI_OAUTH_CALLBACK_BASE` 是内网地址。用真实模块求值确认，而非读 `.env` 字面值：

```bash
docker exec psi-agent-gateway python3 -c \
  "import sys; sys.path.insert(0,'/workspace/tools'); \
   import _oauth_receiver as r; print(r.gateway_redirect_uri(), r.is_private_callback())"
```

`is_private_callback=False` 才表示不走「手工贴 code」的降级分支。

另需确认飞书开放平台后台登记的 redirect_uri 与 `PSI_OAUTH_CALLBACK_BASE + /oauth/callback`
**逐字符一致**（尾斜杠、http/https、大小写），不一致会报 **20071**。

---

## 附录 A：不在 git 里的编排文件

第一次部署到全新机器时，这批文件需从既有目标机 `/srv/haitun/psi-agent/` 拷贝：

> 🔵 **2026-09-10：`Dockerfile` 已从这张表移出** —— 它现在在仓库 `deploy/haitun/Dockerfile`，
> 随 clone 一起到位，不需要拷。把它留在这张表里正是搬机时漏搬构建目录的成因之一。

| 文件 | 作用 |
|---|---|
| `docker-compose.yml` | 三服务编排 |
| `launch-gateway.sh` | gateway 双进程入口。内含 `AI_ID` 与 `FALLBACK_SOCK` 常量 |
| `oauth-proxy.py` | 白名单反代 |
| `config.yml` | agent 配置 |
| `restart-stack.sh` | 按正确顺序重启 + 自检 |
| `workspace/`、`workspace-luolin/` | 含 `.env`、`skills/`、`tools/`、`.psi/appdata/` |

fusion-memory 侧（该仓库自带 `deploy/`，但落地位置与内容有偏移，逐项核对）：

| 项 | 位置 |
|---|---|
| postgres 编排 | `deploy/docker-compose.postgres.yml`（**用这份，不用 `.orig-from-haitun1`**，见 3.4） |
| systemd 单元 | 实际生效的是 `/etc/systemd/system/fusion-memory-{mcp,embed-proxy}.service` 两个。仓库 `deploy/systemd/` 下有 6 个模板（含 `embedding@` / `reranker@` / `health.timer` / `history-sync@`），**本栈只用到 mcp 与 embed-proxy** |
| embed proxy 脚本 | `/root/fusion-memory-embed-proxy.py` —— 不在 `deploy/` 里，单元的 `ExecStart` 直接指向此路径，换机器时要一并拷 |
| 密钥 | `fm-secrets/.config/fusion-memory/{mcp.env,embed-proxy.env}`，0600 |

## 附录 B：证据来源

- 云端 `/srv/haitun/docs/migration-log.md`（2318 行）—— 搬迁施工记录
- `docs/superpowers/specs/2026-08-19-tob-web-gateway-design.md` 的 T 段 —— A1–A9 验收结论。
  **注意：该 spec 尚未合入 `main`**，当前只存在于 kanban checkpoint 提交里
  （`git show 4b0bf92c:docs/superpowers/specs/2026-08-19-tob-web-gateway-design.md`）。
- 现网实跑：本文所有 `console` 块，采样于 2026-08-20 20:38–20:45 CST

阶段 3 验收状态：**A3–A9 七条实证通过；A1 实证通过**（「不重复投递」子项因停机窗口内
0 条入站而无样本可判）；**A2 配置侧实证通过**，端到端外网授权流由阶段 2.8 真人浏览器回调证实。
遗留待人工确认项见 spec 的「待负责人人工确认清单」。
