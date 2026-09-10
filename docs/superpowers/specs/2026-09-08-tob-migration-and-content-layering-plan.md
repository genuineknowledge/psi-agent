# ToB 搬家与内容分层改造 · 方案与排期

2026-09-08 · 执行人 zsd+Claude · 状态:待负责人确认停机窗与镜像统一

## 结论

三件事捆在一起讨论过,实测后应当**拆成两个会话并行,最后统一部署一次**:

| 线 | 内容 | 代码量 | 何时 |
|---|---|---|---|
| **会话 1** | 搬家(境外 B → 境内 A)+ 生产清理 | 0 行 | 9-08 ~ 9-11 |
| **会话 2** | 内容分层 + 工具架构演进(含 Kanban `22710`) | 内核 + 工具 | 9-08 ~ 9-18 |

阶段 D(飞书工具进度反馈)**已删除** —— 核代码发现 `a1173c75`(#836)早已上线,见下。

搬家结束后正常跑一段时间,这段时间把所有要部署的代码准备好,**最后统一部署一次**。

两条线能真并行,因为搬家用 `docker save`/`docker load` 搬镜像、**不从仓库 HEAD 构建**,
所以本地怎么改都污染不到搬迁结果。

顺序死结只有一个:**收债必须早于改挂载**。官方层变 `:ro` 之后,没入库的内容再也拿不回来。

### 9-08 复核:会话 2 完成的是阶段 A/B,不是全部

负责人问"会话 2 似乎都结束了,排到 9-11"。逐项核过代码,**阶段 C/E 一行未动**:

| 阶段 C 四件事 | 状态(9-08 实测) |
|---|---|
| glob 排序(缺陷①) | **未做**,`tool_registry.py:568` 仍是裸 `async for py_file in tools_anyio.glob("*.py")` |
| 缓存键拆分(缺陷②) | **已修**,`24cab20d`,但**不是按方案的改法** —— 归因错了(详下),真原因是 `load()` 每次传 `old_files=None` 必然全量重编。改进程级 `_module_cache`(键 `(layer_id, file_hash)`)后第二次 load 编译 12 文件/63288 字符 → **0** |
| 多内容根 | **未做**,`content_root` 在 `src/` 出现 **0 次**;`agent.py:364` 原样 |
| `22710` 环路 | **已修**,`c14765a9`,59 个工具文件加载失败归零 |

阶段 E 也未做:`tool_defs.py` 那 176 行硬编码白名单还在。

已落地的是阶段 A/B 一片:`c14765a9`(环路)、`953918a9`(同名工具元数据统一到后加载那份)、
`4dfb9ed5`(跨层私有模块隔离探针,5 个 xfail 推翻卡里 2 处预期)、
`06ecbc6a`(元工具两份一致性成为 CI 门)。

**真风险那一步(阶段 B 隔离实验)已过**,这是序列化点,值得记一笔。但阶段 C 是内核改动主体。

因此排期维持:**会话 1 收在 9-11,会话 2 的 C/E 保持到 9-18,汇合仍在 9-19。**
若要两条线都压到 9-11,须砍阶段 C/E 范围,代价是同一处代码改两遍、判据跑两遍,
且搬迁判据依赖"代码不变",两条线同时收口会让搬迁失败时归因不清。

## 工具架构演进并入会话 2

`docs/haitun-delivery/工具架构演进方案-20260904.html` 的三步(§04)全是本地代码工作,
与内容分层同属会话 2(§05 已完成,见下文修正表)。前置条件已满足:PR #815 已合入(`888b1b1f`),
M2 闸门本体在 main(`src/psi_agent/session/agent.py:61`、`:784-787`,
`src/psi_agent/session/tool_defs.py:54-125`)。

### 删 M2 与分层暴露必须同一次部署

`tool_defs.py:54` 的注释记着实测数字:

> Kept because production measured 285566 → 83725 chars,省 70.7% with this gate on.

所以**不存在"只删 M2"这个可上线状态** —— 删掉而分层暴露没跟上,每回合请求体积涨回三倍多。

文档 §04 说"先删 M2,再做第一步",那是**本地量数据的顺序**:不先删,新判据量到的是 M2
的效果而不是新机制的效果(这个仓库出过"绿而不吃劲"的用例)。上线是一次性的,
两件一起走 —— 统一部署天然满足这个约束。

### 归因靠开关,不靠拆窗口

四项改动一次上线,若延迟变差需要分得清是谁干的。仓库里 `PSI_*` 环境变量开关已是既有模式
(生产 compose 正在用 `PSI_DEBUG_MODULES`),所以给工具暴露机制加一个档位开关:
上线后**不重新部署**就能切档对比,归因不必依赖拆停机窗。

这个开关是本次唯一为可观测性新增的东西,阶段 F 一并做。

## Kanban 卡的处置

| 卡 | 处置 | 依据 |
|---|---|---|
| `2e460` | **删**(产出已 cherry-pick 到 main `905524e4`) | 活已干完,254 行 OOM 诊断文档 |
| `ca8c4` | **删** | 两条缺陷都已不成立,见下 |
| `22710` | **留,并入会话 2** | 与 glob 排序共用同一判据 |

### `ca8c4` 为何不成立

卡称两个工具因类型注解被注册器拒绝。实测:**它们都不是工具**。

- `resolve_feishu_display_names` 真身在 `agents/feishu/tools/_assignment_display.py:31`。
  文件名带下划线,`src/psi_agent/session/tool_registry.py:558` 明确
  `if py_file.name.startswith("_"): continue` —— 从不进扫描,`set[str]` 走不到类型检查。
- `load_channel_event_defs` 在 `agents/feishu/tools/channel_event_check.py:26` 已改为
  `as _load_channel_event_defs`,修复在 `30e0f28a`(8-31)。代码里就留着理由:

  ```python
  # Aliased under a leading underscore: the loader treats every public async
  # name in this module as a tool, and this helper's ``Path`` parameter is not a
  # JSON-Schema type, so exposing it would fail signature conversion.
  ```

卡里"已核实该检查在新镜像里原样保留"这句话本身对,但它核的是**内核的检查还在**,
不是**这两个函数还在被扫**。检查还在 ≠ 还撞它。判据搭错了层。

残留一处值得留意:`agents/feishu/tools/positive_negative_list.py:20` 是裸导入
`from _assignment_display import resolve_feishu_display_names`(不带下划线别名),
9-05 `34c73c65` 引入。它依赖 `sys.path` 里有 tools 目录 —— 属于 `22710` 的同族机制,
由 `22710` 覆盖,不单独立卡。

### `22710` 为何必须并入

循环导入在 HEAD 原样存在:

```
agents/feishu/tools/_feishu_impl.py:1031          _LEDGER_SCHEMA_FIELDS,
agents/feishu/tools/_feishu/mentor_ledger.py:31   import _feishu_impl as _core
```

59 个工具文件挂在上面。卡里已实测出判据对:先导 `_feishu_impl` 通 / 直接导
`mentor_ledger` 不通。而会话 2 要把 `tool_registry.py:558` 的无序 glob 改成 sorted ——
改的正是决定谁先谁后的那件事。分开做会互相打脸。

## 基线冻结(已完成)

生产是移动靶:agent 仍在往内容层写文件,用户仍在聊。48 个待收债文件是 9-08 的快照,
拖下去会漂移,搬家后基准还会从 B 机变成 A 机。所以先冻结:

- 快照:49 个文件 / 154,378 字节,包 md5 `43b7f51811df6a47aa343dca0730ea97`
- 逐文件清单:`MANIFEST.md5`(49 行)

之后收债一律对冻结版做,与搬家彻底解耦。

## 会话 1|搬家 + 生产清理

服务器侧,0 行仓库代码。B = 8.222.255.23(境外,现生产),A = 47.100.84.197(境内,目标)。

搬迁背景已核实:阿里云对未备案域名的 403 管控**已消失**(境外打境内 80 得 301),
搬回境内的唯一障碍解除。搬迁是**搬 IP 不搬域名** ——
`account.genuineknowledge.cn` 已硬编码进已发布的 ToC 客户端,只能改 DNS 指向。

### 阶段 0 · 9-08 · 前置(约 1 小时)

一句话:给 A 机装上搬家需要的通路和内存缓冲。

- B 上 `ssh-keygen` 生成密钥对,公钥追加到 A 的 `authorized_keys`。
  现状:B `/root/.ssh/` 只有 `authorized_keys`、无私钥;A 的 sshd
  `permitrootlogin yes` / `pubkeyauthentication yes`,B→A TCP 22 开放
  (`SSH-2.0-OpenSSH_10.2p1 Ubuntu-2ubuntu3.6`);rsync 两侧都有(B 3.2.7 / A 3.4.1)。
  **只缺一个密钥对。**
- A 机加 4G swap。依据:`2e460` 的诊断坐实 OOM 是撞 3G `mem_limit`,
  独立复核为 dmesg **28 次** OOM(最近 9-08 09:40)、gateway RestartCount=7、
  `OOMKilled=false`(子进程被杀的陷阱,所以这类故障零告警)。不加 swap 搬过去必然复现。

判据:B 上 `ssh root@47.100.84.197 true` 返回 0;A 上 `free -h` 显示 swap ≥ 4G。

**密钥对已建**(负责人授权):B `/root/.ssh/id_migrate`,公钥已追加到 A
(`authorized_keys` 现 4 行),实测连通 `B_TO_A_OK`。

#### 纠正:A 机不是空机

原方案称 A 是空机。实测 A 上正在跑:

- `psi-cloud`(3 周,healthy,:8081)
- Caddy active,且**已签发** `account.genuineknowledge.cn` 的 LE 证书
- `/opt/guoshu-weekly`:另一个 psi-agent gateway(:8766)+ BFF uvicorn
  (**`0.0.0.0:8080`,公网可达且无鉴权** —— 已单独告知负责人)
- `mysqld`、`python server.py :18901`

内存也不够:ToB 栈实测 **3.55G**,A 已用 1.6G / 共 7.1G。
负责人决定:**停/删 A 上的 psi-cloud**(B 上那份保留继续用)、
**保留 guoshu-weekly**(是 demo,后续要删,本次搬迁**不能影响、更不能与之耦合**)、
只搬 ToB 那几个容器,加 4G swap。

#### 网段冲突:172.19.0.0/16 已被占

B 的 `psi-agent_default` 是 **172.19.0.0/16**,且三份 `.env` 都硬编码
`FUSION_MEMORY_MCP_URL=http://172.19.0.1:8700/mcp`;
A 上这个 /16 被 `psi-cloud_default` 占着。停掉 psi-cloud 能腾出来,
但**分配顺序不是保证**。按负责人要求"要有验证和兜底手段":

- 验证:起栈后 `docker network inspect psi-agent_default` 确认 Subnet 真是 172.19.0.0/16
- 兜底:compose 里显式声明 `ipam.config.subnet`,不赌自动分配

#### B 上的 lark.oauth 现在就是 502

`oauth-proxy` 挂在死 netns 上(proxy Up 7h vs gateway Up 4h),
所以阶段 3 的判据"ToC 能登录"**在动手前就已经不可能通过**。负责人选择**先修**。

### 阶段 1 · 9-08 · 搬镜像与数据(实测约 5.2 小时,几乎全是等)

一句话:把镜像和三份 workspace 原样搬到 A 机,顺手把垃圾留在原地。

#### 纠正:"生产领先 main"是反的

原文写"生产有 6 个 src 文件领先 main,重建会静默丢掉 meeting-session"。
把生产容器内 125 个 `.py` 全部 LF 归一化后逐个比对 main,实测:

- **生产没有任何 main 缺的文件**(6 处差异全是 main 更新);
- 生产 gateway 跑的 `meeting-fix-main-a1bc44d9-20260907-1154` = commit `a1bc44d9`
  (`fix(haitun): prevent stale meeting transcript delivery (#851)`);
- **#851 已在 main**。按 commit message 搜 `#851` 零命中(squash 会重写 message/SHA,
  `--is-ancestor` 报 NO 同理),必须按内容判:#851 新增行的 48 个标识符 45 个命中 main,
  缺的 3 个(`active_names`/`task_dir`/`task_path`)是**换了实现**——
  a1bc44d9 按目录名清理过期 `TASK.md`,main 改为 `should_process_recording()`
  按 `record_file_id` 去重,更直接。那三个文件 main 是 598 增 / 116 删。

