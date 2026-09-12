# ToB 搬迁 · 操作日志

2026-09-09 起 · 执行人 zsd+Claude(会话 1)· 用途:逐条追溯每一次操作

> 每条记录格式:**时间(CST)| 机器 | 操作 | 命令要点 | 结果 | 可回滚性**。
> 只读探查也记,因为"当时量到什么"是后续归因的依据。
> B = 8.222.255.23(境外,现生产)· A = 47.100.84.197(境内,目标)
> 三台钟已核对同为 CST(12:11:51 本地 / 12:11:53 B / 12:11:55 A)。

## 负责人已拍的四个决定(9-09,授权持续推进)

| # | 决定 | 内容 |
|---|---|---|
| ① | 镜像统一到哪版 | **甲:统一到 `meeting-fix-main-a1bc44d9-20260907-1154`**(生产在跑、已验证,不引入新代码) |
| ② | 硬纪律例外 | **批准**,范围限死 `private-luolin` + `private-chengxx`,不碰 gateway |
| ③ | 停机窗 | **9-10 20:00**,按一小时发通知;全量在窗口前跑完,窗口只走增量 |
| ④ | A 机 gateway `mem_limit` | **3G → 4.5G**,保留 4G swap |

决定①选甲的直接后果(记下来以免后面找不到理由):**不需要**在 compose 补
`PSI_SEED_SCHEDULES_WORKSPACE` —— 那是选项乙(从 main 构建)才有的坑,
因为 main 把 meeting-session 改成由该变量驱动,而 `a1bc44d9` 还是硬编码。
选甲则生产现状不变,会议自动化不受影响。

## 关键路径

全量传输 2.39G @ 134 KB/s ≈ **5.2 小时**,是唯一的关键路径,必须最早启动。
其余工作(修 502、统一镜像、找主人实测)都能与传输**并行**,因为传输只吃带宽。

## 逐条操作记录

| 时间 | 机器 | 操作 | 结果 | 回滚 |
|---|---|---|---|---|
| 12:11 | 本地 | 三机时钟核对 | 均 CST,偏差 <4s | — |
| 12:1x | B | 只读:`cat restart-stack.sh` | 走 `docker compose restart`(**非 `up -d`**),不重建容器,符合硬纪律;自检轮询 8090 最多 90s 等 400,并校验 `/sessions` 必须 404 | — |
| 12:1x | B | 只读:量 wav 与 agent-video | wav **18 个 / 61.1MB**;`agent-video` 目录**不存在**(已被清或从未有) | — |

| 12:14 | A | **建目录**:`/srv/haitun/{psi-agent,fusion-memory,incoming}` + `content/{official,enterprise,users}` 三段 | 已建;A 磁盘 40G 用 5.8G(16%) | `rm -rf`(空目录,无风险) |
| **12:14** | B→A | **启动镜像传输**(关键路径)`docker save 三镜像 \| zstd -3 -T0 \| ssh cat >` | 后台跑,日志 `/root/xfer-images.log`;2.5min 传 16.5MB ≈ **110 KB/s**,与 134 KB/s 上限吻合 | 杀进程 + 删 A 上文件 |
| 12:1x | B | **修 502**:`./restart-stack.sh gateway` | **成功**,详见下节 | 无需(幂等) |
| 12:22 | B→A | **启动 workspace 传输** `tar 三 workspace \| zstd \| ssh cat >` | 后台跑,日志 `/root/xfer-ws.log` | 杀进程 + 删 A 上文件 |
| 12:2x | B | pgvector `pg_dump -Fc -Z0` + zstd | 140573810 → **43865141**(43.9M),2.09s | 删 /tmp 文件 |
| 12:2x | B | 打包 fusion-memory 仓库(排除 `.venv`) | **1679395**(1.7M) | 同上 |
| 12:2x | B | 打包配置(env/compose/Dockerfile/oauth-proxy/restart-stack) | **14889**(15K) | 同上 |
| 12:2x | B→A | 启动小件传输(cfg → repo → pgvector 依次) | 后台跑,日志 `/root/xfer-small.log` | 删 A 上文件 |

### 修 B 的 502(已完成)

`./restart-stack.sh gateway` —— 走 `docker compose restart`,**不是 `up -d`**,不重建容器,
所以不违反硬纪律;脚本会在动过 gateway 后自动跟着 `restart oauth-proxy`(共享 netns)。

判据(公网侧,才是真判据):

| 检查 | 结果 |
|---|---|
| `https://lark.oauth.../oauth/callback` | **400**(缺 state,预期值) |
| `https://lark.oauth.../` | 404 |
| `https://lark.oauth.../sessions` | **404**(白名单仍拦住 agent 路由) |
| gateway 与 oauth-proxy netns ID | **一致**(`5b84e881ab9d`) |
| 飞书 WS | **已重连** |

两点如实记账:

1. **实际等了约 70s 才应答**,脚本注释写"云端实测约 20-40s"偏乐观 ——
   轮询上限 90s 是必要的,固定 `sleep` 会误报。
2. `docker exec oauth-proxy ss -ltn | grep 8090` 显示 `NOT_VISIBLE`。
   这是**我的探针局限**而非故障:proxy 自己 bind 的是 18099,8090 是宿主侧 `docker-proxy`。
   权威判据是公网 400,已通过。

