# 出向跨容器发文件：独立容器的文件让飞书侧可点击下载

**描述：** 修复独立容器（`psi-agent-luolin` / `psi-agent-chengxx`）里的 agent 生成文件后，飞书侧收不到可点击下载附件的问题。出向链路改为「session 供字节 → channel 上传」，不依赖共享文件系统。

**版本号：** 1.4

**状态：** 已交付（V1/V2/V4/V5/V6 通过，V3 经用户决定不验；已发布生产并完成收尾）

**适用范围：** psi-agent 出向文件链路（`[SEND:]` → 飞书附件），生产为新加坡节点 account.genuineknowledge.cn

**关键词：** 出向、跨容器、FileChunk、MediaSource buffer、external-sessions

**创建人：** @zsd

**审核人：** @待补

**关联文档：**

- `docs/superpowers/specs/2026-08-22-external-container-recovery-plan.md` —— 上一轮把出向列为观察项 V11 并「先不管」，本任务承接
- `docs/onboarding/真知开发执行SOP-v1.0.md` —— 本文档结构依据

***

## W —— 是什么

### 1. 解决谁的什么痛点

罗霖与成 xx 的飞书私聊被路由到各自的独立容器（`PSI_FEISHU_EXTERNAL_SESSIONS`）。这两人的
agent 生成文件后，**飞书侧收不到可点击下载的附件**。

2026-08-22 成 xx 实测：要一份可下载的 md，agent 回「文件已存在于工作区，直接发送即可」，
然后没有附件。生产日志同刻实证（`docker logs psi-agent-gateway`，本次复量仍在）：

```
[Lark] [2026-08-22 18:37:29,892] [WARNING] outbound: materialize blocked:
could not read local file '/workspace/真知问题解决与求助SOP（优化版）.md':
[Errno 2] No such file or directory
```

机制：飞书 WS 长连接同一 App 只允许一条，所以 channel 只能跑在 gateway 容器里；而出向
上传是**拿路径读本地文件**。三个容器各挂自己的宿主目录到 `/workspace`（生产
`docker inspect` 本次复核）：

| 容器 | 宿主目录 | 容器内 |
|---|---|---|
| `psi-agent-gateway` | `/srv/haitun/psi-agent/workspace` | `/workspace` |
| `psi-agent-luolin` | `/srv/haitun/psi-agent/workspace-luolin` | `/workspace` |
| `psi-agent-chengxx` | `/srv/haitun/psi-agent/workspace-chengxx` | `/workspace` |

独立容器的 agent 输出 `[SEND:/workspace/x.md]`，gateway 拿这个路径去读**自己的**
`/workspace` —— 那是另一个卷。同名不同物、多数情况直接不存在。

**受损面不止「少一个附件」。** 上传失败后 marker 不被消费，`[SEND:/workspace/...]`
原样当文本发给用户（8-22 11:12 成 xx 收到的就是这行裸标记）；agent 那边以为发成功了，
于是对用户说「已发送」。用户看到的是自相矛盾的两条消息。

这条链路对**本地** session 一直是好的，只在跨容器时坏 —— 上一轮修好了入向（用户发文件
给 agent），出向是同一堵墙的另一面，当时负责人定「先不管」，本任务承接。

### 2. 做完什么样算完（验收标准，可判定）

| 编号 | 验收标准 | 判定方式 |
|---|---|---|
| **V1** | 独立容器的 agent 生成一个**新名字**的文件并发送，飞书侧收到可点击下载的附件 | 真实飞书消息驱动，人眼确认附件可点击下载。**文件名必须是本次新造的** —— 生产上 `/workspace/真知问题解决与求助SOP（优化版）.md` 有硬链接（inode 701477，本次复核 link count = 2），用那个名字验会假绿 |
| **V2** | `psi-agent-luolin` 容器验过 | 同 V1 判法，独立记一次 |
| **V3** | `psi-agent-chengxx` 容器验过 | 同 V1 判法，独立记一次 |
| **V4** | 有覆盖出向跨容器路径的测试 | 新增测试在本机通过；**必须能在修复前失败** —— 提交前先 stash 掉产品代码跑一次，确认红 |
| **V5** | 本地 session 的出向发文件未被破坏 | 既有 `test_feishu.py` / `test__core.py` 相关用例仍通过，且本地路径仍走「直接交路径给 SDK」不产生额外 HTTP 请求 |
| **V6** | 私密区守卫仍然有效 | `_private_space.blocks_send` 的既有用例仍通过；新增路径不绕过它 |

V1-V3 靠真实飞书消息驱动（gateway 的飞书 WS 收到消息才会走到出向链路），无法用脚本代替。

### 3. 明确不做什么

- **不撤生产上两处临时改动。** 硬链接（inode 701477）与 build 机到生产的多余 ssh 公钥
  `haitun1-build-to-prod`，等本任务验收通过后另行撤除。硬链接尤其不能现在撤 —— 它是
  V1 假绿的来源，但撤它属另一件事，本任务只保证「不用那个文件名验证」。