所以 main 是**单向领先**,不存在双向丢失。main 相对 a1bc44d9 多出的代码差异
(`git diff --stat a1bc44d9 main -- src/` = 14 文件 / 26 增 / 453 删):

| 项 | main HEAD | a1bc44d9(生产镜像) |
|---|---|---|
| `positive_negative_rules` / `meeting_pipeline_run` / `meeting_session_notify` / `meeting_transcript_prepare` | 有 | **无** |
| `tool_registry.get()` 里 `reversed(self._files...)` | 有 | **无** |
| `PSI_SEED_SCHEDULES_WORKSPACE` | 4 处 | 2 处 |

`src/` 未提交改动 0 行,所以这些差异都来自 commit,不是会话 2 的本地活。

#### 纠正:A 机不是"连不上镜像源"

原文据"A 连不上 `registry-1.docker.io`"否掉了在 A 构建。复测(A 机 curl):

| 目标 | 结果 |
|---|---|
| `registry.cn-hangzhou.aliyuncs.com/v2/` | **401, 0.07s**(可达,`/v2/` 要鉴权属正常) |
| `github.com` | **200, 0.70s** |
| `gitee.com` | 200, 0.74s |
| `pypi.tuna` / `mirrors.aliyun.com/pypi` | 200, 3.6~3.9s |
| `mirrors.aliyun.com/ubuntu/` | 200, 0.07s |
| `registry-1.docker.io` | 000 超时(官方源确实不通) |
| `docker.mirrors.ustc.edu.cn` / `mirror.ccs.tencentyun.com` | 000 不通 |