**此修复只解当次。** OOM 链(#6)会让 502 复发,根治要靠决定④的 `mem_limit`。

### 传输清单与排除项(排除项已做变异复核)

排除项第一版**没生效**,差点把 5 小时传输浪费在错清单上,记下来:

| 版本 | 模式 | `skills.pre-` 命中 | `tools.pre-` 命中 | 载荷 |
|---|---|---|---|---|
| 第一版 | `skills.pre-*/`(带尾斜杠) | **868** | **255** | 2611937280(2.61G) |
| 修正版 | `skills.pre-*`(无尾斜杠) | **0** | **0** | **2457077760(2.46G)** |

tar 的 `--exclude` **不匹配带尾斜杠的目录模式**,而它**不报错** ——
第一版只省下 97MB(全是 wav 与 bak),两个 42M/4.7M 快照目录照样在里面。
**判据必须是"在清单里数命中数",不是"看着像排除了"。**

正向核对(确认没排错东西,want 全部 >0):

```
workspace/tools/  227    workspace/skills/  394    workspace/systems/ 13
workspace/histories/ 31  workspace/.psi/  2403     workspace/schedules/ 15
workspace/.env    保留   .env.bak-*        已排除
workspace/.psi/memory_tokens.json  保留   ← 缺它会静默杀掉记忆
```

不搬:`skills.pre-*`(42M)、`tools.pre-*`(4.7M)、各类 `*.bak*`、18 个 `.wav`(61.1MB)、
部署目录 13 个备份文件(含 `src.bak-*.tar.gz` 792KB、`src.old-*`)。
`agent-video` 目录**已不存在**(记录说"已砍"属实)。

### fusion-memory 清单:记录里的凭据路径是错的

原记录写 `fm-secrets`。实测 B 上 **`/etc/fusion-memory/` 与 `/root/fm-secrets*` 都不存在**,
真实位置从 systemd unit 里查到:

| 件 | 真实路径 | 实测 |
|---|---|---|
| MCP unit env | `/root/.config/fusion-memory/mcp.env` | 27 行,16 个键(含 `FUSION_MEMORY_PG_DSN`、`TOKEN_PEPPER`、飞书 app 凭据) |
| embed-proxy env | `/root/.config/fusion-memory/embed-proxy.env` | 6 行(含 `DASHSCOPE_API_KEY`) |
| 仓库 | `/srv/haitun/fusion-memory` | 83M 含 `.venv`,**排除 .venv 只有 7.5M**;`git remote` **0 个** |
| embed proxy 脚本 | `/root/fusion-memory-embed-proxy.py` | 4612 字节 |
| token map | `workspace/.psi/memory_tokens.json` | 7059 字节,`-rw-------` |
| pgvector | 容器 `fusion-memory-postgres` | **33 表 / 183 MB** |

两条推论:

- 仓库**无 remote**,所以只能按字节搬、不能 clone ——
  **A 机 `git clone` 不稳这件事对 fusion-memory 不构成障碍。**
- `.venv` 不搬(`pyvenv.cfg` 写死 `/usr/bin/python3.12`,A 是 3.14.4),
  按决定走容器化;`python:3.12-slim` + 四依赖已在 A 实测通过。

| 12:26 | B | 备份 compose 到 `/root/`(**故意放部署目录外**,不会被搬走) | `docker-compose.yml.pre-imgunify-20260909-122618` | 即回滚源 |
| 12:26 | B | 改 compose 两行 `image:` → `meeting-fix`(决定①甲) | `docker compose config -q` 通过;gateway 与 oauth-proxy 行**未动** | 拷回备份 |
| **12:26** | B | **`up -d private-luolin private-chengxx`**(决定②例外) | 两容器 Recreated + Started;**gateway ID 前后一致**(`5b84e881ab9d`),oauth-proxy netns 未受影响 | 改回 `86d5f755` + `up -d` |
| 12:28 | B | 切换后核对基线 | 两容器 running / restarts=0 / ERROR=0(chengxx);**工具数 277→192、205→192**,已查明原因见下 | 同上 |

### 镜像统一(已完成)· 工具数下降是**旧镜像多报**,不是新镜像丢功能

切换前基线:

| 容器 | 镜像 | 工具 | 文件 | schedules | history | `/app` 漂移 |
|---|---|---|---|---|---|---|
| luolin | `86d5f755` | 277 | 98 | 8 | 14597 msg | **0** |
| chengxx | `86d5f755` | 205 | 98 | 1 | 7476 msg | **0** |

切换后:两个容器都变成 **192 tools / 98 files**。工具数掉了 85 和 13,
这正是判据该拦住的那种"静默降级",所以我没放过,查到底为止。

**结论:192 是对的,277/205 是旧镜像多报。** 定量证据(对同一份 98 个文件做 AST 统计):

```
public defs: 195        ← 真实存在的公开函数上界
  async: 192   sync: 3  ← 注册器只收 async
duplicated names: 0     ← 不是同名去重的效果
public imported names: 496   ← 旧镜像把 import 进来的公开名也算成了工具
```

**192 async + 3 sync = 195**,与新镜像报的 192 **精确吻合**。
而旧镜像报 **277 > 195(真实上界)**,它数的是"模块里所有公开名",
把 `from X import Y` 带进命名空间的名字也算进去了 —— 496 个可用于虚增的名字足够解释 277 与 205。

luolin 与 chengxx 的 `tools/` 目录 md5 **不同**,但切换后**都报 192** ——
与"数真实 async def"一致,与"数命名空间里的名字"不一致(后者会因目录不同而不同)。
这条是独立的交叉验证。

**因此:搬迁判据里私有容器的工具数,基线必须用切换后的 192,不能用 277/205。**
沿用旧数字会在 A 机得出"丢了 85 个工具"的错误结论。

功能面核对(不靠日志措辞猜):

| 检查 | 结果 |
|---|---|
| 两容器状态 | running,`RestartCount=0` |
| gateway → `psi-agent-luolin:8081` | **404**(HTTP 在应答,路由通) |
| `PSI_FEISHU_EXTERNAL_SESSIONS` 映射 | **两条都在**,未受重建影响 |
| 会话与历史 | 各自 session 正常起,history 继续增长(15071 / 7963 msg) |
| chengxx ERROR | **0** |
| luolin ERROR | 10 条,**全部是既有的 fusion-memory 崩溃**(#7),切换前就有、约 4 次/小时,**非本次引入** |

几个探针的假结论,记下来免得复用:

- `grep -qE "'toolname'"` 查工具是否存在 → 冷启后不再打印 refresh 列表,
  于是 `memory_search`/`bash` 全报 MISSING。**那是日志没打,不是工具没有。**
- session 没有 introspection 端点(`/tools`/`/status`/`/health` 全 404),
  所以"权威工具清单"只能靠启动日志那一行 + AST 统计交叉验证。

一个待办(A 机起栈时要处理):`up -d` 报
`network psi-agent_default exists but was not created by compose` ——
A 机上要按方案显式声明 `ipam.config.subnet`,正好避开这个模糊状态。

| 12:3x | A | **加 4G swap**:`fallocate` + `mkswap` + `swapon` + 写 `/etc/fstab` | `free` 显示 Swap **4.0Gi**,fstab 已持久化 | `swapoff /swapfile` + 删 fstab 行 |
| 12:3x | A | 解开配置包到 `/root/prep` | 7 个文件全到(compose/Dockerfile/oauth-proxy/restart-stack + 两个 fusion env + embed proxy) | 删目录 |
| 12:3x | B→A | `scp` 取**切换后**的 compose(包里那份是切换前的) | 已取,113 行,三处 image 都是 `meeting-fix` | — |
| 12:3x | A | 生成 `compose-A.yml`:mem_limit 4.5g + 去掉 `build:` 段 + 显式 `networks` | 已生成并放到 `/srv/haitun/psi-agent/docker-compose.yml` | 重新生成 |

### A 机 swap 与 swappiness

4G swap 已加并持久化。但要如实记一件事:**`vm.swappiness = 0`**,
来自阿里云出厂配置 `/etc/sysctl.d/99-apsara-sysctl.conf`,**A 和 B 都是 0**。

我**没有改它** —— 改云厂商的内核参数不在授权范围内。而且它对本案影响有限:
B 上那次 OOM 是 **`CONSTRAINT_MEMCG`**(cgroup 硬上限),
**swap 根本不解除 cgroup 限制**。所以真正的解药是决定④的 `mem_limit`,swap 只是机器级兜底。

### A 机 compose 的三处改造

| 改动 | 值 | 理由 |
|---|---|---|
| gateway `mem_limit` | `3g` → **`4500m`** | 决定④。B 上撞 3g 被杀 86 次(anon-rss 2.93G);A available 5.5G + 4G swap 有余量。私有容器的 `1g` **不动**(实测用量 468M/351M,够) |
| 去掉 `build:` 段 | 删 | A 用 `docker load` 来的镜像,不从源码构建。留着会让误敲 `up --build` 时去 A 机现场构建(而 A 的 `git clone` 不稳) |
| 新增 `networks:` | `name: psi-agent_default` + `ipam.config.subnet: 172.19.0.0/16` | 三份 `.env` 硬编码 `172.19.0.1:8700`,网关地址错了记忆**静默**失效。B 上这个网络**不是 compose 建的**(`up -d` 会警告 "exists but was not created by compose"),A 机从第一天就交给 compose 管并钉死 subnet |

`docker compose config -q` 目前报 `env_file .../workspace/.env not found` ——
**预期**,因为 workspace 还在传输中。三份 `.env` 都在各自 workspace 目录内
(部署目录**没有** `.env`),随 workspace 包一起到,落地后即可通过校验。

### 传输吞吐实测(三路并行)

| 时刻 | 累计已传 | 均速 |
|---|---|---|
| 12:25(11min) | ~78MB | ~118 KB/s |
| 12:37(23min) | ~157MB | **~114 KB/s** |

与 134 KB/s 的链路上限吻合,**三路并行只是在分同一条管子** —— 与"加并发无效"的既有实测一致。
按此速率 2.39G 需 **5~6 小时**,即负责人回来时仍在传,属预期,这正是它不能进停机窗的原因。

---

## 决定⑤(新增):A 机 psi-agent 网段改用 172.21.0.0/16

这是方案里**没有的一条**,是 9-09 在 A 机实测发现的硬冲突。

### 冲突事实(实测)

| 机器 | psi-cloud 网段 | psi-agent 网段 | eth0 VPC 网段 |
|---|---|---|---|
| B(现生产) | 172.18.0.0/16 | **172.19.0.0/16** | — |
| A(目标) | **172.19.0.0/16** ← 撞 | 尚未建 | **172.18.192.0/20** |

A 机 `psi-cloud_default` 正好占着 172.19.0.0/16,gw=172.19.0.1,
而 172.19.0.1:8700 正是三份 workspace `.env` 里硬编码的记忆服务地址。
**且 172.18 在 A 上也不能用** —— A 的 eth0 走 172.18.192.0/20,
建 172.18.0.0/16 的 docker 网络会覆盖主机自己的 VPC 路由,直接失联。

两机实测 **172.21.0.0/16 都空闲**(A 无任何 172.20/172.21 路由;B 的 172.20 被
`deploy_default` 即 pgvector 占用,172.21 空)。

### 为什么不是"删掉 A 的 psi-cloud 腾网段"

我先按方案的思路查了这条,结论是**不能删**:

- A 的 Caddy 里 `account.genuineknowledge.cn` 反代到 `127.0.0.1:8081` = psi-cloud 容器,
  而这个域名**已硬编码进已发布的 ToC 客户端**,属另一条产品线(会话 2 之外的第三方)。
- 改 psi-cloud 的网段需要 `docker compose up -d` 重建 —— 那是别人的栈,不在我授权范围。
- 实测 A 的两个生产域名 **当前都解析到 B**(`account` 与 `lark.oauth` 均 → 8.222.255.23),
  所以 A 的 psi-cloud 目前是**空跑的备用**(6 小时日志里只有 `/healthz` 200)。
  但"现在没流量"不等于"可以删",它是那个域名的将来落点。

**取舍**:动我自己要搬的这一侧(4 个文件、1 个值),而不是动别人的运行栈。

### 需同步改的 4 处(全部实测定位,无遗漏)

`grep -rn "172\.19\."` 在 B 机部署树 + fusion-memory 配置 + systemd unit 全量扫描,命中恰好 4 处:

| 文件 | 行 | 改法 |
|---|---|---|
| `workspace/.env` | 11 | `FUSION_MEMORY_MCP_URL=http://172.21.0.1:8700/mcp` |
| `workspace-luolin/.env` | 21 | 同上 |
| `workspace-chengxx/.env` | 19 | 同上 |
| `mcp.env` | 6 | `FUSION_MEMORY_MCP_HOST=172.21.0.1` |

**尚未改** —— 三份 `.env` 还在传输中,落地后随迁移脚本一起改,并逐一核 md5。

### 已完成的 compose 改动

`/srv/haitun/psi-agent/docker-compose.yml` 的 `networks.default.ipam.config.subnet`
已由 `172.19.0.0/16` 改为 `172.21.0.0/16`(122-125 行,含理由注释)。

核验:两份副本 md5 一致 `2fdca710...`;`mem_limit 4500m` 与三处 image 行未受影响;
残留的 2 处 `172.19` 字样**逐行确认均在注释内**,非配置项(判据:剔除注释行后 `NONE_OUTSIDE_COMMENTS`)。

---

## fusion-memory 容器化(任务 #10)

### 修正:postgres 本来就是容器,不需要"容器化"

实测 B 机 `fusion-memory-postgres` 已是容器(`pgvector/pgvector:pg16`,
compose 在 `/srv/haitun/fusion-memory/deploy/docker-compose.postgres.yml`,
且该 compose **在 git 里**)。真正跑在 host 上的 systemd 服务是**两个**:

| 服务 | ExecStart | 监听 |
|---|---|---|
| `fusion-memory-mcp.service` | `.venv/bin/fusion-memory mcp-server` | `172.19.0.1:8700` |
| `fusion-memory-embed-proxy.service` | `.venv/bin/python /root/fusion-memory-embed-proxy.py` | `127.0.0.1:8701` |

这两个才是搬不动的部分(venv 的 `pyvenv.cfg` 写死 `/usr/bin/python3.12`)。

### 为什么钉 3.12 而不用 A 的 3.14

`pyproject.toml` 写 `requires-python = ">=3.11"`,A 的 3.14.4 理论上够。
但 B 实测跑 **3.12.3**,而 A(Ubuntu 26.04)**仓库里没有 python3.12**
(`apt-cache policy python3.12` 无候选,`/usr/bin/` 只有 3.14)。
搬迁当天不叠加"换解释器"这个变量,故用 `python:3.12-slim` 镜像 —— 与 B 同版本。

依赖版本**钉到 B 机 venv 的实测值**而非 pyproject 的范围:
`aiohttp==3.14.3`、`anyio==4.14.2`、`mcp==1.29.0`、`psycopg2-binary==2.9.12`。
`pip install -e . --no-deps` 避免按范围重解版本。

### 待解决:容器内的 DSN 不能是 127.0.0.1

`mcp.env` 里 `FUSION_MEMORY_PG_DSN=postgresql://…@127.0.0.1:5432/fusion_memory`。
在 host systemd 下这指向 docker-proxy 映射的 pgvector;
**搬进容器后 127.0.0.1 会指向容器自己**,记忆库连不上。
落地时必须改为 pgvector 的容器名或网关地址,并实测连通,**尚未验证**。

### fm.dump 完整性(已核验)

| 判据 | 结果 |
|---|---|
| B 侧 md5 | `1b0b813a20f229406f33fe462a2b3bbd` |
| A 侧 md5 | `1b0b813a20f229406f33fe462a2b3bbd` ✅ 一致 |
| `zstd -t` | `ZSTD_INTEGRITY_OK`,解压后 140,573,810 字节 |

### 澄清:psi-litellm / psi-cloud 不在搬迁范围

B 机 7 容器里 `psi-litellm` 与 `psi-cloud` 的 compose 工作目录都是 `/srv/psi-cloud`
(网段 172.18),属 ToC 线;且三份 `.env` 的 `PSI_AI_BASE_URL` 直连
`https://api.deepseek.com/v1`,**不经 litellm**。故未纳入镜像传输,是正确的。

---

## 推翻:A 机"pip 已验证可用"是假通过(原任务 #8 的结论作废)

这条必须单独记,因为**我自己先前就是这么误判的**,而且误判方式很典型。

### 假通过是怎么来的

9-09 上午我在 A 机宿主上跑 `pip install`(以及 `python -c "import ..."`)
成功,据此记了"A 有构建能力"。实测复查发现:

| 判据 | 结果 |
|---|---|
| A 宿主 `pip install` | ✅ 成功 |
| A → `https://pypi.org/simple/aiohttp/` | ❌ **三次全超时**(`rc=124`) |
| A → `https://files.pythonhosted.org/` | ✅ 200 / 1.48s |
| A → `https://mirrors.aliyun.com/pypi/simple/` | ✅ 200 / **0.24s** |
| A → `https://pypi.tuna.tsinghua.edu.cn/simple/` | ✅ 200 / 1.37s |

宿主 pip 之所以能装,是因为 **`/root/.pip/pip.conf` 悄悄把 index-url 指向了
阿里云内网镜像** `http://mirrors.cloud.aliyuncs.com/pypi/simple/`(阿里云出厂配置)。
而 **容器不继承宿主的 pip 配置** —— `docker build` 老老实实去了 pypi.org,于是卡死。

三个可迁移的教训:

1. **在宿主上验证的能力,不等于容器里有这个能力。** pip 源、DNS、代理、证书都是这类。
2. **只测下载域名会得到假通过。** `files.pythonhosted.org` 通而 `pypi.org` 不通 ——
   两个域名分属不同 CDN,而 pip 必须先读 index 才能下载。
3. 第一次 build 卡了约 12 分钟我才去查,期间它**自己静默重试过一轮**
   (runc bundle 换了新 id)。"还在跑"看着像"在下载",实际是在超时重试。

### 修法与结果

Dockerfile 里显式 `ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`。
选公网 `mirrors.aliyun.com`(HTTPS)而不用 `mirrors.cloud.aliyuncs.com` ——
后者是 VPC 内网且只有 HTTP,换台机器就不可用。

换源后 build **`BUILD_EXIT=0`,pip 步骤 4.6s 完成**(此前 12 分钟未过),
反向坐实 pypi.org 是唯一阻塞点。

---

## fusion-memory 镜像三层核验(已完成)

镜像 `fusion-memory:migrate-20260909`,ID `51e5b7aef5cb`,314MB。

| 判据 | 期望(B 机实测) | A 机镜像内 | |
|---|---|---|---|
| Python | 3.12.3 | **3.12.14** | ✅ 同小版本 |
| `aiohttp` | 3.14.3 | 3.14.3 | ✅ |
| `anyio` | 4.14.2 | 4.14.2 | ✅ |
| `mcp` | 1.29.0 | 1.29.0 | ✅ |
| `psycopg2-binary` | 2.9.12 | 2.9.12 | ✅ |
| `fusion-memory` | 0.1.0 | 0.1.0 (`/app`) | ✅ |

功能性判据(不止"装上了"):

- `which fusion-memory fusion-memory-server` 两个入口点都解析到 `/usr/local/bin/`
- `fusion-memory mcp-server --help` **退出码 0** 且打出 `--host/--port/--path/--public-url`
  —— 证明 systemd unit 里那条命令行的四个参数在这个镜像里都还在
- `import psycopg2` → `PSYCOPG2_OK 2.9.12 (dt dec pq3 ext lo64)` —— libpq 已随 wheel 带进来,
  不需要额外装 `libpq-dev`
- embed-proxy 脚本 `py_compile` 通过

### pgvector 按 digest 拉取(不按 tag)

A 机原先**没有** pgvector 镜像。按 B 的 **digest** 拉取而非 `:pg16` tag:
`sha256:ccc6e83d...4d6b`,拉完再本地打上 `pgvector/pgvector:pg16`。
理由:tag 会漂,digest 不会 —— 这样 A 的库引擎与 B **字节一致**。
B 库实测 `server_version=16.15`、`vector 0.8.6`、public schema **33 张表**。

`fm.dump.zst` 是 **`PGDMP` 自定义格式**(od 验证首字节),所以恢复必须用
`pg_restore` 而非 `psql -f`。

---

## fusion-memory 栈的 compose(A 机新建)

`/srv/haitun/fusion-memory/docker-compose.yml`,三个服务:`postgres` / `embed-proxy` / `mcp`。
`docker compose config -q` **退出码 0**,解析后确认 `172.21.0.1` + `8700`。

### 关键设计:mcp 与 embed-proxy 走 `network_mode: host`

这解决了我先前标为"待解决"的 DSN 问题,而且是**零配置改动**地解决:

| 配置项 | 值 | host 网络下为何成立 |
|---|---|---|
| `FUSION_MEMORY_PG_DSN` | `…@127.0.0.1:5432/…` | postgres 已把 5432 映射到主机 127.0.0.1 |
| `FUSION_MEMORY_EMBEDDING_ENDPOINT` | `http://127.0.0.1:8701/…` | embed-proxy 同在 host 网络 |
| `FUSION_MEMORY_MCP_HOST` | `172.21.0.1` | 这是 psi-agent 网桥的**网关地址**=主机在该网桥上的地址 |

**为什么不能让 mcp 加入 psi-agent 的网络**:容器加入网络拿到的是 `172.21.0.x`,
而三份 `.env` 要访问的是 `.1`(网关)。容器绑不上网关地址,只有主机能。

代价是 host 网络不做端口隔离。但 8700 只绑在 172.21.0.1(docker 网桥内网)、
8701 只绑 127.0.0.1,**都不对公网暴露**,与 B 的暴露面一致。

### 口令处理:从 DSN 提取,不抄第二份

B 机把 `POSTGRES_PASSWORD` **字面量写在 compose 里**。A 机改为
`${POSTGRES_PASSWORD:?}` + 一个 `.env`(mode 600),而 `.env` 由脚本
**从 `mcp.env` 的 DSN 中提取**生成。

理由:口令只能有一个来源。抄第二份就会漂移,而口令漂移的表现是
**记忆服务静默连不上库** —— 这类故障没有告警。

核验用 sha256 前 12 位比对而非打印明文:B 侧 `f5d36c673cea` = A 侧 `f5d36c673cea` ✅。
先前也用同法确认了 B 的 compose 字面量与 DSN 口令**本来就一致**(不是两个值)。

### 已装到 A 的配置文件

`/root/.config/fusion-memory/{mcp.env,embed-proxy.env}`,root:root,mode 600。
`mcp.env` 的 `FUSION_MEMORY_MCP_HOST` 已按决定⑤改为 `172.21.0.1`(第 6 行),
改前已备份 `mcp.env.bak-subnet-20260909-*`,改后核验非注释行无 172.19 残留。

### 尚未验证的一条(有依赖顺序)

`172.21.0.1` 这个地址**要等 psi-agent 的 compose 建好网络之后才存在**。
所以 fusion-memory 栈**必须在 psi-agent 网络建立之后启动**,否则 MCP 绑定失败。
这个顺序依赖 compose 表达不了(两份不同的 compose),必须写进启动步骤。

### 已实测:172.21.0.1 三段链路全通(任务 #13 完成)

用一个一次性网络 `_probe_net21`(同 subnet)在 A 机做了完整验证,**不碰生产**:

| 判据 | 结果 |
|---|---|
| 建网前主机有 172.21 地址吗 | 无(证明地址确实由建网产生) |
| `docker network create --subnet 172.21.0.0/16` 后 | 主机出现 `inet 172.21.0.1/16 br-7a74a63ea656` ✅ |
| host 网络容器能否 `bind(("172.21.0.1",8700))` | **`BIND_OK`** ✅ |
| 172.21 网络内的容器访问 `http://172.21.0.1:8700/` | **`REACH_OK 200 GATEWAY_REACHED`** ✅ |

结论:决定⑤的地址方案成立,MCP 绑网关 + psi-agent 容器访问网关这条链路可用。
探针已清理(容器、网络、临时脚本全删,`ip addr` 里 172.21 已消失,A 机回到原状)。

**启动顺序(硬要求)**:先 `psi-agent` 的 compose(建出 172.21 网络与网关地址),
**再** `fusion-memory` 的 compose。反了 MCP 会 bind 失败。

一个探针自身的坑,记下来免得后人当成结论:第一次测可达性时打出
`REACH_FAIL Connection refused`,**那是假失败** —— 容器名我起了 `_probe_srv`,
docker 拒绝下划线开头的名字,服务器根本没起来。教训是探针要先验"被测对象活着"
(`ss -lntp | grep 8700`)再下可达性结论,否则 refused 会被误读成网络不通。

---

## pgvector 库恢复(A 机,已完成并与 B 逐表比对)

### 操作

| 步骤 | 结果 |
|---|---|
| `docker compose up -d postgres` | 网络 `fusion-memory_default` 建成,容器 **12 秒后 healthy** |
| 恢复前表数 | **0**(证明是空库恢复,不是叠加到已有数据上) |
| `zstd -dc … \| pg_restore -U fusion -d fusion_memory --no-owner --no-privileges` | **`RESTORE_EXIT=0`** |
| `pg_restore: error` 行数 | **0** |
| `pg_restore: warning` 行数 | **0** |

这是 A 机第一次 `up -d`,属**新建栈**,不在"禁止 up -d"的纪律范围内
(纪律针对的是改已有容器内的文件)。

### 结构比对:A 与 B 完全一致

| 判据 | B(生产) | A(恢复后) | |
|---|---|---|---|
| public 表数 | 33 | 33 | ✅ |
| public 索引数 | 107 | 107 | ✅ |
| 扩展 | `plpgsql/1.0, vector/0.8.6` | `plpgsql/1.0, vector/0.8.6` | ✅ |

索引数也比对了 —— 只比表数会漏掉"表建了但向量索引没建"这种情况,
而那会让记忆检索**退化成全表扫描却不报错**。

### 逐表行数比对:0 处倒挂,差 48 行(0.104%)

33 张表逐表比对,结论是**恢复无损,差额是 B 仍在写**:

| 分类 | 张数 | 说明 |
|---|---|---|
| **A > B** | **0** | 若非 0 就说明恢复出了脏数据。是 0 ✅ |
| A == B | 23 | |
| A < B | 10 | dump(12:22)之后 B 上的新写入 |

总行数 B=46372 / A=46324,**差 48 行 = 0.104%**。10 张有差额的表
(`entities` 8、`evidence_spans` 8、`encoding_decisions` 6、其余 2~4)
**全是只增不改的流水型表**,差额方向**一律是 A 少 B 多**,没有一张倒挂。

这正是方案里"停机窗内要做增量重导"的依据 —— 现在有了实测的量级:
**约 0.1%/小时**,窗内重导要覆盖的就是这一段。

### 探针自身的两个坑(比结论更值得记)

1. **`docker exec … psql -f /tmp/x.sql` 找的是容器内的路径,不是主机上的。**
   我 scp 到主机后直接 `-f`,两台机器都报 `No such file or directory`。
   看着像"库有问题",实际是文件不在它以为的地方。改用 `< file` 走 stdin 才对。
2. **嵌套引号经 ssh 传递会被剥掉一层**,导致 SQL 里的 `E'\n'`、`'public'`
   变成裸标识符,报 `syntax error at or near "chr"`。
   最后把 SQL 写成文件再 scp 进去,才拿到可信结果。
   教训:**跨 ssh 的多层引号是不可靠的判据载体**,复杂查询一律走文件。

### 尚未做

- **增量重导**:停机窗内需重新 dump 并恢复差额(现测 0.1%/小时)。
- MCP 与 embed-proxy **尚未启动** —— 它们要绑 172.21.0.1,
  必须等 psi-agent 的 compose 先建出网络(见上文启动顺序)。

---

## A 机验收判据(全部取自 B 机实测,搬完逐条比对)

搬完之后要拿什么判"搬对了"。这些值**现在**从 B 上量好并记下来 ——
搬完再去 B 上量就来不及了(届时 B 已停机)。

### 镜像层(digest 必须逐字节相同)

| tag | B 机 image ID | 大小 |
|---|---|---|
| `meeting-fix-main-a1bc44d9-20260907-1154` | `sha256:c84002c4fe02acd7…6b9b96c9` | 1.87GB |
| `86d5f755` | `sha256:d0dec87fd82730e9…20768819` | 2.68GB |
| `local` | `sha256:3dc1521172c953e8…602bdd378` | 2.68GB |

`docker load` 之后三个 ID 必须**完全一致** —— 不一致说明传输损坏或加载错了源。

### 容器层:工具装载数(第三层核验)

| 容器 | 期望日志 |
|---|---|
| `psi-agent-gateway` | `Loaded 220 tool(s) from 130 file(s)` |
| `psi-agent-luolin` | `Loaded 192 tool(s) from 98 file(s)` |
| `psi-agent-chengxx` | `Loaded 192 tool(s) from 98 file(s)` |

**注意两个已知陷阱**:

1. 日志原文带括号 —— 正则必须写 `Loaded [0-9]+ tool\(s\) from [0-9]+ file\(s\)`。
   写 `Loaded [0-9]+ tools?` 会得到**假零**,而假零比真零危险,因为它长得像结论。
2. 私有容器的判据是 **192,不是 277/205**。旧镜像 `86d5f755` 报的 277 是
   把 import 进来的公开名也数进去了(实测同 98 个文件里公开 def 只有 195 个
   = 192 async + 3 sync,277 超过真实上限)。**192 才是对的,没有能力损失。**

### /app/src 层

`psi_agent.__file__` = `/app/src/psi_agent/__init__.py`,`/app/src` 下 **125 个 `.py`**。

### workspace 体积(排除项生效后的合理性参照)

| 目录 | B 机实测 |
|---|---|
| `workspace` | 2.6G |
| `workspace-luolin` | 267M |
| `workspace-chengxx` | 60M |

合计约 2.93G,而排除后的传输载荷是 2.46G —— 差额即
`skills.pre-*` / `tools.pre-*` / `*.bak*` / `*.wav` / `agent-video` 四类。

### 三份 workspace/.env 的改网段脚本(已就位,尚未执行)

`/root/prep/patch-envs.py`。**不用 `sed -i`**,因为 sed 改 0 处也退出 0 ——
静默失效,而这个值错了的表现是"记忆服务连不上且不报错"。脚本的判据设计:

- 三个文件缺任一个就 **exit 2 且不写任何东西**(已实测:workspace 未解开时正确中止)
- 每份文件必须**恰好命中 1 行** `FUSION_MEMORY_MCP_URL=…172.19.0.1…`,否则 exit 3
- 按字节读写以保住生产的 LF 换行(仓库是 CRLF,写错会让整个文件看起来全变了)
- 改完打印 `172.19.0.1 remaining=0` 与新 md5 作正向核验
- 改前每份存 `.bak-subnet`

---

## B 机 13:40 复查:502 修复仍有效,但 gateway 贴着上限跑

### 502 修复保持

| 判据 | 结果 |
|---|---|
| `https://lark.oauth.genuineknowledge.cn/oauth/callback` | **400** ✅(预期值) |
| `/` | 404 ✅ |
| `/sessions` | 404 ✅ |
| gateway 与 oauth-proxy 的 netns inode | 均 `net:[4026532616]` ✅ **共享未断** |
| 三个容器 `RestartCount` | 全 **0**(04:14/04:26 起至今没重启过) |

netns 这条判据要注意:`docker inspect` 的 `SandboxKey` 对 oauth-proxy 是**空值**
(它 `network_mode: service:gateway`,没有自己的 sandbox),
所以**不能用 SandboxKey 比对**。要用 `readlink /proc/<pid>/ns/net` 拿内核 inode 才对。

### 修正我自己的一个误读:内存不是在爬,是平台期

`docker stats` 显示 gateway `2.673GiB / 3GiB = 89.10%`,我起初读成"正在爬向 OOM"。
**实测 90 秒四次采样是平的**:

| t | memory.current | anon |
|---|---|---|
| 0s | 2737MiB | 2711MiB |
| 30s | 2736MiB | 2711MiB |
| 60s | 2737MiB | 2711MiB |
| 90s | 2737MiB | 2711MiB |

所以不是稳定泄漏,而是**长期停在一个很高的平台上**。

### 但风险是实在的,判据在 memory.events

```
low 0    high 0    max 26680    oom 0    oom_kill 0    oom_group_kill 0
```

- `oom_kill 0` —— 自 04:14 重启以来**没有再被杀过**(dmesg 里最后一次是今天 11:01:30,
  属于上一个容器生命周期)。
- **`max 26680`** —— 这个 cgroup **撞到硬上限 26680 次**,一直在被回收边缘反复拍打。

结论:**OOM 不来自缓慢泄漏,而来自高平台之上的瞬时尖峰**。
2711MiB 的常驻 + 3072MiB 的上限 = 只剩 361MiB 余量,一次大回合就能捅破。
这坐实了决定④(A 机 4500m)是对的方向 —— 留出的余量从 361MiB 变成约 1.8GiB。

**未验证**:B 在停机窗之前是否还会再撞一次 502。按"平台高+尖峰触发"的模型,
可能会;`restart-stack.sh gateway` 是已验证过的恢复手段(约 70 秒)。

主机整体也紧:`free -h` 显示 7.1Gi 总量、available **仅 1.4Gi**。
这是 B 机的既有状况,不是搬迁引入的。

---

## 方案漏项②:A 机 Caddy 缺 ToB 的 vhost(会静默断掉 OAuth 回调)

### 怎么发现的

比对两机 Caddyfile 的 vhost 清单时,B 的文件里**只有 `account.` 一个域名**,
可是 `lark.oauth.genuineknowledge.cn` 明明在跑(公网 `/oauth/callback` 返回 400)。
既然 443 上只有一个 caddy 进程,那配置必然在别处 —— 查到 B 的 Caddyfile
**第 46 行有 `import /etc/caddy/tob.d/*.caddyfile`**。

`/etc/caddy/tob.d/lark.oauth.caddyfile` 才是 ToB 的入口定义:
反代 `127.0.0.1:8090` = `psi-agent-oauth-proxy`。

### A 机的实际状态

| 判据 | A 机 |
|---|---|
| Caddyfile 里的 `import` 行 | **没有** |
| `/etc/caddy/tob.d/` | **不存在** |
| `/var/log/caddy/` | 存在(caddy:caddy) ✅ |
| caddy 版本 | v2.11.4,**与 B 完全一致** ✅ |

**后果如果不补**:切换后 `lark.oauth.genuineknowledge.cn` 会落到 A 的
`:80` catch-all 静态页,三个 ToB 容器的 OAuth 授权回调**全断,而且不报错** ——
用户点完「同意授权」跳到一个静态页,取件箱永远收不到回调。
这正是那个 vhost 注释里写的、当初要建它的原因。

### 已做

文件已取到 A 的 `/root/prep/caddy/lark.oauth.caddyfile`,
**LF 归一化后 md5 与 B 一致**:`f432130ce5d694e3d4293b615e67a7f7`。

### 未做(有副作用,改 A 的活 web 服务,留到切换步骤)

1. `mkdir /etc/caddy/tob.d` 并装入该文件
2. 在 A 的 Caddyfile 末尾加 `import /etc/caddy/tob.d/*.caddyfile`
3. `caddy validate` 通过后 `reload`

**验收判据**(注意别用错):机内
`curl -H "Host: lark.oauth.genuineknowledge.cn" http://127.0.0.1/` 应得
**502**(上游 oauth-proxy 未起时 502 属正常),
**而不是**静态页的 200 —— 得 200 说明 import 没生效,请求落到了 catch-all。

### 顺带确认的一件事

A 的 `account.genuineknowledge.cn` vhost 与 B 的规格一致(都反代 `127.0.0.1:8081`),
且 A 的 caddy 与 B 同版本,所以 ToC 那一侧不需要额外处理。

---

## 载荷完整性的正向核验(排除项没删到该留的东西)

排除四类文件之后,要证明**删掉的确实没人用**,而不只是"体积变小了"。

### 排除项没被任何配置引用

`grep` 三份 `.env` 与 `docker-compose.yml`,`skills.pre-` / `tools.pre-` /
`agent-video` **一处引用都没有** → 安全。

### 关键路径全部落在传输范围内

| 配置项 | 值 | 是否在包内 |
|---|---|---|
| 挂载点 | `/srv/haitun/psi-agent/workspace` → `/workspace` (rw) | ✅ 整个目录都传 |
| `PSI_APPDATA` | `/workspace/.psi/appdata` | ✅ `.psi/` 已确认保留(2403 项) |
| `FUSION_MEMORY_TOKEN_MAP_FILE` | `/workspace/.psi/memory_tokens.json` | ✅ 三份都在 |

gateway 只有**一个** volume 挂载(`workspace` → `/workspace`),
没有额外的宿主目录映射 —— 这简化了搬迁:workspace 目录搬全了就够了。

### 记下一个既有状态:chengxx 的 token map 是空的

| workspace | `memory_tokens.json` |
|---|---|
| `workspace`(gateway) | dict,**42** 条 |
| `workspace-luolin` | dict,**1** 条 |
| `workspace-chengxx` | dict,**0 条**(文件仅 3 字节 = `{}`) |

chengxx 的 `.env` 也**没设** `FUSION_MEMORY_AUTO_REGISTER_FEISHU`,
容器日志总共**只有 21 行**、**0 行**提到 memory。

判断:这不是"记忆坏了",是**这个容器基本没被用过**。

**为什么要记**:搬到 A 之后 chengxx 若报 `configuration_error` 或
`memory_user_not_configured`,那是 **B 上就有的状态**。
判据应当是「与 B 一致」,而**不是**「应该能用记忆」——
否则会把既有状态误诊成搬迁回归,然后去修一个没坏的东西。

### 宿主层依赖审计(用发现 Caddy 漏项的同一方法again查了一遍)

| 检查项 | B 机 | 结论 |
|---|---|---|
| root crontab | 无 | — |
| `/etc/cron.d` | 只有 `e2scrub_all`、`sysstat` | 与 psi-agent 无关 |
| systemd timer | 只有 `psi-cloud-backup.timer` | **ToC 线**,不在范围 |
| 引用 psi-agent/haitun 的 unit | 只有那两个 `fusion-memory-*.service` | 已处理(改为容器) |

**结论:psi-agent 在宿主层没有自己的定时任务或 unit**,搬迁不会漏掉后台作业。

### 一处差异,方向是安全的

| | B | A |
|---|---|---|
| `/etc/docker/daemon.json` | **不存在**(json-file 无上限) | 有:`max-size 10m, max-file 3` + 三个 registry 镜像 |

B 的 gateway 容器日志已涨到 **101M**(累计 121M)。A 有上限,所以**A 更安全**,
不需要改。副作用只有一个:**A 上基于日志的判据只能看到最近的历史**,
`docker logs | grep` 拿不到很早的启动行 —— 量工具数要在启动后尽快量。

## 9-09 14:10 自查发现手册的完整性判据不成立(已修)

写完手册后回头核阶段 0.2,发现我自己写的判据是空的:

```
# 原文(错的)
ssh root@8.222.255.23 'md5sum /tmp/*.zst 2>/dev/null'
ssh root@47.100.84.197 'md5sum /srv/haitun/incoming/*.zst'
```

两份大 archive 是 `docker save|tar -cf - | zstd | ssh cat >` **流式直传**,
B 机磁盘上从来没有过这两个文件(B 根盘 80% 满、只剩 7.5G,这正是当初选流式的原因)。
实测 B 的 `/tmp/*.zst` 只有三份小的:

| 文件 | B 侧 | 说明 |
|---|---|---|
| cfg.tar.zst | 14889 | 落地了,可两侧比 md5 |
| fm.dump.zst | 43865141 | 落地了,可两侧比 md5 |
| fm-repo.tar.zst | 1679395 | 落地了,可两侧比 md5 |
| images.tar.zst | **不存在** | 流式,B 侧无副本 |
| workspaces.tar.zst | **不存在** | 流式,B 侧无副本 |

注意 `2>/dev/null` 让这条命令**在缺文件时也不报错**,只是少打两行——
判据会静默变成"只比了三份小的",而窗内照着敲的人不会发现。这是我写判据时的疏忽:
带 glob 的 md5 比对天生会漏,因为它比的是"两边都有的",而不是"清单上应该有的"。

改成了四条真判据:

1. `PIPE_EXIT` 全 0(管道任一环失败都露头)
2. A 侧 `zstd -t` 帧校验(zstd 每帧带 xxhash,截断/坏字节会被抓)
3. **镜像用 image ID 比对**——`docker save` 保的是内容摘要,这比 archive md5 判据
   *更强*:它证明的是"装载进 A 的层内容与 B 逐字节相同",而 archive md5 只证明
   传输没坏。已记录 B 侧三个 ID:
   `c84002c4…6b9b96c9`(1154)/ `d0dec87f…20768819`(86d5f755)/ `3dc15211…602bdd378`(local)
4. **workspace 用文件数清单比对**,B 侧 14:10 实测(已扣三类排除模式):

| 目录 | files | psi | tools_py | .env |
|---|---|---|---|---|
| workspace | 35639 | 2381 | 218 | yes |
| workspace-luolin | 1345 | 9 | 155 | yes |
| workspace-chengxx | 1164 | 6 | 161 | yes |

判据定为:**psi / tools_py / .env 三项必须完全相等,且没有任何一项变小**;
`files` 允许略增(B 仍在跑,日志在写)。这个分寸是必要的——把 `files` 也定成严格相等,
窗内一定会因为日志多了几行而"失败",然后人就会开始忽略判据。

同时把 3.2b 加进阶段 3(解包后立刻量),并注明必须在 3.3 之前——
`patch-envs.py` 要三份 `.env` 都在位才不会 exit 2。

顺带确认了 tar 的两条 `file changed as we read it` 告警不影响判据:
命中的是 `workspace/.psi/appdata/logs/psi-debug-{9,138}.log`,活着的日志文件,
tar 仍会把它收进包(只是内容是读取瞬间的快照)。日志不在关键路径上。

## 9-09 14:08 传输进度

| 时刻 | images.tar.zst | workspaces.tar.zst | 合计 |
|---|---|---|---|
| 13:52 | 341,737,472 | 273,285,120 | 631M |
| 14:08 | 413,204,480 | 339,869,696 | 753M |

16 分钟涨 122M,合 **约 127 KB/s**,与此前实测的 114 KB/s 同量级,
再次印证是链路总带宽封顶(两条流加起来才这个数)。

未压缩基线:三个镜像 2,997,045,243 字节;三份 workspace 2,994,787,901 字节。
按当前速率剩余部分仍需数小时,**负责人回来时传输仍在进行,属预期**。

## 9-09 14:20 漏项③:`account` 域名是 ToC 的,搬它会打断另一条产品线

去做阶段 0.3(装 Caddy vhost)前先读 A 的 Caddyfile,发现 A **已经有**
`account.genuineknowledge.cn` 这个 vhost。顺着查下去发现手册阶段 5 有个真缺陷。

两条域名的实际归属(两机都实测了 `ss -lntp` + `docker ps`):

| 域名 | Caddy 里 | 端口 | 后面是谁 | 产品线 |
|---|---|---|---|---|
| `lark.oauth.genuineknowledge.cn` | B 的 `tob.d/lark.oauth.caddyfile` | 8090 | psi-agent gateway / oauth-proxy | **ToB(我负责)** |
| `account.genuineknowledge.cn` | 两机的主 `Caddyfile` | 8081 | **`psi-cloud` 容器** | **ToC(不是我负责)** |

计划文档第 196 行写「搬迁时这一行要改指 ToB 栈」——**这条是错的**。
`account` → 8081 → psi-cloud,psi-agent 从来不在这个端口上;ToB 的入口是
`lark.oauth`(计划文档第 482 行自己也这么写:「入口是 lark.oauth…**不是** account.*」)。
文档内部这两处互相矛盾,而我照抄了错的那处,把两条 A 记录一起写进了阶段 5。

**为什么不能顺手一起搬**:A 机的 psi-cloud 与 B 机已经分叉,不是我此前记的
「落后 1 commit」。实测:

```
公共基  64baf89 feat(auth): 解绑路由 + 409 拆成 3 个码
B: 64baf89 → 03ec9fe → 68c984d
A: 64baf89 →           9ed700c
```

- B 独有 `03ec9fe`「免费模型转发器上线,上游 key 从 app 容器里彻底移出」,
  改了 14+ 个文件(Dockerfile / compose / litellm/config.yaml / core/app.py …)
- `68c984d` 与 `9ed700c` **message 逐字相同但 hash 不同**——同一个改动在两机各自
  提交了一次(A 那次的父节点是 64baf89,B 那次是 03ec9fe)。这类「看着一样其实是两个
  commit」最容易被当成同步完成。

功能上量得出差别(`/openapi.json` 路由数):

| | 路由数 | 独有 | litellm 容器 |
|---|---|---|---|
| B | 17 | `/llm/v1/chat/completions`、`/llm/v1/models`、`/llm/v1/health/upstream` | `psi-litellm` 在跑 |
| A | 14 | — | **NONE** |

所以把 `account` 的 A 记录指到 A 机,ToC 客户端会**丢掉整个 `/llm/*` 转发能力**,
且 A 上连 litellm 容器都没有。这是静默的:`/auth/*` 全都还在,登录照样能过,
只有用到免费模型的功能会 404。

**改法**:阶段 5 只搬 `lark.oauth` 一条。`account` 留在 B,并在手册里写明理由,
免得窗内有人「顺手把两条都改了」。ToC 迁移是另一条线的事,不在我的范围内,
但这个分叉已记录在此供负责人决策。

**连带修正一条我之前写的**:决定⑤ 的理由里提到「不删 psi-cloud」是对的,
但现在有了更强的理由——A 上这个 psi-cloud 虽然缺 commit,它仍是 ToC 在境内的
**唯一**一份;而且它落后这件事本身说明 ToC 的部署流程没覆盖 A 机。

**顺带确认 A 的 Caddy 现状**(只读):`v2.11.4` active,80/443 都在听,
`/etc/caddy/tob.d` **不存在**、`import` 行**没有**——漏项② 复核成立。
A 的 Caddyfile 52 行,含 `account` 的 HTTPS vhost(`disable_http_challenge`,
因为境内 80 曾被未备案管控拦)、一条 `http://account` 跳转、一个 `:80` catch-all 静态页。
那个 catch-all 正是阶段 0.3 判据要盯的:装好 `tob.d` 后打 `lark.oauth` 的 Host
得 502 而**不是** 200,得 200 就说明 `import` 没生效、请求落到 catch-all 了。

---

## 9-09 16:20–16:40 窗前四项前置核验(全部只读/无副作用)

负责人问「搬家主线现在是不是干等」,答案是不是——传输是硬门槛,但有四件事
不依赖文件落地,现在就能做。四件全做完了,结论如下。

### ① 时区:过了,但载体比我以为的脆

**判据**:三条定时任务是 `0 16 * * 1,3,5`,A 机时区错则全部错点且**无任何报错**。

宿主机两边一致(`Asia/Shanghai`,NTP active,A 与 B 的 `date` 差 5 秒)。
但真正吃劲的是容器层,而容器层的结论出人意料:

```
docker exec psi-agent-gateway date        -> Wed Sep  9 16:20:58 CST 2026   ✅
docker exec psi-agent-gateway echo $TZ    -> Asia/Shanghai
docker exec psi-agent-gateway readlink -f /etc/localtime
                                          -> /usr/share/zoneinfo/Etc/UTC   ⚠️
```

**`/etc/localtime` 在容器里指向 UTC,时区完全靠 `TZ` 环境变量撑着。**
而 `TZ` 的来源不是镜像、不是 `.env`:

| 层 | 有没有 TZ |
|---|---|
| 镜像 `Config.Env` | **没有** |
| `env_file`(`./workspace/.env` 等) | **没有** |
| compose 的 `environment:` 段 | **有,4 处** |

所以 **compose 文件是时区的唯一载体**。A 机那份是手工准备的,核了:
4 处 `TZ=Asia/Shanghai` 一个不少,`gateway` / `private-luolin` /
`oauth-proxy` / `private-chengxx` 四个服务全覆盖。行号 B=19/57/89/112、
A=19/57/86/109,偏移只因 A 还没有 `env_file:` 那几行(`.env` 待阶段 3.3 补)。

**连带修正我之前写错的一处位置**:compose 不在 `/srv/haitun/`(那里 `*.yml` 是空的),
而在 **`/srv/haitun/psi-agent/docker-compose.yml`**。从容器 label 查出来的:
`com.docker.compose.project.config_files`。之前按错路径 grep 才得到「compose 里
没有 TZ」的假结论。

### ② 磁盘:峰值 8.1 GB / 可用 25 GB,富余很多

不用估——压缩比从 `/proc/<pid>/io` 的 `rchar`/`wchar` 实测反推:

| 项 | 实测 |
|---|---|
| workspace tar 未压缩总流 | **2.57 GB**(`tar ... \| wc -c` 实跑) |
| workspace 压缩比 | **0.396** → 最终 zst ≈ 1.02 GB |
| 镜像压缩比 | **0.482** |

| A 机峰值构成 | GB |
|---|---|
| 可用 | 25.0 |
| 两个 zst 包(不删) | 2.52 |
| `docker load` 解出层(23 唯一层 / 34 引用,已去重) | ~3.0 |
| workspace 解包 | 2.57 |
| **峰值** | **8.09** |
| **峰值后剩余** | **16.91** |

结论:**包不用删,不用腾地,手册里不需要加清理步骤。**

附一条测量教训:`docker inspect --format {{.Size}}` 在 B 机上给出 `.45GB`,
而 `docker images` 同一个 tag 报 1.87GB,**自相矛盾,该字段不可信**。
改用 `/proc/<pid>/io` 的字节计数才拿到可信数。

### ③ `docker load` 管道:实测通了(窗内最大未知项已消除)

这一步手册里写着但**从没在 A 机跑过**。用真镜像造包跑完整链路:

```
zstd --version                     -> v1.5.7
docker save <真镜像> | zstd -3      -> 76,405,772 字节
docker rmi                         -> 确认不存在 (0 个)
zstd -dc /tmp/loadprobe.tar.zst | docker load
   -> Loaded image: loadtest:probe
   -> PIPE_EXIT=0 0                 ✅
   -> 载入后存在 1 个
```

顺带对已落地的小包做了完整性验证:`zstd -t fm-repo.tar.zst` 通过、
`zstd -t cfg.tar.zst` 通过(这正是阶段 0.2 修好后的判据②)。

探针已清理:`docker rmi loadtest:probe`、`rm /tmp/loadprobe.tar.zst`,
`df` 回到 25G,无残留。

### ④ 16:00 定时任务:触发成功,新工具首次真实运行 9/9 全绿

```
16:00:00.009  schedule_registry:_run_one:392 - Schedule triggered: 'todo-ledger-push'
16:03:23      ai-turn open
16:04:33.376  ai-turn close elapsed_ms=69779 outcome=ok
16:04:33.379  Executing tool: 'feishu_todo_compare_card_send' × 9 (9 个 receive_id)
16:04:34-35   Tool result ok:true × 9   (elapsed_ms 1279~2424)
16:05:16.842  ai-turn close elapsed_ms=40946 outcome=ok
```

**`ok:true` 计数 = 9,零 `ok:false`,零 ERROR/Traceback。**
PR 871 新投的 `feishu_todo_compare_card_send` 首次真实运行即通过。

**一条必须自己纠正的错误判断**:我先前用 `docker logs --since 15:55` 查,
报了「16:00 无触发无报错」。那是**假的**——docker 不认 `HH:MM` 这种格式,
返回的是单行解析错误 `invalid value for "since": failed to parse value as
time or duration: "15:55"`,被我当成了「grep 结果为空」。
`--since` 只吃相对时间(`45m`)或完整 RFC3339。

> **判据教训(与已有的「pytest 路径参数被静默忽略」同类)**:
> 当一条 grep 返回空而你预期它有内容时,先证明**数据源本身非空**,
> 再解释「空」的含义。这次只要多打一句 `wc -l` 就会看到「1 行」而不是「0 行」,
> 那 1 行就是错误消息本身。空结果和取数失败在 shell 里长得一模一样。

### 顺带发现:一条与本次改动无关的 ERROR(记录,不动)

```
16:00:02.693 | ERROR | request_assembly:build:439 -
Request still 406456 chars after eliding every elidible row (budget 375000):
the un-elidible floor exceeds the budget — system prompt, tool schemas,
the two most recent rows, and one handle per elided row (59 of them).
Elision cannot fix this. Sending anyway
```

这个会话**不可省略部分已超预算 8.4%**,省略机制无能为力,只能硬发。
系统提示词 187,1xx 字符的构成:bootstrap 文件 84,209(45.0%)、
技能索引 ×2 53,577(28.6%)、workflow section 8,641(4.6%)。
`TMPFIX-M2 tools_exposed=43 of 221`。

与 797/868 无关(是该 workspace 长期积累的结果),且本轮**照样跑成了**。
但它说明这个会话离硬失败不远。属内核侧,不在我的配额内,仅记录。

### 传输进度(16:23 实测)

| 包 | 未压缩总流 | 已传 | 进度 |
|---|---|---|---|
| workspaces | 2.57 GB | 1.72 GB | **67%** |
| images | 未知总量 | 1.57 GB | — |

A 侧已落地 `images.tar.zst` 812,679,168 + `workspaces.tar.zst` 731,348,992,
合计 1.5 GB。

### 回滚演练:改到 A 机做,不在 B 机做

负责人问副作用。在 B 机演练有三条,都是真副作用:

1. **真实停机**。要测 `start` 必须先 `stop`,那就是真把生产停了,gateway
   冷启 60–90 秒,工作时间内飞书机器人哑掉一分半。
2. **必然撞 netns 孤儿**。`oauth-proxy` 是 `network_mode: "service:gateway"`,
   gateway 一重启它就挂死 netns,且**仍打印 `Running on http://0.0.0.0:8090`
   而公网 502**。演练等于故意再造一次今天 13:39–15:00 那 81 分钟的故障。
   修法已验证(14:59 用过 `restart-stack.sh gateway`,70s RC=0),但仍是真故障。
3. **毁掉 OOM 证据**。gateway 为何 13:39 退出(ExitCode=0,非 OOM)仍未查清,
   原因就是容器重建把 cgroup `memory.events` 清零了。再停一次又清零一遍。

注意 `docker compose stop` **本身是安全的**(保留容器不重建,`/app/src`
改动不丢),危险不在 stop 语义,而在「这次停机是真停机」。

**改法**:排进阶段 4(A 机起栈自测)。A 机此刻零用户流量,停多久都无人感知,
而要验的两件事在 A 机上完全等价——(a) `stop`→`start` 后容器 ID 不变,
(b) `start` 后 oauth-proxy netns 是否孤儿(证明 `restart-stack.sh` 那步必需)。
**唯一在 A 机验不了的**是「DNS 切回 B 需多久生效」,那只能靠 TTL 算,无法演练。

---

## 9-09 16:40–17:10 镜像传完并装载,阶段 3.1 提前在窗外完成

### 传输完成(判据①)

```
/root/xfer-images.log  ->  PIPE_EXIT=0 0 0 rc=0
                           END 2026-09-09T16:40:10
```

三段管道(`docker save` / `zstd` / `ssh cat`)**全 0**。落地
`images.tar.zst` 868,891,431 字节。

### 完整性(判据②)

```
zstd -t images.tar.zst  ->  1726436864 bytes    ✅
zstd -l                 ->  Frames=1 Skips=0 Check=XXH64
```

### 装载 + digest 比对(判据③,比 archive md5 更强)

```
zstd -dc images.tar.zst | docker load
  Loaded image: psi-agent-gateway:meeting-fix-main-a1bc44d9-20260907-1154
  Loaded image: psi-agent-gateway:86d5f755
  Loaded image: psi-agent-gateway:local
  PIPE_EXIT=0 0
```

| tag | A 侧装载后 digest | 与 0.2 ③ 记的 B 侧值 |
|---|---|---|
| `meeting-fix-…-1154` | `sha256:c84002c4fe02acd7f4c6955e7ee6025e3e6ea5c8086667a3333ba9506b9b96c9` | **逐字节相同** |
| `86d5f755` | `sha256:d0dec87fd82730e9cf1ec8bd4bb1f3f4e94e32fcf137f46eb2ad3ac620768819` | **逐字节相同** |
| `local` | `sha256:3dc1521172c953e8507a8176856f4f7a25d0d73a0b81febcf5c571b602bdd378` | **逐字节相同** |

这条判据证明的不是「压缩包传对了」,而是**装进 A 的层内容与 B 上跑着的完全一致**。

**阶段 3.1 就此提前在窗外完成**(装载镜像不触碰任何运行中的服务),窗内少一步。
磁盘:装载后 A 机 21G 可用,比预估的峰值后余量还宽松。

### A 机 Caddy `tob.d`(漏项②)已备好并预演通过——**未安装**

B 侧的载荷取到了:`/etc/caddy/tob.d/lark.oauth.caddyfile` 1,807 字节,
`md5`(LF 归一化)`= f432130ce5d694e3d4293b615e67a7f7`;
`import` 行在 `Caddyfile:46`,内容是 `import /etc/caddy/tob.d/*.caddyfile`。

**这份配置与机器 IP 无关**——上游写的是 `127.0.0.1:8090`(oauth-proxy 靠
`network_mode: service:gateway` 共享 gateway 的网络命名空间),所以能原样搬。

已 scp 到 A 机 `/root/prep/lark.oauth.caddyfile`,A 侧 md5 一致。
然后做了**零副作用的语法预演**:在 `/root/prep/caddy-dryrun/` 沙箱里
拷一份现网 `Caddyfile` + 追加指向沙箱的 `import` 行,跑

```
caddy validate --config <沙箱>/Caddyfile --adapter caddyfile
  -> Valid configuration     EXIT=0
```

输出里 `srv1` 那条 `server is listening only on the HTTP port, so no automatic
HTTPS will be applied` 是**预期的**——那是 `http://lark.oauth` 的 301 跳转块;
`srv0` 被加上了 TLS policy,说明 443 的 vhost 被正确识别。

沙箱已删。**核实过 `/etc/caddy` 全程未被改动**:`tob.d` 仍不存在、
`grep -c import Caddyfile` 仍为 0。

**剩下的安装动作有副作用,需负责人批准**:`mkdir /etc/caddy/tob.d` +
装文件 + 追加 `import` 行 + `caddy reload`。判据仍是阶段 0.3 那条:
打 `lark.oauth` 的 Host 应得 **502**(上游未起时正常)而**不是**静态页 200。

### workspace 传输(17:10 实测)

镜像传完释放了带宽,速率从 66 KB/s **回升到 141 KB/s**(60 秒窗口实测):

| | |
|---|---|
| tar 已读出 | 1.97 / 2.57 GB(77%) |
| A 侧 zst 已落地 | 0.91 GB / 估计总 1.02 GB |
| **ETA** | **约 13 分钟** |

这也顺带说明前面观察到的「速率持续恶化 140→66」主要是**两个包互相抢带宽**,
而不是跨境链路单方面劣化。

---

## 9-09 17:45 gateway 502 的根因坐实:memcg OOM,`docker inspect` 在谎报

任务 #6 一直挂在「gateway 为何 13:39 退出仍未查清」。查清了,而且这是比 502
本身更重要的发现。

### `ExitCode=0` / `OOMKilled=false` 是假的

内核日志实证(`dmesg -T`),容器 `5b84e881` 经 `docker ps` 核对 = **`psi-agent-gateway`**:

```
oom-kill:constraint=CONSTRAINT_MEMCG, oom_memcg=/system.slice/docker-5b84e881….scope,
         task=psi-agent, pid=2489066, uid=0
Memory cgroup out of memory: Killed process 2489066 (psi-agent)
         total-vm:5187200kB, anon-rss:2909524kB
```

**这正是记忆里那条 [[docker-oomkilled-false-when-child-dies]]**:被杀的是容器内的
**子进程**而非容器 init,所以 docker 视角完全看不见,状态还显示 Up。

### 不是偶发,是持续发作

| 日期 | 时刻 | 次数 |
|---|---|---|
| 9-07 | 19:03、19:50、20:20、21:14 | 4 |
| 9-08 | 09:40、14:25、19:11 | 3 |
| 9-09 | 11:01、**13:37**、14:58、15:38 | 4 |

每次被杀时 `anon-rss` 落在 **2.76–2.94 GB**,而 `mem_limit: 3g`。**触顶即死。**
13:37:35 那次就是 13:39 那 81 分钟 502 的起点。

查询当时状态:`docker stats` 报 **2.465 GiB / 3 GiB = 82.16%**,离下次 OOM 不远。

### `memory.events` 的矛盾解释了为什么之前查不出来

```
max 2297        <- 触顶 2297 次
oom 0
oom_kill 0      <- 却报零次 OOM kill
```

`max` 计的是「因触及上限而被迫回收」的次数,`oom_kill` 只计 **cgroup 级**的 kill。
子进程被杀不进这个计数器。所以先前「`memory.events` 被容器重建清零、证据没了」
这个判断**只对了一半**——计数器确实清零了,但 `dmesg` 里的记录是内核环形缓冲,
**不受容器重建影响**,一直都在。查错了地方。

### 对搬家的影响:是好消息,但不要当成「修好了」

决定④ 把 A 机 gateway 的 `mem_limit` 从 3G 提到 **4500m**,当时理由只是
「B 上常驻 2711MiB,3G 太紧」。现在有硬证据:**3G 就是 OOM 的直接原因**。

另一个此前没量到的差异:

| | A 机 | B 机 |
|---|---|---|
| 物理内存 | 7,265 MB | ~7,168 MB |
| **swap** | **4,095 MB(全空)** | **0** |
| `available` | 5,481 MB | **1,024 MB** |

**B 机零 swap,撞上限直接被杀,没有任何缓冲;A 机有 4G swap 兜着**,
同样压力会先换页。

搬过去后 A 机的实际峰值:gateway 2.9G + luolin 383M + chengxx 239M +
oauth-proxy 31M + postgres 153M + psi-cloud 47M ≈ **3.75 GB**,物理 7.1G 装得下。

> 一个我自己先算错又推翻的判断:三容器 `mem_limit` 之和 4500m+1g+1g = 6.5G,
> 加上已用 1G 超过 7G 物理内存,我一度认为是超配。**这个担心不成立**——
> `mem_limit` 是天花板不是预留,而实际峰值只有 3.75G。A 机比 B 机宽裕得多。

**所以搬到 A 之后 502 预期自然消失**,但要说清:**这不是修根因**。
真根因是 gateway 为什么要吃 2.9 GB——很可能与同日发现的
`request_assembly` 超预算(单会话不可省略部分 40 万字符 / 预算 37.5 万)是
同一件事的两面。那属内核侧,是会话 2 的范围,此处只记录。

### 顺手修掉 A 机 compose 注释里一个错数字

原注释写「B 上撞 3g 被 OOM 杀 **86 次**」——那个数字是我先前从别处拿的,**不对**。
实测 `dmesg | grep -c "Killed process"` 全部累计 30 次,其中 `psi-agent`
在 9-07 之后 **11 次**。已改为准确表述,并补上 docker 谎报与 A 有 swap 两条。

改动只涉及注释文本,`mem_limit: 4500m` 本身未动;A 机栈尚未启动,零副作用。
备份 `/root/prep/docker-compose.yml.bak-<时分秒>`,改后 `docker compose config
--services` 仍列出 4 个服务。

### 连带:任务 #17(chengxx 记忆为空)坐实并写入观察日基线

路径我先前记错了:是 `.psi/memory_tokens.json`,**不在** `.psi/appdata/` 下。
三份对照实测:

| workspace | 大小 | 条目 |
|---|---|---|
| `workspace` | 7,059 B | 42 |
| `workspace-luolin` | 183 B | 1 |
| `workspace-chengxx` | **3 B(就是 `{}`)** | **0** |

**空不是回归,搬家前就没配过。** 已写进 runbook 观察日表:
搬完后 chengxx 若报记忆相关错误,不得当成搬家造成的故障。

---

## 9-09 17:38 502 第二次发作,我漏检了 2 小时 45 分钟

例行体检时发现**公网又是 502**。这次不是新问题,是上一节刚查清的 OOM 的直接后果,
但**我的漏检必须记下来**。

### 时间线

| 时刻 | 事件 |
|---|---|
| 15:38:14 | memcg OOM 杀掉 gateway 的 `psi-agent` 进程(`anon-rss=2931780kB`) |
| 15:39:51 | gateway 自动重启(`RestartCount: 1`) |
| — | oauth-proxy **未重启**(`RestartCount: 0`,`StartedAt` 仍是 14:59:43),孤儿在旧 netns |
| 15:39:51 → 17:40 | **公网 502,约 2 小时 45 分钟** |
| 17:38:28 | 我例行体检时才发现 |
| 17:39:58 | `./restart-stack.sh gateway` 修复完成,RC=0,90 秒 |

### 我的漏检

15:41 我确认 `Schedule runner started: 'todo-ledger-push'` 之后,
**只顺着定时任务和传输进度往下走,没再核过公网状态**。中间还核过 16:00 定时任务
触发成功、9 张卡片全发出去了——**那些都是从容器内部看的**,而故障在 Caddy→8090
这一跳上,内部视角完全看不见。

> **判据教训**:「定时任务能跑、卡片能发」不能推出「公网入口是活的」。
> 两者走的是**完全不同的路径**——定时任务是容器内的 scheduler 自己触发,
> 出站发飞书 API;而 OAuth 回调是**入站**,要经 Caddy → 127.0.0.1:8090 → oauth-proxy。
> 后者挂了,前者照常绿。这与 [[criterion-must-touch-the-layer-it-names]] 同类:
> 判据必须落在它声称的那一层。

### 故障确认(四个层面,证明不是误报)

```
gateway      StartedAt 2026-09-09T07:39:51Z (=15:39:51 CST)  RestartCount: 1
oauth-proxy  StartedAt 2026-09-09T06:59:43Z (=14:59:43 CST)  RestartCount: 0
netns:  gateway net:[4026532616]  ≠  oauth-proxy net:[4026532566]
oauth-proxy 日志末尾:  ======== Running on http://0.0.0.0:8090 ========
gateway 的 ns 内打 8090:  000        <- 那个 ns 里根本没有监听者
```

**oauth-proxy 仍在打印「Running on」而实际不可达**——再次印证
[[oauth-proxy-netns-orphaned-on-gateway-restart]]:**日志不是判据。**

### 修复与验证

`./restart-stack.sh gateway`,RC=0,90 秒。脚本自己识别出
`[restart-stack] gateway 动过了, 跟着重启 oauth-proxy (共享 netns)`——
**脚本是懂这个坑的**,这也说明这个故障模式早已被人踩过并写进了工具。

不只信脚本自检,按四条独立判据核:

| 判据 | 修复前 | 修复后 |
|---|---|---|
| ① netns 是否相同 | `4026532616` ≠ `4026532566` | **两者同为 `4026532616`** |
| ② gateway ns 内打 8090 | `000` | **400** |
| ③ Caddy 带 SNI(`--resolve`) | 502 | **400** |
| ④ **公网**(最强判据) | **502** | **400** |

### 已挂上周期巡检

OOM 每天最多发作 4 次,每次都会造出这个 502,而我不可能全程盯着。
已挂 13 分钟一次的静默巡检:公网得 400 就完全不输出,非 400 才报警并按
netns 判据确认后执行已批准的修复。搬到 A 机(`mem_limit 4500m` + 4G swap)后
诱因预期消除,届时撤掉。

### workspace 传输(17:45)

`tar` 已读 2.27 / 2.57 GB(**88%**),A 侧落地 1,291,026,432 字节。
先前按 0.396 压缩比估的「总量 1.02 GB」偏小了——`.psi/appdata/histories` 那批
jsonl 的压缩率远高于平均,实际包会更大。这不影响判据(判据是 `PIPE_EXIT` +
`zstd -t` + 文件数清单,不依赖预估大小)。

---

## 17:55–18:05 等传输的空档里,取到了 7 个新文件的 md5 基线;顺手推翻自己一条推断

### 结论先行

- `patch-envs.py` 的前提在 B 侧实测成立,脚本可以照原样在阶段 3.3 跑:三份 `.env` 各恰好 1 行 `FUSION_MEMORY_MCP_URL=http://172.19.0.1:8700/mcp`,且**全文没有第二处 `172.19`**,所以它"只改 KEY 那行"不会漏改、`len(hits)!=1` 的 guard 不会误伤。
- PR-797/868 那 7 个文件的 B 侧 md5 基线已取齐并写进手册判据⑤。**手册里原来那句"所以那 7 个文件在包里"是我从目录序反推的,不构成证据,已删除。**
- 传输仍在跑,已推进到 `workspace-luolin`(主 `workspace` 读完),tar 已读 2445 MB。

### 一、路径找错了一次:`schedules/` 不在 `.psi/` 下

我先按 `.psi/schedules/*.json` 去取基线,得到空结果。真实位置是 `workspace/schedules/<name>/TASK.md`,cron 写在 TASK.md 里:

```
workspace/.psi/ 顶层只有: appdata background feishu fusion-flow gateway.url
                          memory_tokens.json(+3 个 bak/lock) rookie_sop todos
```

这个坑和 9-08 记的 `memory_tokens.json` 路径错法一样:**照错路径核会得到"文件不存在",长得像文件丢了,而不是像路径写错了。**已在手册判据⑤ 里显式标注路径,并写明这条误判形态。

### 二、6 条定时任务的基线(md5 前 12 位,LF 归一化)

| schedule | md5 | 字节 | mtime | cron |
|---|---|---|---|---|
| `remind-checkin-gaobo-0950` | `116b36acee7f` | 394 | 08-31 19:44 | `50 9 * * 1-6` |
| `remind-checkout-gaobo-1930` | `100915ee1bb8` | 405 | 08-31 19:45 | `30 19 * * 1-6` |
| `remind-lunch-gaobo-1155` | `4f6c3f76ee89` | 367 | 09-03 13:02 | `55 11 * * *` |
| `todo-ledger-push` | `4522f9281e97` | 3908 | **09-09 15:25:14** | `0 16 * * 1,3,5` |
| `todo-remind` | `aa634b3c242e` | 2664 | **09-09 15:25:16** | `30 14 * * 1,3,5` |
| `todo-writing-check` | `693a38de447f` | 3412 | **09-09 15:25:18** | `0 15 * * 1,3,5` |
| `tools/feishu_todo_compare_card_send.py` | `3cab0e8525e2` | 5007 | **09-09 15:23:54** | — |

现存 6 条,PR 797 涉及的是后三条(`1,3,5` 那组)。前三条 gaobo 提醒是更早的存量,不在本次变更范围。

### 三、被推翻的推断:包里是新版还是旧版,无法从目录序推断

手册里原有一句:

> tar 是在 15:23 投放**之后**才读到 schedules/ 与 tools/ 的(它 12:22 开始,按目录序推进,15:23 时还在 `.psi/appdata/histories`),所以那 7 个文件在包里。

这句的推理方向本身是对的(`.psi` 在字母序最前,`schedules`/`tools` 在后,所以 15:23 时未读到),但**它推不出结论**:三个 TASK.md 的 mtime 是 15:25:14–18,比投放时刻又晚 2 分钟,而 tar 走完 `.psi/appdata/histories` 剩余部分要多久,我没有量。落在投放前还是投放后,只差几分钟,是个真正的竞态。

失效表现极隐蔽,这是它值得单列一条判据的原因:**文件数一个不少、判据④ 全绿,但包里是旧版 TASK.md,搬完后三条定时任务按旧逻辑发卡,没有任何报错。** 解包后逐个核 md5 才能定;不相等就 scp 补这 7 个文件,不重传整包。

### 四、两处我自己的量错(都已即时纠正,记下来是因为方法可复用)

1. **`pgrep -af "tar -cf"` 匹配不到真正的 tar。** 实际命令行是 `tar --exclude-from=... -cf -`,`"tar -cf"` 这个连续子串不存在。我据此报了"tar 已退出、zstd 在收尾",是误判。正确模式是按 `exclude-from=/root/xfer-excludes` 匹配。
2. **量到了 bash 包装器的 pid。** `pgrep -f` 同时命中包装它的 `bash -c`(pid 2492373)和真正的 tar(2492376),前者 `/proc/<pid>/io` 的 rchar 是 0 MB,读起来像"tar 一个字节都没读"。判据:取 pid 后先 `ls -l /proc/<pid>/fd` 看它是否真的开着数据文件。

---

## 18:04–18:15 阶段 3 全部完成;判据⑤ 当场抓到 7 个文件没进包

### 结论先行

- **workspace 传输完成并解包**,阶段 3.1/3.2/3.3 全部闭环。A 机剩 18G。
- **判据⑤ 立刻见效:PR-797/868 的 7 个文件全部没进包。** 三条 PR 797 的 TASK.md 是旧版、PR 871 的 tool 根本不存在。已 scp 补齐 4 个文件并复核 md5 全部命中。**这就是那条被我删掉的错推断本来会造成的后果——文件数一个不少、定时任务按旧逻辑发卡、零报错。**
- 文件数差额 1073 个**全部有解释**,无一是意外丢失:908 部署快照 + 104 `.bak` 备份(按排除清单设计排除)+ 61 传输期新增。
- `patch-envs.py` 跑通:`PATCHED_FILES=3`、`remaining=0`、三份备份齐全。`docker compose config -q` 由恒失败转为 **EXIT=0**。

### 一、传输收尾:`PIPE_EXIT=1 0 0` 里的那个 1 不是故障

`END 2026-09-09T18:04:12`,耗时 5 小时 42 分。tar 退出码 1 的成因是三条 `file changed as we read it`(`psi-debug-138.log`、`psi-debug-9.log`、`.psi/appdata/histories`),全部来自 B 仍在运行时的持续写入,`grep -c "^tar:"` 恰为 3,没有第四条。硬判据 `zstd -t` 通过:2,731,417,600 字节、XXH64 校验无错。

解包同样干净:`PIPE_EXIT=0 0`,`/tmp/untar.err` 为空,27 秒完成(18:06:16→18:06:43)。解包前已确认目标无 `workspace*` 目录。

### 二、判据⑤ 抓到了真问题:竞态输在坏的一侧

A 侧解包后的实测:

| 文件 | A 侧 | B 基线 | |
|---|---|---|---|
| `remind-checkin-gaobo-0950/TASK.md` | `116b36acee7f` 394B | 同 | ✅ |
| `remind-checkout-gaobo-1930/TASK.md` | `100915ee1bb8` 405B | 同 | ✅ |
| `remind-lunch-gaobo-1155/TASK.md` | `4f6c3f76ee89` 367B | 同 | ✅ |
| `todo-ledger-push/TASK.md` | `63fb741833ca` **3083B** | `4522f9281e97` 3908B | ❌ 旧版 |
| `todo-remind/TASK.md` | `a2a2852d08e3` **2214B** | `aa634b3c242e` 2664B | ❌ 旧版 |
| `todo-writing-check/TASK.md` | `6190d3aaa856` **3924B** | `693a38de447f` 3412B | ❌ 旧版 |
| `tools/feishu_todo_compare_card_send.py` | **不存在** | `3cab0e8525e2` 5007B | ❌ 缺失 |

三条 gaobo 旧任务完全相等,这一点很重要:它证明**比对方法本身有效**,后三条的不等不是 LF/编码之类的伪差异。

我原先那句推断(「tar 15:23 时还在 `.psi/appdata/histories`,所以之后才读到 `schedules/`,那 7 个文件在包里」)**方向恰好是反的**:tar 在 15:23 投放之前就已经走过 `schedules/` 与 `tools/` 了。判据④ 的 `tools_py=218`(基线 219)正是这个缺失文件,两条判据互相印证。

处置:从 B 打 20480 字节的 tar 经本地中转补这 4 个文件,`EXTRACT_RC=0`,复核 md5 四项全部命中基线。**没有重传整包。**

### 三、1073 个文件差额的逐项归因(关键:判据脚本与排除清单不一致)

判据④ A 侧:`workspace files=34685 psi=2374 tools_py=218`,比 B 的 35690/2394/219 全线偏小。`files` 差 1005 太多,不能用"B 在持续写日志"打发过去。做了两侧完整清单的 `comm -23`:

| 类别 | 数量 | 判定 |
|---|---|---|
| `skills.pre-*` / `tools.pre-*` / `__pycache__` / `.mcp_cache` / `.pyc` | 908 | 按排除清单设计排除,是 9-07 部署脚本留的旧版快照,非活数据 |
| `*.bak` / `*.bak-*` / `.bak-*` | 104 | **同样是排除清单里的模式**,按设计排除 |
| mtime ≥ 12:22:16(tar 启动时刻) | 61 | 传输期间 B 新写的,正常漂移 |
| 意外丢失 | **0** | — |

**根因是判据脚本与传输排除清单不一致**:`/root/xfer-excludes.txt` 有 10 条模式(含四条 `.bak` 模式),而 `count-ws.sh` 的 `find` 只排 `src.old-*` / `*.wav` / `agent-video`。所以 `files` 必然差这一截,这个差额是判据自身的产物,不是数据问题。

途中一次量错:我用 `[ "$f" -nt /tmp/tar-start-marker ]` 分类新旧,而 marker 文件不存在——`-nt` 对不存在的右操作数恒为真,于是报出"165 个全是传输后新增"。这是判据坏掉的产物。改用 `stat -c %Y` 与 `date -d "12:22:16" +%s` 比绝对秒数后,才得到 61/104 的真实分布。

### 四、阶段 3.3 `patch-envs.py`

跑前先在 B 侧验了脚本前提:三份 `.env` 各恰好 1 行 `FUSION_MEMORY_MCP_URL` 含 `172.19.0.1`,全文无第二处 `172.19` —— 两条 guard 都不会误伤或漏改。执行结果:

```
workspace/.env:           line 11 patched, remaining=0, md5=8065b704c66f
workspace-luolin/.env:    line 21 patched, remaining=0, md5=f0f96758d762
workspace-chengxx/.env:   line 19 patched, remaining=0, md5=5d1f6e1b8a3a
PATCHED_FILES=3 (expected 3)   EXIT=0
```

三份 `.env.bak-subnet` 备份齐全(2909 / 2153 / 1496 字节)。顺带扫了 `.env` 里其他可能写死 B 机 IP 或旧网段的值:**没有**,`grep` 命中的全是注释行(luolin 那三条是解释为什么停用 localhost 回调的说明,`PSI_FEISHU_REDIRECT_URI` 本身已注释掉)。

`docker compose config -q` 由此前的恒失败(`env file …/workspace/.env not found`)转为 **EXIT=0**,A 机 compose 现已可完整解析。

---

## 18:17–18:25 起栈前的最后一道检查,查出手册的切换设计整个是错的

### 结论先行

- **飞书是出站 wss 长连接,不是入站 webhook。** 手册阶段 4 那句「DNS 还没切,外部无感」是错的:飞书事件不经任何公网入口,DNS 管不住它。
- **按原手册起 A 栈就是生产事故**:同一个 `PSI_FEISHU_APP_ID` 两条长连接,两台机器争抢应答同一批真实用户,且无任何报错。
- 因此**没有起 A 栈**。阶段 4/5 已按新结论重写:自测必须先隔离飞书通道;切换顺序必须先停 B 再起 A;DNS 降级为顺序无关的次要动作。

### 一、为什么会去查这件事

阶段 3 做完,下一步是 3.4 `docker compose up -d`。起栈前我要先回答一个问题:**A 起来后会不会立刻抢飞书流量?**

- 若飞书是**入站 webhook**(经 Caddy/DNS),DNS 未切时 A 收不到事件,起栈对生产零影响,可放心自测;
- 若飞书是**出站长连接**,A 一起来就和 B 同时在线,同一批用户的消息被两台机器争抢——直接的生产事故。

这一条不定死不能起栈。

### 二、证据链(结论与手册假设相反)

```
[Lark] [2026-09-09 17:39:19,376] [INFO] connected to
       wss://msg-frontier.feishu.cn/ws/v2?fpid=493&aid=552564&device_id=…&access_key=<hidden>
```

6 小时内 4 次建连:13:39:49 / 15:00:16 / 15:40:28 / 17:39:19 —— **与 gateway 每次 OOM 重启的时刻一一对应**,这本身也印证了长连接是随进程起落的。

长连接实体:`172.19.0.4:48856 → 35.244.241.247:443 ESTABLISHED`,发起者是容器内 pid 138 的 `psi-agent channel feishu --gateway-url http://127.0.0.1:8080 --require-mention`。

三条独立的反面证据也一致:

| 证据 | 实测 |
|---|---|
| gateway 监听端口 | 只有 `127.0.0.1:8080`(**仅回环**)。容器内另一个 `0.0.0.0:8090` 是 oauth-proxy |
| 公网 vhost | 只有 `lark.oauth`,白名单只放行 `/oauth/callback` 与 `/oauth/code`(白名单在 `oauth-proxy.py` 的 `ALLOWED_PATHS`,vhost 里不重复写) |
| Caddy 访问日志近 200 条 | `/oauth/code` 167 次、`/feishu-web/index.html` 15、`/oauth/callback` 9、`/` 4、`/sessions` 2、`/config.json` 1。**没有任何飞书事件路径** |

日志里那些 `POST /events`(18:15–18:16 的 `haitun.assignment.delivery_check`)全部打在 `127.0.0.1:8080`,是容器内部的 trigger 自触发,不是外部事件入口。

### 三、后果与手册改法

`PSI_FEISHU_APP_ID=cli_aaead44976389bb6` 在两台机器上各建一条长连接时,飞书把消息投给其中之一(哪一条不确定),用户会收到重复或错乱的应答。**DNS 层面完全无法隔离**,因为这条流量根本不经 DNS。

阶段 4 加了两个隔离方案:

| 方案 | 做法 | 代价 |
|---|---|---|
| ① 只起 gateway | `up -d gateway oauth-proxy`,不起 `private-*` | 测不到飞书收发链路 |
| ② 临时换测试应用 | 改 A 的 `PSI_FEISHU_APP_ID`/`SECRET`,自测后必须改回 | 要有测试应用;改回漏一处就是搬完不上线 |

**推荐 ①**:4.1–4.5 五项判据没有一项需要飞书通道,而方案 ② 多一次"改回"的漏改风险。

阶段 5 由「切 DNS」改名「长连接交接」,顺序定死为**先停 B 再起 A**(反过来是重叠期抢消息),并新增判据 5.3:必须在 A 的日志里看到 `connected to wss`。**只看容器 `Up` 不够** —— 长连接建不起来时容器照样 Up,日志里也没有 ERROR。DNS 降级为 5.5,标注顺序无关、只影响 OAuth 回调;未生效期的表现是"消息一切正常,但新用户授权失败",那不是搬家失败。

### 四、两处我自己的判据错误(这次差点造成事故)

1. **`grep -vE "172\.(19|2[0-9])\."` 把本端地址也滤掉了。** 我用它排除"本地连接",但所有出站连接的**本端**都是 `172.19.0.4`,于是整条 wss 连接被自己的过滤器吃掉,报出「总 ESTAB=42 本地=42 外网=0」。排除模式只能作用于**对端**。可靠判据是 `docker logs | grep "connected to wss"`,或按对端 IP 直接 grep。
2. **按反解猜 QUIC,猜错了。** `msg-frontier.feishu.cn` 反解出 `direct-http.quic-mix-proxy-gcpsg-v3…`,我据此判断"走 QUIC/UDP,所以 `ss -tan` 看不到"。`ss -uan` 实测 UDP socket 数为 **0**,连接确实是 TCP。反解里的 `quic` 只是字节跳动那套负载均衡的命名,不代表这条连接的协议。

这是今天第三次判据自身出错(前两次:`docker logs --since HH:MM` 静默无效、`-H Host` 打裸 IP 的 https 恒得 000)。三次的共同形态是**判据坏掉时给出的是"一切正常"或"什么都没有",而不是报错**。

---

## 19:29–20:07 停机窗执行:搬家完成,停机 23 分 55 秒

**结论先行:ToB 生产已从 B 机(8.222.255.23)搬到 A 机(47.100.84.197),停机 19:40:28 → 20:04:23 = 23 分 55 秒。** 唯一剩余缺口是 DNS 仍解析到 B,只影响 OAuth 回调、不影响消息收发。

### 一、时间线

| 时刻 | 动作 | 判据 |
|---|---|---|
| 19:29 | A 机装 ToB Caddy vhost | validate 绿但 **reload 失败**,见下文坑① |
| 19:37 | 从 B 复制 lark.oauth 证书 | 三文件 md5 与 B 逐个一致,https 得 502 |
| **19:40:28** | **停 B 栈,停机计时开始** | 4 容器 exited,running=0,公网转 502 |
| 19:41 | B 侧 `pg_dump -Fc -Z6` | RC=0、stderr 空、44M、201 目录条目 / 33 TABLE DATA |
| 19:53–20:00 | dump 经本地中转到 A | 端到端 md5 `2ddd21aa…` 一致 |
| 20:01 | A 侧备份 → drop → create → restore | `RESTORE_RC=0`、stderr 空、18 秒 |
| 20:03:44 | 起 A psi-agent 栈 | 4 容器 running,网段 172.21.0.0/16 |
| **20:04:23** | **`connected to wss`,停机结束** | 长连接在 A 建立 |
| 20:06:42 | 起 A fusion-memory 栈 | 8700→`172.21.0.1`,8701→`127.0.0.1` |

### 二、判据汇总

- **飞书长连接(唯一硬判据)**:`connected to wss://msg-frontier.feishu.cn/ws/v2` @ 20:04:23;独立判据 netns 内 ESTAB → `101.226.41.162:443`。
- **数据库**:33 表逐个 `count(*)` 全相等,**不一致表数 0**。`events` 3081 而 19:40 基线是 3069,差 12 行是基线到 dump 之间 schedule 的收尾写入,A 与 B 当前实测相等,说明 dump 抓的是最终状态。两侧环境对齐:PG 16.15 / `vector 0.8.6` / 单一 `fusion` 超级用户 / 33 表。
- **工具加载**:`Loaded 221 tool(s) from 131 file(s)` = B 基线。
- **内存**:gateway `mem_limit=4500m`(决定④生效),实际占用 1.73G = 39%。B 上撞 3g 被 memcg OOM 的诱因已消除,该项巡检可撤。
- **时区**:容器内 CST + `TZ=Asia/Shanghai`。
- **Caddy 全链路**:8090 与 SNI 均 400。
- **跨栈连通**:psi-agent 三容器 curl `172.21.0.1:8700/mcp` 均 401(网络通、缺认证头属正常),MCP 零报错。
- **真实服务证据**:workspace 在 20:05:50、20:06:12、20:06:34 连续写入三个 `feishu-ou_*` 会话。
- **B 侧静默**:running=0,最新 history mtime 停在 19:40:00 不再增长。
- 启动期 ERROR = 0。

### 三、两个坑

1. **`caddy validate` 绿而 `systemctl reload caddy` 失败,服务仍 `active`。** 真因是 `/var/log/caddy/lark.oauth.access.log` 被我早先 `touch` 成 `root:root 0600`,caddy 以 `caddy` 用户跑打不开。validate 只解析语法、不打开 log writer。改成 `caddy:caddy 0640` 后 reload 即成功。这个组合(validate 绿 + 服务 active + 新 vhost 没生效)很容易读成"装好了"。
2. **起栈 15 秒时量到 8090=502,我误判为端口映射缺失。** 当时只在容器 netns 内查了监听,没查宿主机。实际 `127.0.0.1:8090:8090` 映射与 B 一致,是上游未就绪的瞬态,复核得 400。

另外两处判据措辞问题:①「http 侧应得 502」漏算了我自己写的 http→https 重定向块,实得 301 并非失败;②`pg_restore -l` 喂管道 `/dev/stdin` 拿不到可 seek 的文件,报 0 条目录条目,看着像 dump 损坏 —— 用 `docker cp` 后重验得 201 条。**这两处又是同一形态:判据坏掉时给的是"异常数字"而非报错。**

### 四、未完

**DNS 仍解析到 `8.222.255.23`**(#14,负责人处理)。需把 `lark.oauth` 指向 `47.100.84.197`,**不要动 `account.`**(ToC 已发布客户端硬编码)。当前公网 OAuth 回调 502,而 A 机本地同探针已是 400 —— A 侧全就绪,只等 DNS。证书已复制到 A,DNS 一切过来 https 立即可用,不必等 ACME。

<!-- OPLOG-APPEND-MARKER -->