- **不给容器挂共享卷。** 见 H 段候选 B 的取舍。
- **不改入向链路。** `_attachment_handoff`（`client.py:300`）已解决入向，本次不动。
- **不开 PR。** 用户统一提。
- **不做出向的通用「文件服务」抽象。** 只解决 `[SEND:]` 这一条出向路径；不引入
  下载链接、不做文件缓存、不做断点续传。

***

## H —— 怎么做

### 4. 有哪几种做法，为什么选这个

先定位到真正的那一层。出向链路（行号本次逐个核过，见下方核对结论）：

1. `channel/_core.py:88` 实例化 `SendMarkerScanner`，`:126` 喂流式 content
2. `channel/_markers.py:41` 的 scanner 扫出 `[SEND:/path]` → `_types.py:9` 的
   `FileChunk(path)`，只带一个 `path` 字段
3. `channel/feishu/client.py:490` 收到 `FileChunk`，过私密区守卫后调 `_send_file`
4. `client.py:193-200` 的 `_send_file`：`channel.send(chat_id, {"image": {"source": path}})`，
   失败再 `{"file": {"source": path}}`

**关键事实：读本地文件的不是我们的代码，是 SDK。** `{"source": <str>}` 经
`lark_channel/channel/_coerce.py:165-170` 判成 `MediaSource(kind="file", path=…)`，再由
`channel/outbound/media/uploader.py:148-165` 在 gateway 进程里 `Path(path).read_bytes()`。
失败即 `sender.py:430` 打出 `materialize blocked`。

而同一个 `_coerce.py:156-164`：**`bytes` 会被判成 `MediaSource(kind="buffer")`**，
`uploader.py:146-147` 直接用这段字节上传，不碰文件系统。生产镜像内的 SDK（1.2.0）
本次已逐条核过这三处都在。**这决定了修复的落点：让 channel 手里有字节，而不是让
gateway 能看见对方的文件系统。**

候选方案：

**A（选定）· session 开 `GET /files`，channel 取字节再上传。**
独立容器的 session 已经在 `http://0.0.0.0:8081` 上服务 `POST /chat/completions` 与
`POST /events`（`session/server.py:30-31`，容器内 `config.yml:19`）。加一个
`GET /files?path=…` 返回字节流；`FileChunk` 带上来源地址；`_send_file` 在有地址时
先取字节、把 `bytes` 交给 SDK。

- 优点：不动部署拓扑、不停机、不改 compose。复用已经打通且正在承载全部消息流量的
  同一条 HTTP 通道 —— 那条通道通不通，本身就是消息能不能到的前提，不新增故障模式。
  同名不同物的问题被彻底消除：字节来自哪个容器是显式的，不再靠路径碰运气。
- 优点：本地 session 完全不受影响 —— 没有来源地址时走原路径（直接交路径给 SDK），
  连一次多余的 HTTP 请求都不产生。
- 代价：session 多一个端点，多一份路径包含判定要写对。字节要过一次内存
  （飞书文件上限 30MB，可接受；设上限拦住误传大文件）。
- 代价：`FileChunk` 多一个字段。这是**信息补全**而非兜路 —— 一个「要传输的文件」
  在跨容器世界里，光有 path 本就不足以定位，缺的就是「在谁那儿」。

**B · 给三个容器挂一个共享卷。**

- 优点：零代码改动。
- 代价：改 compose + 重启全部三个容器（含连带重建 oauth-proxy），有停机。
- 致命代价：**没有真正解决问题。** 三个容器的 `/workspace` 各是各的根，
  `/workspace/x.md` 在两个容器里指不同文件；共享卷得挂在**另一个**路径下，于是 agent
  必须学会「要发的文件得先拷到共享目录」。等于把机制问题转嫁成 prompt 约定 —— agent
  忘了拷就静默失败，而这正是当前故障的形态。且它反向打穿了独立容器的文件系统隔离
  （那是这套部署存在的理由）。
- 结论：绕路，且换来的隔离损失比省下的代码多。否决。

**C · 独立容器自己上传到飞书，回传 `file_key`。**
两个独立容器确实有 `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`（本次核过），
技术上可行；`_coerce.py:168-169` 也认 `file_`/`img_` 前缀的 key，channel 侧几乎零改动。

- 优点：字节不过 gateway，省一跳。
- 代价：把「飞书」这个具体渠道塞进 session 层。session 现在对渠道一无所知 ——
  `FileChunk` 是渠道中立的，telegram 也在用（`channel/telegram/client.py:127`）。
  按 C 做，session 得知道自己的输出要发去飞书、得持有飞书凭据、得处理 image/file
  两种上传 —— 未来接第三个渠道要再来一遍。
- 代价：`file_key` 有效期与幂等语义要另行确认，多一个待验的未知。
- 结论：省的那一跳换来一层错位的耦合，不值。否决。

**判断标准的优先级**（按负责人既有取向）：① 不动生产拓扑、无停机优先；
② 结构上消除问题，而不是加约定绕开；③ 抽象要名副其实 —— `FileChunk` 加的是它
本就该有的信息，不是给它挂一个新职责。A 在三条上都占优。