A 的 `/etc/docker/daemon.json` 已配 registry-mirrors。**"在 A 构建"是候选项,不是禁区。**
但 curl 200 只证明 TLS 可达,**不证明 build 能跑完** ——
真实 `docker pull` + `git clone` 未跑,这一条按**未验证**记账。

#### 镜像:三个镜像 829M 就是底线,合并省不下来

原设想"三镜像 1.73G → 单镜像 0.45G,省 1.3G"。实测 `docker save | zstd -3 | wc -c`:

| 组合 | 字节 |
|---|---|
| 单 `meeting-fix` | 453431896(433M) |
| `meeting-fix` + `local` | 868019156(828M) |
| `meeting-fix` + `86d5f755` | 868844498(828M) |
| 三个全带 | 868891431(829M) |

`local` 与 `86d5f755` 几乎完全共享层,**第三个镜像只多 872KB**。
395M 差价全在 `meeting-fix` 与另两个之间,成因是**两代代码**:

- `meeting-fix`:125 个 `.py`,有 `gateway/desktop/` 整个子包、`_card_markers.py`、`_workspace_paths.py`
- `86d5f755`:103 个 `.py`,`gateway/` 下平铺 `_ai_manager.py`/`_auth_manager.py`/`server.py`
  —— 即 `gateway/desktop/` 拆包**之前**的布局