### 5. 别人怎么做的，我这样是否更好

**仓内既有惯例（最强对标，且是同一堵墙的另一面）：** 入向已经这么解决过。
`client.py:300` 的 `_attachment_handoff` 的做法是「不在本容器下载，把**协议事实**
（`message_id`/`file_key`）交给对端容器，由它自取」。方向恰好互为镜像：入向是
「谁要用谁去取」，出向是「谁有谁来供」。两边共同的原则是**不假装两个容器共享文件系统**。
本方案与之同构，不新造第二套世界观。

**仓内既有惯例（端点形状）：** gateway 早有 `GET /workspace/file`
（`gateway/server.py:290` → `_workspace_manager.py:132-152`）：给路径、可选给 root，
`resolve()` 后判包含、越界抛 `PermissionError`。新端点照抄这套判定，不自创一套路径校验。

**业界：** 这是容器化 IM 机器人的常见形态 —— 收发进程与工作进程分离时，附件靠
对象存储或内部 HTTP 传字节，而非共享挂载（共享挂载会把隔离打穿，正是候选 B 的问题）。
我们没有对象存储，内部 HTTP 是同类做法里最轻的一档。

**友商：无直接对标及理由。** 「一个飞书 App 的 WS 单连接 + 每人一个独立容器」这个
拓扑是本项目为绕备案与换隔离而临时形成的（见记忆：部署拓扑是过渡态），公开产品里
找不到同形状的实现可比。故只对标业界通用手法与仓内惯例。

### 开工前核对诊断（触发式要求）

上游诊断由别的会话写于 8-22。本次开工前逐条核到代码与生产，**4 处不符**：

1. **「`source: path` 似乎是让 gateway 自己去打开该路径」—— 方向对，落点错。**
   不是我们的代码去 open，是 SDK 的 `kind="file"` coercion
   （`_coerce.py:165-170` → `uploader.py:148-165`）。差别是实质的：修复不该去改写路径，
   而该在调用点换 source 形态。**正因为看清这一层，才发现 SDK 本来就收 `bytes`
   （`_coerce.py:156-164`），修复代价从「挂卷/改拓扑」降到「几十行代码」。**

2. **「grep 出向日志零命中，可能说明压根没走到 `_send_file`」—— 判据无效，结论也不对。**
   `_send_file` 里那三行（`as image` / `image rejected` / `trying file`）全是
   `logger.debug`，而生产 72h 日志里 **DEBUG 行数为 0**（同期我们自己的 INFO 行 164 条，
   说明日志在正常输出，只是级别到不了 DEBUG）。用 DEBUG 关键词去证「没走到」，
   在生产日志级别下永远零命中。有效判据是 SDK 的 WARNING：`materialize blocked`
   —— 它 72h 内 **13 次命中**。**结论相反：确实走到了 `_send_file`，且失败在上传。**

3. **上游文档「`materialize`/`outbound` 这两个字符串在生产 `/app/src` 全树都不存在，
   那行日志来自 workspace 层的工具，不是 gateway」—— 错。**
   来源是 `lark_channel/channel/outbound/sender.py:430`，即 gateway 进程内的 SDK。
   「不在 `/app/src`」这个观察本身没错，但推论错了：SDK 不在 `/app/src` 底下。

4. **13 次 `materialize blocked` 里只有 4 次是本 bug。** 另外 9 次是
   `code=234011 Can't recognize image format` —— `_send_file` 先试 image 的探测性失败，
   之后 fallback 到 file 会成功，属正常噪声。**排查时不能把 13 当作故障次数**，
   否则会误判影响面。

核对**相符**的部分（不再复核）：上游给的 6 处 file:line 全部成立；三个容器的挂载与
`PSI_FEISHU_EXTERNAL_SESSIONS` 配置与上游描述一致；`_send_markers.py:29-41` 确实已有
空路径过滤，裸 `[SEND:]` 不会进到 `_send_file`（上游提示「先读」的那处注记已读，
guard 已在，无需补）。

生产侧本次新量到、上游没有的两条：三个容器同在 `psi-agent_default` 网络
（`172.19.0.2/3/4`），gateway 到两个独立容器的 `8081` **实测可达**（打空 body 得 HTTP 400，
即服务在、只是拒绝空请求）—— 方案 A 的前提成立。宿主的 `127.0.0.1:8081` 是 `psi-cloud`
占用，与独立容器的 8081 不冲突（后者未映射到宿主，仅 docker 网络内可达）。

***

## A —— 执行过程

技术方案即本文档 W/H 两段，落地步骤见 `docs/superpowers/plans/2026-08-24-outbound-cross-container-file.md`。本段只列落点与路径，不复述设计。

### 代码落点

| 层 | 位置 | 做了什么 |
|---|---|---|
| Session（新） | `src/psi_agent/session/file_serving.py:48` `resolve_within_root()` | 路径判定纯逻辑：限 workspace 根内、`resolve()` 后比前缀（挡 `..` 与符号链接）、体积上限 `MAX_FILE_BYTES`（`:28`，30MB）。**存在性检查排在包含性之后**，根外文件一律 403 不泄漏存在性 |
| Session | `src/psi_agent/session/server.py:18` `_make_files_handler()` + 注册 `GET /files` | HTTP 壳，`web.FileResponse` + `Content-Disposition: attachment`。不加鉴权，理由见下「隔离与鉴权」 |
| Session | `file_serving._in_private_space()` | 根内但落在 `.private/` 下一律 403。**只有本侧有文件系统事实**，故这道守卫只能在这里，详见下节 |
| Session | `src/psi_agent/session/agent.py:240` `workspace_path` 只读 property | handler 需要根路径；原先只有私有 `_workspace_path` |
| Channel | `src/psi_agent/channel/_types.py:25` `FileChunk.source: str = ""` | 字节可从哪取；默认空值使入向侧所有构造点无需改动 |
| Channel | `src/psi_agent/channel/_core.py:37` `_byte_source`、`:141` 扫描循环里盖章 | `session_socket` 为 `http(s)://` 时填规范化前缀，否则留空。盖在 `post()` 而非 `SendMarkerScanner` 内：scanner 是纯解码 |
| Channel（新） | `src/psi_agent/channel/_file_bytes.py` `fetch_file_bytes()` | `GET {source}/files?path=...` 取字节；非 200 / 异常 / 空体 / 超限一律记日志返回 `None`，**不抛**。**放通用层而非 `feishu/`**：`FileChunk` 是所有 channel 共用的，函数不认识任何平台的上传 API，放进 feishu 等于给 telegram 留一份逐字复制 |
| Feishu | 同上 `:194` `_send_file(channel, chat_id, path, source="")` | `source` 非空则改传 `bytes`（SDK 走 `kind="buffer"` 不碰文件系统）；走 file 分支时补 `file_name`；**取字节失败抛 `OutboundFileError`，不回落**（见下「为什么不回落」） |
| Feishu | 同上 `:516` `_stream_reply._produce` | 捕获 `OutboundFileError`：记 ERROR + 向会话发一句「文件发送失败: <名>」，**不重抛**——这里在卡片流式渲染里，抛出去会中断整条回复，一个附件失败不该让用户连文字也收不到；其余 chunk 继续处理 |
| Feishu | 同上 `:552` 调用点 | 传 `chunk.source`。私有空间守卫（`_private_space.blocks_send`）位置不变，仍在其之前 |

### 隔离与鉴权：一次事实澄清（负责人追问后补）

负责人问：这样改是否等于让 agent 实质拿到别的独立容器的文件，违背私有隔离的目的？**方向对，但「新增了跨容器读取能力」这个结论不成立**，核对结果如下。

**1. `/files` 不是新开的门，是已敞开的门上多开的一条缝。** 改动前 `HEAD~1:server.py:30-31` 就已暴露两条无鉴权路由：`POST /chat/completions`（读 `agent.py:343` 确认无任何鉴权或来源校验）与 `POST /events`。`/chat/completions` 比 `/files` **强得多**——它能驱动那个容器的 agent 执行任意 tool。A 容器要 B 容器的文件，改动前就能发一句 `POST /chat/completions` 让 B 自己读了交出来。`/files` 只是让同样的事少绕一步。

**2. 真问题是既有拓扑的性质：独立容器隔离的是文件系统，从来没隔离网络。** 三容器同在 `psi-agent_default`、彼此 8081 直连可达。我上一轮验过这一点（gateway→两个 session 的 8081 都 HTTP 400 = 活着），但当时只当「方案可行」的证据，没往「反过来 session→session 也通」这一面想。

**3. 我原先写的鉴权理由是错的，已改。** `server.py` docstring 与 `session/AGENTS.md:228` 原文是「该端口只在 docker 网络内可达（未发布到宿主）」——这句话把「宿主访问不到」当成了「不可信方访问不到」，而威胁模型里的不可信方（被 prompt 注入的 agent，手上有 `bash` / `fetch`）**正在那个网络里面**。挡的是外人，挡不住邻居。改后的真实理由是「加了也不改变暴露面」（见上第 1 点），并把缺口显式记进 `session/AGENTS.md`「已知缺口」而不是假装已解决。

**4. 我这次改动确实削弱了一处，已修。** channel 侧守卫 `_private_space.blocks_send`（`client.py:549`）判据是路径字符串，`owner_of` 走 `realpath`。同容器时解析的是真实存在的路径，判定可靠；**跨容器时那路径在 gateway 上不存在，`realpath` 退化成纯字符串规范化**，于是「公共区放个软链指进 `.private/`」这类写法在 channel 侧判不出来。修法是把一道无条件的私密区判定下沉到**源容器侧**（`file_serving._in_private_space`）——那是唯一有文件系统事实的一侧，`resolve()` 之后软链绕不过。

两道守卫都保留，判据不同不重复，分工根据是**谁掌握什么事实**：