负责人指出成因:架构重排后部署时没重新构建私有容器的镜像。与实测吻合。
compose 里 `image: psi-agent-gateway:86d5f755  # 与主容器同镜像` 这句注释**已过时**。

结论:**不要为省 1MB 去改 compose 引入风险。** 镜像这块占 829M / 约 1.7 小时。

另:`docker save` 必须**一次调用带全部镜像**(管道 1.73G vs 分开写盘 5.6G,约 70% 去重);
B 盘只剩 7.7G/40G,**必须流式,不能落盘**。

清理就在这一步做 —— 搬家天然是一次重装,不带走等于清掉,零风险:

- 不搬:部署目录 17 个备份文件、内容层 4 个备份文件、快照目录
  `skills.pre-*`(42M)、`tools.pre-*`(4.7M)
- A 机目录**从第一天就按三段建好**:`/official`、`/enterprise`、`/users`
  (先都可写,阶段 G 再改 `:ro`)。这样后续改挂载只改 compose 一行,不用挪数据。

#### 跨境带宽是真瓶颈:实测 134 KB/s

| 量法 | 结果 |
|---|---|
| B→A 单流 20MB | 152s |
| B→A 8 并发共 32MB | 249s = **131 KB/s 合计**(每流 16 KB/s) |
| A 主动拉 B | 141 KB/s |
| 对照:B 拉阿里云镜像源 | 2.4 MB/s |

所以是**链路总量封顶**,不是每流限速、不是方向不对称、不是机器问题。加并发无效。

压缩能救回大约一半(zstd-3 实测):

| 项 | 原始 | 压缩后 |
|---|---|---|
| `workspace`(已砍 agent-video + wav) | 2702295040(2.52G) | 1425171013(1.33G)48% |
| `workspace-luolin` + `workspace-chengxx` | 333987840(319M) | 159266446(152M) |
| 三镜像 `docker save` 一次管道出 | 1.73G | 868891431(829M) |
| pgvector `pg_dump -Fc` | 182M | 44.5M(8.1s) |
| fusion-memory 仓库 | 83M | ~77M |

合计约 **2.39G ≈ 5.2 小时**(不压缩 9.8 小时)。上次搬迁的 `sync-data.sh` 用 `RS_FLAGS="-a"`
**不带压缩**,且它记的 50Mbps 是"境内内网 → 境外"这条**不同的路**,不适用于 B→A。

因此:**全量传输不进停机窗**,停机窗只走增量。

#### 判据:文件数必须现场取,不能写成常数

原文的 38747/1314/1129 已过期。9-08 实测 **39722 / 1352 / 1160** —— 生产是移动靶,
agent 还在写、用户还在聊。同理工具数原文写 200/93,实测 **220/130、277/98、205/98**
(三个容器各不同,`86d5f755` 与 `meeting-fix` 本就不是一套代码)。

**判据改为:停机前一刻现场逐容器取一次基线,搬后逐容器比对。** 不写死数字。

加上:A 机 `docker images` 出现目标 tag 且 digest 与 B 一致;
那 49 个收债文件在 A 机的 md5 与冻结清单逐条一致(**比对前做 LF 归一化** ——
生产 LF、仓库 CRLF,裸比对会报几乎全不一致,已出过一次错误判断)。

#### 漏项:fusion-memory 全文未提,而阶段 2 的判据依赖它

原方案通篇没有 fusion-memory,但阶段 2 写着"memory MCP 连得上"。它实际包含:
两个宿主 systemd unit、一个 **83M 且无 remote** 的 git 仓库(`pip install -e` 装的)、
`fm-secrets`、两个 env 文件、`/root/fusion-memory-embed-proxy.py`,
以及 pgvector(182MB / 33 表)。

它的 venv **搬不动**:`pyvenv.cfg` 写死 `/usr/bin/python3.12`,B 是 3.12.3 而 A 是 3.14.4。
负责人选择**容器化**。可行性已核:依赖只有 `aiohttp`/`anyio`/`mcp`/`psycopg2-binary`,
proxy 是纯标准库,`python:3.12-slim` 即可,A 宿主的 3.14 不相关。

### 阶段 2 · 9-09 · A 机起栈自测(约 2 小时)

一句话:在 A 机把栈拉起来但**不接飞书通道**,自己验一遍再决定切不切。

飞书一个 App 只能维持一条出向 WS,所以承载通道的新栈必须在旧栈停掉之后才能起。
这一阶段先起不带通道的,专验容器内部。

判据(都来自已知会踩的坑):

- `curl 127.0.0.1:8090` 通
- 工具数与**停机前现场取的那份基线**逐容器一致(不写死数字,理由见阶段 1)。
  漏配 `PYTHONPATH=/workspace/tools` 会让工具数掉下来
- memory MCP 连得上 —— `FUSION_MEMORY_MCP_URL` 网段不对会**静默**杀掉记忆
  (且 fusion-memory 本身是原方案的漏项,见阶段 1)
- `docker network inspect` 确认网段真是 172.19.0.0/16

同日:DNS TTL 从 **599s 降到 60s**(方案文档说"需降到 300s",实测仍是 599s)。

### 阶段 3 · 9-10 20:00 · 停机切换(按一小时窗口发通知)

一句话:停旧栈、A 机起带通道的栈、改 DNS 指向。

原文写"预期 8 分钟"、参考实测 6m28s —— 那是**不同链路**的历史数字(境内内网→境外)。
按 134 KB/s,一小时只能搬 ~470MB,而单个 generated `.wav` 就有 46M。
所以主张:**全量在窗口前跑完,窗口内只走增量;通道在停机前先停**,不追增量。
增量到底多大**未验证**(需全量同步后取两次快照才能量),这一条不当结论用。

顺序不可换:停 B 的栈 → A 起带通道的栈 → 改 DNS。
飞书一个 App 只能维持一条出向 WS,新栈必须在旧栈停掉之后才能起。

#### 照着敲的清单(每步标"敲错会怎样")

| # | 操作 | 敲错会怎样 |
|---|---|---|
| 1 | B:`docker compose stop`(先停通道容器) | 顺序反了两栈抢同一条飞书 WS,消息随机丢一半,现象是"有时不回" |
| 2 | B:取最后一次增量,`zstd` 流式推 A | 落盘会撑爆 B(只剩 7.7G) |
| 3 | B:逐容器取工具数/文件数基线,记下来 | 不取就没有比对基准,搬完无法判成败 |
| 4 | A:`docker compose up -d`(A 是新栈,此处 up -d 是正当的) | 网段没显式声明就赌自动分配,memory 静默失联 |
| 5 | A:`docker network inspect` 核 172.19.0.0/16 | 跳过则 `FUSION_MEMORY_MCP_URL` 指错网关,记忆功能静默消失、不报错 |
| 6 | A:逐容器比对第 3 步基线 | 只看"容器 Up"会漏掉 `PYTHONPATH` 漏配这类静默降级 |
| 7 | A:Caddy 用 `caddy reload` | `systemctl restart caddy` 会**中断 ToC** |
| 8 | 改 DNS 指向 A | TTL 没提前降到 60s 则回滚要等 599s |
| 9 | 验:飞书发一句话有回复 + ToC 能登录 | 入口是 `lark.oauth.genuineknowledge.cn` → 8090 oauth-proxy,**不是** `account.*` |

两个不能犯的操作:

- 改**已有容器内**文件只能 `docker cp` + `docker restart`,**绝不能 `docker compose up -d`**
  —— `/app/src` 在镜像里不是挂载,`up -d` 会重建容器并静默丢掉所有改动。
  (A 机首次起栈不在此列:那是新建,没有可丢的改动。)
- 单条 `restart gateway` 会让 `oauth-proxy` 挂在死 netns 上(曾静默 502 了 29 小时);
  B 上 `restart-stack.sh` 存在就是为这个,它轮询 8090 最多 90s 等 HTTP 400,
  注释记着"云端实测约 20-40s 才开始应答"。

#### 回滚

DNS 指回 B + B 上 `docker compose start`。前提是**B 的栈全程不删**,
只 stop 不 down、镜像不清。观察通过之前 B 保持可随时拉起。

### 阶段 4 · 9-11 · 观察日

一句话:只看不动,确认"境内已起来且可用"这个观察点成立。

## 镜像统一:负责人提议,建议前置到搬迁之前

负责人提议:**先在生产上统一镜像,找 luolin/chengxx 两位主人实测,验证可用再搬。**
理由是这两个私有容器只是架构重排后部署时没重新构建。这个提议比原方案好,采纳。

理由:升级私有容器**无论如何都要做一次**,区别只是做的时候有没有回滚余地。
放在搬迁后、停机窗里做,窗口内没人可问、没有基线可比;
放在现在的生产上做,有真人测、`86d5f755` 原地待着随时切回、失败也不影响搬迁排期。

#### 前置条件已实测:四个容器 `/app` 零漂移

| 容器 | `docker diff` 总行 | 落在 `/app` | 当前镜像 |
|---|---|---|---|
| psi-agent-gateway | 123 | **0** | `meeting-fix-main-a1bc44d9-20260907-1154` |
| psi-agent-luolin | 4 | **0** | `86d5f755` |
| psi-agent-chengxx | 4 | **0** | `86d5f755` |
| psi-agent-oauth-proxy | 1 | **0** | `local` |

这条数字决定了 `up -d` 的安全性:硬纪律"绝不能 `up -d`"防的是
`/app/src` 不是挂载、重建会静默丢改动。**这里前提不成立** ——
没人在容器里改过代码,所以这次是**量出来的例外,不是赌**。