| | channel 侧 `blocks_send` | session 侧 `_in_private_space` |
|---|---|---|
| 判什么 | 这位飞书发送者**是不是主人** | 这文件**是不是**私密区的 |
| 凭什么能判 | 只有 channel 手里有 `sender_open_id` | 只有源容器有文件系统事实 |
| 判不出什么 | 跨容器时的软链绕行 | 发送者是谁（本端点无从得知） |

刻意**不复用** `_private_space.owner_of`：它未配 `PSI_PRIVATE_OPEN_IDS` 时返回 `None` 即放行（因为它判「谁是主人」），而本端点要的是无条件的「是不是私密区」——白名单没配好不该等于把私密目录敞开供字节。已有专门用例覆盖这条区别。

### 为什么不回落到「交路径给 SDK」（负责人预判点 ①）

负责人预判：取字节失败后回落到交路径，跨容器时这条路必然失败，那正是 bug 本身；留着它只是把我们的错误换成 SDK 的错误，用户那边还是静默失败。**这条判断成立，1.2 版里的回落已删。**

核对依据：回落只在 `source` 非空时才可能触发，而 `source` 非空的定义就是「这个路径在本进程的文件系统里没有意义」。所以回落的成功概率不是「低」而是**零**——除非两个容器恰好在同一路径上各有一个同名文件，那反而是更坏的结果（发出去的是**另一个**文件的内容，静默发错比不发更难查）。而 SDK 那侧的失败形态恰好是静默的：`sender.py:430` 抛出后被 lark 的 `send` 收成 `result.success = False`，我们的代码接着试 file 分支、同样失败，最终既没有附件也没有任何用户可见的提示——**与修复前一模一样的症状**。

改后：`_send_file` 抛 `OutboundFileError`（带文件名的中文消息），`_produce` 里就地捕获、记 ERROR、向会话发这句话。**刻意不重抛**：那里在卡片流式渲染的回调里，抛出去会中断整条回复，一个附件失败不该让用户连文字也收不到；多个文件失败就各报一次。`source` 为空（同容器 Session）根本不进这条分支，行为与改动前逐字节相同、一步 HTTP 都不多走（V5 显式断言 `fetch_file_bytes` 零调用）。

### 30MB 两份字面量的跨文件锁（负责人预判点 ②）

负责人预判：30MB 在两个文件里各写一份，没有测试锁住它们相等，以后容易改一个忘一个。**成立，已补锁。**

上限刻意写两份而不是 import 共用：channel 不该依赖 session 包（否则前面刚做的解耦白做），且两侧是**各自独立的一道防线**——服务端拒绝供字节 / 客户端拒绝接收，任一侧单独失效另一侧仍然有效。代价就是能改一个忘一个。两侧各自那条 `== 30 * 1024 * 1024` 的断言**锁不住这个**：改动方只会改自己那侧的字面量与断言，两条依旧全绿。而**不一致的后果是静默的**——谁小谁生效，大的那侧白设，没有任何报错。故加 `test_max_bytes_agrees_with_session_side` 直接比对两个字面量（这是测试里唯一一处允许 channel 测试 import session 常量的地方，目的正是跨层比对）。

**5. 真正的边界只能在网络层或鉴权层，工具层拦不住**（只要 agent 有 `bash`，任何 URL 过滤都能绕）。两条路都要改部署，本任务不做，记为待办：

- 网络层：compose 里每对 gateway↔Session 一个独立 network，Session 之间不同网（最彻底）
- 鉴权层：给该端口上的**所有**路由统一加共享密钥，`/chat/completions` 必须一起加

顺带发现一条独立于本任务的：agent 包 `tools/read.py` 走 `resolve_under`，**绝对路径原样直通不做包含判定**，故本容器内的 `.private/` 在工具层目前也不设防。已记进 `session/AGENTS.md`。

### 三向同步

| 文件 | 补了什么 |
|---|---|
| `src/psi_agent/channel/AGENTS.md` | 目录树加 `_file_bytes.py` 一行；ChannelCore 段加 `_byte_source` 盖章条目（含「为什么不放 scanner」）；Feishu 约定段加三条——图片先试再降级会留常量级 `materialize blocked` WARNING（**勿把条数当故障数**）、`fetch_file_bytes` 必须交 bytes 而非路径的根因（并说明为何在通用层）、**两道私密区守卫的判据分工与「谁掌握什么事实」的根据**（不写下来后人极可能当重复删掉一道）。1.3 又把「取字节失败回落到原路径」那句**改成**「抛 `OutboundFileError` 不回落」＋调用点就地告知不重抛的理由 |
| `src/psi_agent/session/AGENTS.md` | 新增「GET /files——出向文件的字节来源」小节：为什么需要、关注点落点表（纯逻辑分离、限根内、不泄漏存在性、体积上限、私密区不供字节）＋**新增「已知缺口：同网络的 Session 之间没有隔离」**——显式写明「端口只在 docker 网络内」不能当安全依据、不加鉴权的真实理由、两条要改部署的封堵路线、以及 `tools/read.py` 绝对路径直通这条独立缺口。1.3 又在「体积上限」一行补上**两份字面量的由来、不一致后果是静默的、以及锁住它们的那条用例名**（改上限时两侧一起改） |
| `src/psi_agent/gateway/AGENTS.md` | **未改**。核对后确认该文件从未记载 `PSI_FEISHU_EXTERNAL_SESSIONS`，无过期表述需要对齐；本次事实归属 session / channel 两层 |

### 本机质量门

- `ruff check src tests` → `All checks passed!`；`ruff format --check src tests` → 全部已格式化。过程中修掉的都是自己新代码的问题：3 处 SIM117（嵌套 `async with`）、5 处 PLC0415（函数内 import，已提到文件顶部，`web` 一并补上）。**教训（两条，都是「只查改动文件」漏掉的）**：① 先前只对改动文件跑 `ruff check` 漏掉了那 8 条，全量 `src tests` 才暴露；② `ruff format` 也要跑 `--check` 全量——1.3 收尾时它才报出 `_file_bytes.py` 未格式化，而 `ruff check` 那侧是全绿的，两个命令查的不是一回事
- 测试见 T 段

***

## T —— 测试与验收

照 W 段 V1-V6 逐条核验，不新立标准。

| 项 | 结论 | 依据 |
|---|---|---|
| **V1** | **通过** | 见下「V1/V2 明细：生产验收」。新造文件名 `out-test-1944.md`，飞书侧可点击下载，51 Byte 两行内容正确 |
| **V2** | **通过** | 同一次验收即经 `psi-agent-luolin` 容器出向（临时把自己 open_id 指到该容器），日志 `19:46:18 GET /files serving '/workspace/out-test-1944.md'` |
| **V3** | **不验** | 用户明确决定跳过 `psi-agent-chengxx`。链路同构且已核验端点可达（`GET /files` 返回 404 = 端点在），但**没有真实飞书消息驱动过该容器**，如实记为未验 |
| **V4** | **通过** | 见下「V4 明细」 |
| **V5** | **通过** | 见下「V5 明细」 |
| **V6** | **通过** | 见下「V6 明细」 |

### V4 明细：测试覆盖 + 修复前能失败

新增 36 条，分四个文件，按被测层归位：

- `tests/psi_agent/session/test_file_serving.py` **17 条** —— 路径判定 12 条（根内放行；`..` 逃逸 403；软链逃逸 403（Windows 无权限时 skip）；root 为 None 403；空路径 400；不存在 404；目录 400；超限 413；常量等于 30MB；端点逐字节一致且 `Content-Disposition` 带中文名；端点逃逸 403 且不泄漏内容；端点缺文件 404）＋私密区守卫 5 条（见 V6 明细）
- `tests/psi_agent/channel/test__file_bytes.py` **8 条**（新文件，随 `fetch_file_bytes` 一起从 feishu 层搬出）—— 对**真起的** session server 端到端取字节逐字节一致；根外返回 `None`；主机不可达返回 `None`；`source` 带尾斜杠仍能取到（别拼出 `//files`）；空 body 当失败；客户端侧体积上限也拦（服务端上限是独立的另一道）；常量等于 30MB；**两侧上限相等**（`test_max_bytes_agrees_with_session_side`，1.3 新增，见上「30MB 两份字面量的跨文件锁」）
- `tests/psi_agent/channel/feishu/test_feishu.py` **7 条**（只留「飞书出向怎么用它」）—— 无 source 时传路径且 `fetch_file_bytes` **零调用**；有 source 时上传 bytes；bytes 走 file 分支带 `file_name`；`_stream_reply` 把 `chunk.source` 作第 4 位置参传给 `_send_file`；以下 3 条为 1.3 新增/改写：**取字节失败抛 `OutboundFileError` 且 `send` 零调用**（原「回落路径」那条的替代，断言零调用才能锁住「没有偷偷试一把」，并断言消息里点到文件名）；`source` 为空时**不受影响**（照旧交路径、`fetch_file_bytes` 零调用）；`_stream_reply` 把失败**告诉用户**且后续 chunk 继续流出
- `tests/psi_agent/channel/test__core.py` **4 条** —— TCP 填 `_byte_source` 含尾斜杠规范化；unix socket 与命名管道留空；`post()` 对 TCP 盖章、对本地留空

解耦的一个副证：搬走后 `test_feishu.py` **不再 import 任何 session 模块**（`web` / `SessionAgent` / `AiClient` / `_make_files_handler` / `ToolRegistry` 五个 import 被 ruff 判为未使用而清掉）。

**修复前能失败已实测，分三轮（后两轮各针对当轮新增的行为，不沿用前一轮结果）。**

第一轮（主体修复）：`git stash` 掉 5 个产品文件改动 + 临时移走新模块 `file_serving.py` 后跑同一批用例——