`oauth-proxy` 用 `local` 镜像只是当跑 `oauth-proxy.py` 的 python 底座
(它只 import `aiohttp`/`os`/`urllib.parse`)。已实测:把同一个 `oauth-proxy.py`
挂进 `meeting-fix` 镜像能正常起(`Running on http://127.0.0.1:18099`,exit=0)。
`psi-agent run` 子命令两个镜像都有。

#### 统一到哪一版:两个选项

| 选项 | 内容 | 风险 |
|---|---|---|
| **甲** 统一到 `meeting-fix`(= a1bc44d9) | 生产已在跑、已验证的那一版 | 最低,不引入新代码;但拿不到 main 的 17 个新工具 |
| **乙** 从 main 构建新镜像 | 负责人想要的标准部署流程 | 见下两条,都可控 |

既然 main 是单向领先(#851 已在 main,不丢东西),**乙可行**。它的两条真实风险:

1. **工具数判据会变** —— 多 17 个工具名。不是坑,只是搬迁判据要用新基线,
   而新基线可以在切镜像后、搬迁前**当场量出来**。
2. **`PSI_SEED_SCHEDULES_WORKSPACE` 生产没设** —— 我 dump 过 gateway 容器全部
   `PSI_*` 环境变量,没有这一条;而 main 已把 meeting-session 从硬编码
   `PUBLIC_MEETING_SESSION_ID = "meeting-session"` 改成由它驱动的
   `is_org_session(session_id, workspace)`,`_scheduler_manager.py` 注释写明"空 = 关闭"。
   **从 main 构建又不补这个变量,会议自动化会静默关掉。** 必须在 compose 里补上。

第 2 条恰好被负责人的方案覆盖:在生产上先统一、找两位主人实测,
会议链断了主人立刻能发现,而不是搬完在 A 机上排查。

#### 仍未验证的两条(不当结论用)

- `luolin`/`chengxx` 的 `config.yml` 在新镜像里的 **schema 兼容性未验**。
  `86d5f755` → `meeting-fix` 跨了 `gateway/desktop/` 拆包那次重排,
  config 里 `type: session` / `channel_socket` / `appdata` 这些键新代码是否还认同一套,
  没测出来不能说能切。空跑验证是只读的(临时目录 + 假凭据 + `--rm`),不碰生产。
- **A 机能否真正 build 未验**(curl 200 只证明 TLS 可达)。选项乙若要在 A 构建,
  需先跑真实 `docker pull` + `git clone`。

#### 另有一处需查:生产上有两份不同代龄的 src

`/srv/haitun/psi-agent/workspace/genuine-psi-agent/src/` 下还有**第二份** psi_agent 源码,
且那份**已含** `PSI_SEED_SCHEDULES_WORKSPACE`(主份也有 2 处)。
两份不同代龄的 src 在同一台机器上,直接影响"哪份是被加载的那份"这个判断,
而 md5 三层核验的第三层正是靠它。这份是否被 `PYTHONPATH` 捡到,**未查**。

## 需要负责人确认的副作用操作(按时间序)

| 时间 | 操作 | 影响 | 回滚 | 状态 |
|---|---|---|---|---|
| 已做 | B 建密钥对 + 公钥追加到 A | A `authorized_keys` 4 行 | 删该行 | **已授权已做** |
| 9-08 | 修 B 的 `oauth-proxy` 死 netns(502) | 走 `restart-stack.sh`,ToC 登录短暂中断 | 无需 | 负责人已选"先修" |
| 9-08 | A 机停/删 psi-cloud | A 上该服务不可用(B 那份保留) | 重新 `up -d` | 负责人已选 |
| 9-08 | A 机加 4G swap | 无 | `swapoff` | 待确认 |
| 9-09 | **只读**空跑验 config schema 兼容 | 无(`--rm` + 临时目录) | — | 只读,按规则可直接做 |
| 9-09 | 改 compose 两行 `image:` + `up -d private-luolin private-chengxx` | **重建这两个容器**;不碰 gateway,故 oauth-proxy netns 不受影响 | 改回 `86d5f755` 再 `up -d` | **待确认(申请硬纪律例外)** |
| 9-09 | 找两位主人实测 | 占用他们时间 | — | 待确认 |
| 9-10 | DNS TTL 599s → 60s | 无 | 改回 | 待确认 |
| 9-10 20:00 | 停机切换(见阶段 3 清单) | ToB 全停,ToC 登录受影响 | DNS 指回 B + `docker compose start` | **待确认停机窗** |

判据:24 小时内 dmesg 零 OOM;无 502;飞书响应正常。
B 机**保持不动**作为回退 —— 回退只需把 DNS 改回去。

## 会话 2|内容分层 + 工具架构演进

仓库侧,与会话 1 同时进行。阶段 A~C 是内容分层与加载期确定性,E 是分层暴露。

### 核代码后对 `工具架构演进方案` 的修正

文档写于 9-04,四个缺陷 + §05 逐条核过代码:

| 项 | 文档说 | 代码现状 |
|---|---|---|
| §05 飞书进度反馈 | 缺口在 `client.py:551`,建议先做 | **已完成**,`a1173c75`(#836):`channel/feishu/_tool_status.py` 146 行 / 46 别名 / 25 判据 / 变异 13/13。文档的三个约束都已实现,且多处理了并发只报个数、`merge_streaming_text` 去重吃正文开头 |
| ① glob 无排序 | 部分缓解 | **仍无序**,`tool_registry.py:557` 裸 `glob("*.py")` |
| ② 缓存键混淆 | 未解 | **仍在**,`:576` `module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"` |
| ③ 平铺暴露 | 临时门挡着 | 未做,只有 `tool_defs.py` 的 M2 硬编码门 |
| ④ 元工具两份 | 待决 | 确认两份:`agents/{desktop,feishu}/tools/tool_search.py` |

**①② 上移到阶段 C**:它们与工具扫描下沉、`22710` 全落在 `tool_registry.py`
同一片代码,分阶段做等于把同一处改三遍、每遍重跑同一套判据。文档本身也说
①② 与分层暴露"无强依赖,可并行推进"。

### 阶段 A · 9-08 ~ 9-09 · 收债

一句话:把冻结的 49 个文件逐个判定入库还是丢弃。

`agents/feishu/tools/_card_dsl.py` 那 603 行漂移单独查(日志显示 agent 动过 45 次,
不能盲收)。

判据:全量 pytest 落在 **57-62 failed** 基线内 —— 这是基线不是回归
(Windows 上 5 条 session 测试恒失败,数字浮动因硬编码管道名被残留进程占着)。
跑法必须 `PYTHONPATH=src`、`-o testpaths=` **写在路径之前**,否则静默失效。
lint **看退出码,不看输出末尾**。

### 阶段 B · 9-09 ~ 9-10 · 隔离实验(唯一真风险)

一句话:验 `_tools_dir_on_sys_path` 那个全局槽位在跨层时会不会串味。

`src/psi_agent/session/tool_registry.py` 的 stash/restore 假设同一时刻只有一个目录作用域打开;
分层打破这个前提。候选 A(槽位键带层 id)vs 候选 B(禁跨层可见 —— 不可行,派生是核心动作)。

这一步的结果**决定阶段 C 的走法**,是真正的序列化点。

### 阶段 C · 9-11 ~ 9-15 · 内核多内容根 + `tool_registry.py` 一次改到位

一句话:让内核能同时挂多个内容根,并把工具扫描搬出内核,同时把 `tool_registry.py`
上四件事一次改完。

四件事共处一个文件,分阶段做等于把同一处改三遍、每遍重跑同一套判据:

1. **工具扫描下沉**(内容分层方案的第二项改动)
2. **glob 排序**(缺陷①):`tool_registry.py:557` 仍是裸 `glob("*.py")`
3. ~~**缓存键拆分复用与隔离**(缺陷②)~~ —— **已由 `24cab20d` 修掉,且本条归因是错的。**
   原文说"`session_id` 把 `file_hash` 的复用废掉"。实测:`module_name` 只是 `sys.modules`
   的注册键,**复用判定根本不看它** —— 判定在比对 `old_files[str_path].file_hash`,而
   `old_files` 是 `ToolRegistry` 实例内的 `_files`。`load()` 每次新建实例并传
   `old_files=None`,所以复用只在同一实例的 `refresh()` 路径上发生,`load()` 必然全量重编。
   实测 `session_id` 完全相同的两次 `load` 照样全量重编。**照原文只改键,判据永远红不了。**
   实际改法:进程级 `_module_cache`,键 `(layer_id, file_hash)`;键里必须有 `layer_id`,
   否则两个 workspace 里同名同内容的文件会连带拿到对方的 `_priv_helper` 绑定。
4. **`22710` 循环导入**:`_feishu_impl.py:1031` ↔ `_feishu/mentor_ledger.py:31`,
   59 个工具文件挂在上面

多内容根的根因单点在另一个文件:

```python
# src/psi_agent/session/agent.py:364
agent_root = agent_path if agent_path is not None else workspace_path
```

`--content-root` / `content_roots` 在 `src/` 里**出现 0 次** —— 参数还不存在,要新写。

三条红线:

- **`schedules` 那行必须保持 `workspace_path`**,不许"顺手统一成 agent_root" ——
  那会静默丢掉所有用户日程。它是四行里唯一本来就正确的一行。
- **分层只进装表期,绝不进 `get(name)`**。`tool_registry.py:438` 的 `get()`
  是全集可达语义,`agent.py:932` 的工具调度依赖它。**看不见 ≠ 调不到。**
- **glob 排序与 `22710` 必须同批**:改排序就是改加载顺序,而 `22710` 的病灶正是
  加载顺序。分开做会互相打脸。

免费的重载机制已有:`agent.py:705-706` 每回合 `refresh()`。

判据:

- `22710` 那对 A/B 导入顺序实验在改 glob 前后各跑一次。
  变异复核:**故意把 sorted 去掉,确认用例真的红**
- 同一 workspace 起两个会话,第二个的编译文件数显著低于 114。
  变异复核:**把 `session_id` 加回键里,确认用例真的红**
- 全量 pytest 落在 57-62 failed 基线内

### 阶段 E · 9-16 ~ 9-18 · 删 M2 + 正式分层暴露 + 档位开关

一句话:把 45/210 的硬编码临时门换成"常驻核心 + 按域加载"的正式机制,并留一个
生产可切的档位开关。

顺序:**先删 M2,再上新机制**(否则判据量到的是 M2 的效果)。两者同一次部署上线 ——
`tool_defs.py:54` 记着 285566 → 83725 chars,只删不补等于把 70.7% 还回去。

硬约束:**变更只在回合边界发生,一个回合内 tools 绝不变**。
`ToolDefsCache.freeze()` 已经是这个约束的执行点。

**前置:元工具必须先收口(缺陷④)。** 分层暴露靠 `tool_search` 让模型找到没暴露的
工具;而 `agents/{desktop,feishu}/tools/tool_search.py` 两份实现已出过"上游改动
只跟到一侧"的分歧。若分层暴露只跟到一侧,ToB 这边模型就找不到工具 —— 能力直接消失,
不是退化。所以「元工具是否提共享层」这个决定**卡在阶段 E 开工前**。

同时加暴露档位环境变量(见上文"归因靠开关"),让上线后不重部署即可对比。

判据:请求字符数与 M2 时期(83725)同量级或更低;`get(name)` 仍能调到全部 210 个
(**看不见 ≠ 调不到**,这条必须有独立用例,且变异复核要能让它红)。

## 汇合:阶段 G · 9-19 · 统一部署一次

一句话:内容分层的挂载改造 + 工具架构四项改动,一次性上线。

前置**全部满足才动**:会话 1 已落地并观察通过、会话 2 阶段 A~F 全部测完。
且**收债必须已完成** —— 官方层变 `:ro` 后没入库的内容再也拿不回来。

挂载改成 `/official`(ro) + `/enterprise`(ro) + `/users`(rw)。A 机目录在阶段 1
已按三段建好,所以这里只改 compose 一行,不挪数据。

镜像要做**三层核验**:build 机的 src / 镜像内的产物 / 容器内实际加载的。
第三层是 8-18 那次事故缺的那层。

上线后用阶段 F 的档位开关做归因对比,不需要额外停机。

## 清理不再复发的机制

生产乱象的实测归因(用 mtime 纳秒位区分:整秒 = 部署投放,带纳秒 = 就地写入):

- 人的乱象**大于** agent 的乱象:部署目录 17 个备份 + 内容层 4 个 + 2 个快照目录
  vs agent 就地写入 14 个文件
- `agents/feishu/tools/_runtime_paths.py:101` 的 `refuse_agent_write` **是个空操作**,
  两个原因叠加:① 只有 3 个文件调它(自己、`write_excel.py`、`write_word.py`),
  真正的写入通道 `bash.py`/`python_run.py`/`edit.py`/`write.py`/`powershell.py`
  **都不调**;② 生产把两个根都设成 `/workspace`,`agent_root != ws_root` 恒为假

结论:**`:ro` 挂载才是真正的执行点**,bash 绕不过去;应用层守卫只提供可读的报错,不是防线。
Plan B 的挂载分离本身就是解药。

另外要纠正一个说法:agent 往官方层写,不是"同事错误地让 agent 去改东西" ——
是因为**当时没有个人层可写**。给了个人层,这个动机就消失了。

## 需要负责人确认

副作用操作的完整时间序见上文「需要负责人确认的副作用操作」表。此处只列需要决断的事:

0. **镜像统一到哪一版**:甲(`meeting-fix`,最低风险)还是乙(从 main 构建,拿到标准部署流程
   但须补 `PSI_SEED_SCHEDULES_WORKSPACE`)。另需批准一次**硬纪律例外**:
   切镜像必须 `up -d`,已实测四容器 `/app` 零漂移。
1. **阶段 0 的副作用操作**:A 机加 4G swap(密钥对已做;"A 是空机"已被推翻,见阶段 0)
2. **停机窗 9-10 20:00** 是否可以 —— 注意全量传输 5.2 小时**不进窗口**,窗口只走增量
3. **元工具是否提为共享层**(`工具架构演进方案` §06 唯一点名要你定的事)。
   文档建议:只把协议契约(工具描述格式、调用信封)提共享,不共享工具集合与提示词。
   它是**阶段 E 的前置而非落点选择** —— 分层暴露若只跟到一侧,ToB 这边模型
   就找不到工具。请在 **9-15 前**给我。
4. **按域划分的粒度**(几个域、怎么分)。文档说"随第一步设计一并定" ——
   我在阶段 E 动手前把方案发你过目,不需要你现在决定。

## 待办(非阻塞)

- 与实习生对齐 ToC 时序:双方都动 `agent.py:364`,内核改动 9-15 落地后他 rebase
- 在阿里云控制台确认备案号 —— 目前只证明了 403 管控不在,属间接证据
- 与实习生对齐 ToC 时序另需你帮着通气(见上)