- feishu 7 条全红（7 failed, 79 passed）
- `_core` 4 条全红，且失败原因是 `AttributeError: 'FileChunk' object has no attribute 'source'` / `'ChannelCore' object has no attribute '_byte_source'`，即**冲着缺失的产品代码红**，不是撞 Windows 基线
- `test_file_serving.py` 12 条因 `ModuleNotFoundError: No module named 'psi_agent.session.file_serving'` 整个模块无法收集（比逐条断言更强的红）

第二轮（私密区守卫）：产品代码 stash 回 `a62ea2e0`（那版**没有** `_in_private_space`）、新测试留在树上，跑 `-k private` 得 **3 failed, 1 passed, 1 skipped**。红的正是三条要拦的（`.private/` 下 403、未配白名单也拦、端点层 403 不泄漏）；**绿的那条是「名字里带 `.private` 的公共文件不该误伤」——它在加守卫前后都该绿**，是防误伤的哨兵而不是漏网，这一条如果也红说明守卫写宽了。软链那条在 Windows 无权限 skip。

第三轮（1.3 的「不回落」，两条新用例各自单独验，因为它们盯的是**两个不同位置**的代码）：

- 把 `_send_file` 里的 `raise OutboundFileError(path)` 临时改回 `if data is not None: payload = data`（即 1.2 版的回落），跑 `-k "raises_instead_of_falling_back or reports_outbound_file_failure or local_path_unaffected"` 得 **1 failed, 2 passed**，红的正是 `test_send_file_raises_instead_of_falling_back_when_fetch_fails`，失败原因 `Failed: DID NOT RAISE OutboundFileError` —— 冲着缺失的行为红。另两条本就该在两版下都绿（一条盯 `source` 为空不受影响，一条盯调用点）。
- 恢复后再临时去掉调用点的 `try/except OutboundFileError`（让异常沿 `_produce` 逃出去 = 静默/中断的旧形态），跑 `-k reports_outbound_file_failure` 得 **1 failed**。

三轮恢复改动后均转绿。三文件合跑（`test_file_serving.py` + `test__file_bytes.py` + `test_feishu.py`）**109 passed, 2 skipped**。

**（实测坑）** 一次 pytest 调用里若有模块收集失败，整个 run 被 `Interrupted` 打断、其余文件不执行，需分文件跑才能看到各自的红。

### V5 明细：本地出向未被破坏

四文件合跑（`test_file_serving.py` + `test__file_bytes.py` + `test_feishu.py` + `test__core.py`）：**17 failed, 115 passed, 2 skipped**（1.3 新增 3 条后的数字）。17 条全部在 `test__core.py`，失败于 `NotImplementedError`（asyncio 无 `create_unix_connection`），已在未改动的 main 检出上复核为同样的 17 条红（`17 failed, 2 passed`），是 Windows 既有基线，非本次回归（对齐根 AGENTS.md 与既往记录）。2 skipped 都是软链用例在 Windows 无 `SeCreateSymbolicLinkPrivilege`。

「不产生额外 HTTP 请求」由 `test_send_file_without_source_passes_path_and_makes_no_request` 显式断言 `fetch_file_bytes` 调用次数为 0，而非仅看结果相同。

**（实测坑）** 在 worktree 里跑必须给 `PYTHONPATH` 指向本 worktree 的 `src`，否则 `psi_agent` 解析到主检出 `F:\code\psi-agent\src\psi_agent`，新模块表现为「明明存在却 ModuleNotFoundError」。命令见 plan。

### V6 明细：私密区守卫

**本条 1.1 版的结论有错，1.2 版改正。** 1.1 写的是「`owner_of` 用 realpath + parts 判定，对跨容器路径字符串同样成立」——前半句对，结论错：`realpath` 在**跨容器**时解析的是一个 gateway 上并不存在的路径，退化成纯字符串规范化，因此「公共区放个软链指进 `.private/`」这类写法在 channel 侧判不出来。这是我这次改动实际削弱的一处。

**修法与现状**：channel 侧那道守卫保持原样（`client.py:549`，判「发送者是不是主人」，位置在 `_send_file` **之前**未动，取字节只在守卫放行之后才可能执行）；另加一道无条件的私密区判定到**源容器侧** `file_serving._in_private_space()`——那是唯一有文件系统事实的一侧，`resolve()` 之后软链绕不过。两道判据不同不重复，分工表见 A 段「隔离与鉴权」。

新增 5 条用例专覆盖这道守卫：`.private/` 下文件 403；**未配白名单也拦**（与 `owner_of` 刻意分道的那条区别）；公共区软链指进私密区 403（Windows 无权限时 skip，正是 channel 侧判不出的那种写法）；名字里带 `.private` 的公共文件**不误伤**（判的是目录层级）；端点层 403 且不回显文件内容。`_private_space` 既有用例随合跑通过。

### V1/V2 明细：生产验收

**发布**：镜像 `psi-agent-gateway:outbound-039fdf70`，停机 **68s**（上一轮 95s）。三层核验在 build 机全过，**第三层在生产独立重跑一次**：真 `import` 成功、两处上限均为 `31457280`、报错文案正确。孤儿文件检查（旧/新 `.py` 清单 `comm -23`）为空，确认 build 机 src 未漂移。回退点 `psi-agent-gateway:rollback-preoutbound-20260824-193837`（`527deff72043`）。

**验收链路的前提先单独证过**：脚本在 luolin 容器造 `/workspace/出向验收-20260824-194402.md`（inode 442586、link count 1，确非 inode 701477 那个硬链接），gateway 侧 `ls` 该路径返回 No such file or directory，而 `GET /files` 取回 HTTP 200 / 108 字节且逐字节相符 —— 「文件系统不通、HTTP 通」这个前提成立。随后用户真实飞书消息验收通过。

**过程中三件事实，记下来**：

- **生产是四个容器同镜像**（gateway / luolin / chengxx / oauth-proxy 都写死 `image: psi-agent-gateway:local`），发一次版会重启四个，不是只重启 gateway。
- **`docker save`/`load` 后镜像 ID 会变**（build 机 `611bd4741e67` → 生产 `3dc1521172c9`），是两侧 Docker 版本（29.6.2 / 29.7.2）重算 manifest 所致，不是传错。判据只能是第三层内容核验，不能拿 ID 比。
- **`docker compose restart` 不重读 `env_file`**。撤临时 env 时改完文件重启，容器内 `printenv` 仍是旧值；必须 `up -d` 重建容器才生效（两个 private 容器读各自 workspace 下的 `.env`，本就没这个变量，不受影响）。

**19:46:51 那条上游 400 不是主链路失败**：它与 `Fusion Memory MCP supervisor thread crashed`（`/workspace/tools/_fusion_memory_mcp.py` 的 `memory_health`）同刻，session 本身正常收尾（`19:46:52 Compaction completed`、`model_completed, model_turns=2`）。另立卡片，不属本任务。

### 生产侧收尾（已完成）

- **临时 env 已撤**：`workspace/.env` 第 27 行删掉 `ou_6c30c11b76b15e42a7870e0686733c0f=http://psi-agent-luolin:8081`，与发布前备份 `diff` 为空。重建容器后 gateway 内生效值已是两条映射，`/oauth/callback` 400、`/sessions` 404、四容器 Up。
- **测试记录已删**：罗霖历史里原始四行（`tool_calls` 与其 `tool` 返回必须成对删，否则后续每次请求 400）。删前按内容特征逐条断言，防定时 trigger 追加导致的行号漂移。删后全行可解析、孤儿 `tool` 消息 0。残留一处文件名在一条 `compacted` 摘要里，但跑真实 `messages_for_ai()` 确认它已被后续两个压缩点顶掉、投影中出现 0 次，属死数据，未改生产的压缩摘要。
- **两处临时改动仍在**（按 W 段「明确不做」）：硬链接 inode 701477、build 机到生产的多余 ssh 公钥 `haitun1-build-to-prod`，另行撤除。
- **观察项，归入标记泄漏卡**：最新压缩摘要里 `[SEND:` 出现 27 次 —— 标记会被压缩吞进摘要再喂回模型，是与卡片渲染路径并列的**第二条**泄漏通道。附件发送本身正常。

***

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-08-24 | 初版，W/H 开工前落定；开工前核对出 4 处与 8-22 诊断不符 |
| 1.1 | 2026-08-24 | 补 A（代码落点 / 三向同步 / 本机质量门）与 T（V4-V6 通过并附实测明细，V1-V3 记未验待发布） |
| 1.2 | 2026-08-24 | 负责人追问耦合度与隔离后：`fetch_file_bytes` 移出 feishu 到 channel 通用层（测试随之搬出，feishu 测试不再 import 任何 session 模块）；私密区守卫下沉一道到源容器侧并补 5 条用例；**改正 1.1 版 V6 的错结论**（跨容器时 channel 侧 `realpath` 会退化，软链绕得过）；改掉「端口只在 docker 网络内可达」这条错鉴权理由并把「同网络 Session 之间无隔离」记为已知缺口 |
| 1.4 | 2026-08-24 | 发布生产并完成验收收尾：V1/V2 通过（新造文件名 `out-test-1944.md`，停机 68s），V3 经用户决定不验；补 A 段「V1/V2 明细」与「生产侧收尾」，记下三件实测事实（四容器同镜像、`docker save/load` 后镜像 ID 必变、`compose restart` 不重读 `env_file`）；改正一处误判（19:46:51 的上游 400 是 Fusion Memory MCP 崩溃所致，非主链路失败）；标记泄漏发现第二条通道（压缩摘要），另立卡片 |
| 1.3 | 2026-08-24 | 负责人预判的两点，均判定为对并落实：① **删掉「取字节失败回落到交路径给 SDK」**，改抛 `OutboundFileError`，调用点就地告知用户、不重抛（回落在跨容器下必然失败，只是把我们的错换成 SDK 的**静默**错，用户看到的与修复前一样）；② **30MB 两份字面量加一条跨文件锁**（两侧各自的 `== 30MB` 断言锁不住不一致，而不一致的后果是静默的）。V4 用例数 33 → 36，两条新用例各自单独做了修复前能失败的实测 |
