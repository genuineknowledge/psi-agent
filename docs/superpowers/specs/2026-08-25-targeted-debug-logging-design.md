# 定向 DEBUG 日志：让模型原始输出下次可观测

**描述：** 给 `_logging.py` 加按模块定向调级能力，DEBUG 只进一个自带轮转的文件 sink，`docker logs` 保持 INFO 不变。目标是下次 thinking 泄漏复现时能直接看到上游 SSE 的原始字段，从而分辨「模型从 `content` 直出自我对话」与「模型走 `reasoning_content` 而我们只读 `reasoning` 丢了」。**本任务只做可观测性，不修泄漏本身。**

**版本号：** 1.2

**状态：** 代码已实现，本机质量门通过；V7/V8（生产实测 + 镜像核验）与 V10 的多进程并发那一半待上线后验证

**适用范围：** psi-agent 日志基础设施（`src/psi_agent/_logging.py`），生产为新加坡节点 account.genuineknowledge.cn 的 `psi-agent-gateway` 容器

**关键词：** 可观测性、per-module 日志级别、loguru filter、日志轮转、reasoning 字段归属

**创建人：** @zsd

**审核人：** @待补

**关联文档：**

- 《真知开发执行 SOP》v1.0 —— 本文档 W/H/A/T 结构依据（文档在飞书，仓库内无副本）
- `docs/superpowers/specs/2026-08-22-external-container-recovery-plan.md` —— 8-18 事故与三层核验要求的出处
- `AGENTS.md`「日志约定」—— 本任务修正其中一条已失效的描述

***

## 结论先行

1. **加一个环境变量 `PSI_DEBUG_MODULES`**，填模块名（逗号分隔）就把这些模块的 DEBUG 写进文件，不填则与今天逐字节等价。改它只需重启容器，不重建镜像。
2. **DEBUG 只进 `{appdata}/logs/psi-debug-<pid>.log`**，每份 20MB、保留 10 份、gz 压缩，单进程磁盘上限 200MB。**一个进程一个文件**：生产 gateway 容器里 `gateway` 与 `channel feishu` 并排跑，而要观测的两个模块分居其中；共用一个路径实测 600 行只落盘 586 行。`docker logs` 仍是 INFO，一行不多 —— docker json-file 没有轮转，绝不能让它涨。
3. **SSE 日志额外打一行永不截断的字段清单**，因为现有日志截断到 1000 字符，content 一长 `reasoning` 键就被截掉，而「键不存在」与「键被截断」正是本次要分辨的东西。
4. **顺带修一个文档与代码不一致**：`_logging.py` docstring 与 `AGENTS.md:219` 都写「批量模式恒 DEBUG」，但 `_run.py:115` 早在 PR #625（`ea53f35b`）就改成了 `verbose=False`。生产跑的正是批量模式 —— 这是现场三处日志全空的直接原因之一。
5. **不做脱敏**，靠默认关闭 + 自动删除 + 文档写明风险控制。打码与「看模型原始输出」直接矛盾。

***

## W —— 是什么

### 1. 解决谁的什么痛点

2026-08-25 排查同事 `ou_e2c20a4f83edc6ff46f04f6d5298767c` 的 thinking 泄漏（生产 `psi-agent-gateway` 容器，模型 deepseek-v4-flash），52 条 assistant 消息的 `content` 里混着英文自我对话，且无一条带 `reasoning` 字段。

排查的结论不是「找到了根因」，而是**原始模型输出物理上已经不存在了**。三处能记它的地方当时全是关着的：

| 位置 | 记的是什么 | 级别 | 当时 |
|---|---|---|---|
| `ai/server.py:111` | `SSE chunk: {data[:1000]}` —— 上游 provider 的原始 chunk | DEBUG | 没记 |
| `channel/_core.py:137` | `delta.content` | DEBUG | 没记 |
| `channel/_core.py:144` | `delta.reasoning` | DEBUG | 没记 |

litellm 侧同样记不到：显式配了 `turn_off_message_logging: true`，且故意不接 Postgres（config 里写明不配 `DATABASE_URL`）。

**为什么生产是 INFO 而不是 DEBUG。** 生产入口是 `psi-agent run config.yml`，走 `_run.py:115` 的 `setup_logging(verbose=False)`。`_logging.py` 的 docstring 和 `AGENTS.md:219` 都还写着「批量模式始终为 DEBUG，各组件配置里的 `verbose` 字段被有意忽略」—— 这句话在 PR #625（`ea53f35b`）把 `verbose=True` 改成 `verbose=False` 之后就失效了，两处文档没跟上。**当前没有任何路径能让生产开出 DEBUG**：`_run.py` 先调用并锁定 INFO，one-shot 守卫让后续组件的 `verbose` 全部变成 no-op。

**连带发现一个更该先补的缺口。** `channel/_core.py:102` 的发送路径日志也是 DEBUG，所以当前配置下**无法知道任何一条消息实际发给用户的是什么**。这比泄漏本身更基础 —— 泄漏是偶发的，「发出去的内容无记录」是常态。

**为什么不能直接全局开 DEBUG。** 两个硬约束：

- 该容器 24 小时已产生 45228 行 INFO 日志，全局 DEBUG 的量级不可接受。
- **日志完全没有轮转**：docker log driver 是 `json-file` 且 opts 为空，`/etc/docker/daemon.json` 不存在。当前最大单容器日志 28MB，磁盘 40G 用了 51%（19G 可用），内存只剩约 2.6G。开着无上限涨的 DEBUG 会写爆磁盘。

现有 `_logging.py` 只有一个全局二元开关，没有任何 per-module 控制：

```python
def setup_logging(*, verbose: bool = False) -> int:
    global _handler_id
    if _handler_id is not None:
        return _handler_id          # one-shot: 首个调用者赢
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    _handler_id = logger.add(sys.stderr, level=level, format=...)
    return _handler_id
```

补充一条排除项：容器环境变量里的 `PSI_LOG_LEVEL=INFO` 是 **litellm 容器**的变量，psi-agent 代码里 grep 不到任何地方读它。改它无效。

### 2. 做完什么样算完（验收标准，可判定）

| 编号 | 验收标准 | 判定方式 |
|---|---|---|
| V1 | `PSI_DEBUG_MODULES` 未配置时，行为与改动前逐字节等价：不创建任何文件、不多装 sink | 单测：未配置时 `logger` 的 handler 数与改动前相同；`{appdata}/logs/` 不存在 |
| V2 | 配置后，仅指定模块的 DEBUG 进文件；未指定模块的 DEBUG 不进 | 单测：配 `psi_agent.ai.server`，该模块 DEBUG 落盘，`psi_agent.session.agent` 的 DEBUG 不落盘 |
| V3 | stderr 级别不受 `PSI_DEBUG_MODULES` 影响 | 单测：配了定向模块后，stderr sink 仍为 INFO（`verbose=False` 时） |
| V4 | one-shot 与批量模式语义不被破坏 | 现有 `tests/psi_agent/test_logging.py` 两条全绿；新增一条断言二次调用不重复装文件 sink |
| V5 | 文件 sink 带轮转与保留上限 | 单测断言传给 `logger.add` 的 `rotation`/`retention`/`compression` 参数值 |
| V6 | SSE 字段清单行能分辨 (a)/(b) 两种假设 | 单测：构造只有 `content` 的 delta → 断言输出含 `reasoning=ABSENT`；构造只有 `reasoning_content` 的 delta → 断言含 `reasoning_content=<n>ch` 且 `reasoning=ABSENT` |
| V7 | 生产开启后，日志里真的出现目标字段 | 上线后进容器 `grep 'delta keys' /…/logs/psi-debug-*.log`，人工确认三个字段名可见 |
| V8 | 镜像内产物与 git 一致 | 三层核验（见 A 段），第三层验镜像内 `_logging.py` 含新代码 |
| V9 | `uv run ty check .` 不引入新诊断 | main 基线 0 条（Windows 本机额外 2 条 `os.killpg` 平台差异不计） |
| V10 | 同容器多进程各写各的文件，不互相丢行 | 单测：断言路径以本进程 PID 结尾，且写入后 `logs/` 下只有一个文件、文件名含 `os.getpid()` |
| V11 | 请求侧清单行能分辨 `reasoning_content` 是否上了 wire | 单测：构造无 reasoning 的 history → 断言 `reasoning_carriers=0`；构造带 `reasoning_content` 的 assistant 消息 → 断言 `reasoning_carriers=1` 且报出该消息下标与字段长度；再构造 20×50k 的超长 history → 断言整行 < 900 字符（不随载荷增长） |

### 3. 明确不做什么

- ~~**不修 thinking 泄漏。** 根因未坐实，方案待定。本任务的产出是「下次能看见」，不是「已修好」。~~ → **1.3 之前有效**。根因已于 2026-08-26 坐实并修复，经负责人批准，见文末「根因坐实」节。观测能力正是坐实它的手段。
- **不改 `session/ai_client.py` 读 `reasoning` 的行为。** 那正是待验假设 (b) 的对象，改了就毁掉判据。（假设 (b) 已排除，该文件最终**未改** —— 读 `reasoning` 是对的。）
- **不碰 docker json-file / daemon.json。** 轮转做在应用侧，理由见 H 段方案比较。这也意味着 stderr 那一份仍无轮转 —— 但因为 stderr 级别不变，它的增速也不变，不构成新增风险。
- **不做 per-module 的任意级别 DSL**（如 `psi_agent.ai=TRACE,psi_agent.channel=DEBUG`）。当前只有一个消费者，只需要 DEBUG。
- **不做日志脱敏。** 见 H 段第 6 节。
- **不改 `_run.py:115` 的 `verbose=False`。** 那是 #625 有意为之，本任务只修跟不上的文档。

***

## H —— 怎么做

### 4. 有哪几种做法，为什么选这个

**决策一：轮转放哪一侧。**

| | 应用侧 loguru（选中） | docker json-file log-opts |
|---|---|---|
| 改动位置 | 仓库内 | 仓库外（宿主机配置） |
| 可测 / 可走 CI | 是 | 否 |
| 生效代价 | 随镜像走 | daemon.json 需重启 dockerd → 全部 7 容器停机；单容器 log-opt 需 recreate |
| 覆盖 stderr | 否 | 是 |

选应用侧：能测、能随 PR 交付、不影响其余 6 个容器。代价是 stderr 那份仍无轮转 —— 由「stderr 级别不变」这条设计消解。

**决策二：定向 DEBUG 输到哪个 sink。**

选**只进文件，stderr 保持原级别**。若定向 DEBUG 也进 stderr，docker json-file 就会无上限涨，直接违反「没有轮转就不要开 DEBUG」这条前置约束。分工的结果：

```
setup_logging(verbose)
  │
  ├─ sink 1: stderr    level = DEBUG if verbose else INFO   ← 现状不动
  │                    → docker logs（无轮转，故绝不让它涨）
  │
  └─ sink 2: file      level = "DEBUG"                       ← 新增，默认不装
                       filter = {模块: "DEBUG"} 白名单
                       rotation="20 MB", retention=10, compression="gz"
                       → {appdata}/logs/psi-debug-<pid>.log（一进程一文件）
```

代价：看定向日志要进容器 `tail` 文件，不能只用 `docker logs`。已接受。

**决策三：定向模块怎么表达。**

| | 环境变量给模块名列表（选中） | 复用 `verbose` 加第二个布尔 | 完整 DSL |
|---|---|---|---|
| 表达力 | 任意模块前缀，逗号分隔 | 只能全开全关 | per-module 不同级别 |
| 撞 one-shot 语义 | 不撞（env 与 `verbose` 正交） | 撞（仍是全局） | 不撞 |
| 本次够用 | 够 | 不够（等于全局 DEBUG，45228 行的代价回来了） | 过剩 |

选环境变量列表。loguru 的 `filter` 参数原生支持 `dict[str, str | bool]` 形式的 per-module 级别映射，不需要自己写匹配逻辑 —— `setup_logging` 只做「解析逗号分隔 → 转成 filter dict」。

**决策四：怎么保证能分辨 (a)/(b)。**

现有 `logger.debug(f"SSE chunk: {data[:1000]}")` 的问题不是「截断上限太小」，而是**截断本身破坏了判据**：`content` 较长时，delta 字典里排在其后的 `reasoning` 键会被截掉，而「键不存在」（假设 a）与「键被截断」（观测不足）在日志里长得一模一样。单纯把 1000 改大只是把这个陷阱推远，没有消除它。

所以额外打一行**永不截断的字段清单**：

```
delta keys: content=1843ch reasoning=ABSENT reasoning_content=ABSENT thinking=ABSENT tool_calls=0
```

判据变成一条 `grep`：

- 假设 (a) 坐实：`content=<n>ch` 且三个 reasoning 口子全 `ABSENT` → 模型没走 reasoning 通道，自我对话直接从 `content` 出来。
- 假设 (b) 坐实：`reasoning_content=<n>ch` 而 `reasoning=ABSENT` → 模型走了 `reasoning_content`，`ai_client.py:104` 只读 `reasoning` 把它丢了。

原有全文行保留（同时抬高上限），供人工确认「这就是泄漏的那段文本」。

**本任务只让它可观测，不给结论。** 现有倾向 (a) 的依据是间接的：8-18 抓过同一模型的 SSE，40 个含思考的 turn 里 `reasoning` 与 `reasoning_content` 逐字节相同，故只读 `reasoning` 不丢东西。但那不是这次泄漏的直接观测。

### 5. 别人怎么做的，我这样是否更好

Python 标准 `logging` 的常规做法是 `logging.getLogger("pkg.mod").setLevel(DEBUG)` —— per-logger 级别是内建能力。loguru 刻意不做 logger 树，改用 sink 上的 `filter`，官方文档给的等价写法正是 `filter={"module.name": "DEBUG"}`。本方案直接用这个官方机制，没有自造。

环境变量驱动日志级别是通行实践（`RUST_LOG`、`DEBUG=express:*`、loguru 自己的 `LOGURU_LEVEL`）。差别在于本方案是**白名单而非级别覆盖**：只有列进来的模块才进文件 sink，避免「开一个模块顺带开全部」。

未采用 `LOGURU_LEVEL` 等 loguru 内建环境变量：它们只在 loguru 首次导入时影响默认 handler，而本项目 `setup_logging` 第一件事就是 `logger.remove()` 清掉默认 handler，两者不衔接。

### 开工前核对诊断（触发式要求）

任务描述给的行号来自 2026-08-25，动手前已逐一核对 main（`b1bbad23`）：

| 描述中的位置 | 核对结果 |
|---|---|
| `ai/server.py:111` `SSE chunk: {data[:1000]}` | 一致 |
| `channel/_core.py:137` `delta.content` | 一致 |
| `channel/_core.py:144` `delta.reasoning` | 一致 |
| `channel/_core.py:102` 发送路径 DEBUG | 一致 |
| `session/ai_client.py:104` 只读 `reasoning` | 一致 |
| `_logging.py` 单一二元开关 + one-shot | 一致 |
| 「batch 模式恒 DEBUG」 | **不一致** —— `_run.py:115` 实为 `verbose=False`，见 W 段 |

额外查明：仓库内**没有任何** docker/compose/daemon.json 文件，部署配置全在仓库外 —— 这独立佐证了决策一（轮转必须做在应用侧才可能进 PR）。

另一个影响实现的发现：`gateway/__init__.py` 里 `setup_logging` 在 `:127`，而 `resolve_appdata_root` 在 `:138`，**前者早于后者**，且 `setup_logging` 是同步函数而 `resolve_appdata_root` 是 async。所以文件 sink 无法复用已解析的 appdata 根，也看不到 `--appdata` CLI 参数。处理办法见下节。

***

## A —— 执行过程

### 代码落点

| 文件 | 改动 |
|---|---|
| `src/psi_agent/_logging.py` | 新增 `debug_modules()` 解析环境变量、`debug_log_path()` 解析落盘路径（文件名带 PID）、`_file_handler_id` 守卫、`_setup_debug_file_sink()`；`setup_logging` 在有定向模块时额外装文件 sink（**在** stderr 安装之后） |
| `src/psi_agent/ai/server.py` | `_CHUNK_LOG_LIMIT` 抬高截断上限至 8000；新增 `_describe_delta()` 与 `delta keys:` 字段清单行 |
| `tests/psi_agent/test_logging.py` | 12 条新增：未配置零副作用、白名单过滤、stderr 级别不变、one-shot、轮转参数、顺序回归、解析与路径优先级 |
| `tests/psi_agent/ai/test_server.py` | 10 条新增：(a)/(b) 判据、超长 content 不截断、不回显原文、未知字段、tool_calls 计数、5 种畸形载荷、端到端每 chunk 一条 |
| `AGENTS.md` | 「日志约定」修正失效描述 + 补定向调级与环境变量说明 |

**路径解析。** 因 `setup_logging` 早于且不能 await `resolve_appdata_root`，文件 sink 自己算一遍：`PSI_DEBUG_LOG_PATH`（显式路径，可含 `{pid}`）→ `PSI_APPDATA/logs/psi-debug-<pid>.log` → `platformdirs.user_data_dir("Haitun")/logs/psi-debug-<pid>.log`。中间那档与 `_appdata.py:29` 的 env 分支同源，故容器里配了 `PSI_APPDATA` 就自动落在挂载卷上。给显式路径变量是为了兜住「`PSI_APPDATA` 指向容器层」的情形 —— 那会占宿主机磁盘。

**一个进程一个文件（V10）。** 动手部署时才发现生产 `launch-gateway.sh` 在**一个容器里起两个进程**（`psi-agent gateway` 与 `psi-agent channel feishu`，`docker top` 已确认），而要观测的 `psi_agent.ai.server` 与 `psi_agent.channel._core` 恰好分居其中。两进程写同一路径会静默丢行：`enqueue=True` 只在单进程内串行化，轮转后落败的一方还会继续往被改名的 inode 里写。写了个探针实测，**600 行只落盘 586 行，且轮转都没触发**。于是文件名带上 PID。

PID 由本项目自己拼，不用占位符：loguru 的 file sink 只替换 `{time}`（`loguru._file_sink.FileSink._create_path` 里 `self._path.format_map({"time": ...})`），我一开始误以为支持 `{process}`，首次写入直接 `KeyError`，6 条测试挂在这上面。显式路径里的 `{pid}` 用 `str.replace` 而非 `format` 替换 —— 运维给的路径不是格式串，里面偶然出现的花括号不该抛异常。

刻意**不** import `_appdata.py`：那是 async 模块，且 `_logging.py` 目前零项目内依赖，保持它在依赖图底层。代价是 `"Haitun"` 这个 appname 字面量出现在两处，用注释交叉引用锁住。

**两个独立守卫。** `_handler_id` 照旧守 stderr sink；新增 `_file_handler_id` 独立守文件 sink。不共用是因为触发条件不同 —— stderr 由 caller 的 `verbose` 决定，文件由进程环境决定。共用一个守卫会让「首个调用者的 verbose」意外决定文件 sink 装不装。

**未配置即不存在。** `PSI_DEBUG_MODULES` 空或未设时不调用 `logger.add`，也不创建目录 —— 「默认关」不是一个配置值，而是一个不存在的 sink。这让 V1 可以断言得很硬。

### 三向同步

- **AGENTS.md**：「日志约定」删掉失效的「批量模式始终为 DEBUG」，改为陈述实际语义（`_run.py` 先调用并锁定 INFO，故各组件 `verbose` 恒被忽略）；新增定向调级段落，写明两个环境变量、落盘路径优先级、轮转参数、以及**日志含真实对话与 open_id 的风险**。
- **本文档**：设计目标与实现方案。
- **代码**：`_logging.py` docstring 同步修正 one-shot 段落中关于批量模式的描述。

### 隐私与使用纪律

开启后 `psi-debug-<pid>.log` 里会有**真实对话内容与 open_id**，不做脱敏 —— 打码与「看模型原始输出」直接矛盾，自我对话本身就是要看的东西。靠三件事控制：

1. 默认关闭，需显式配置环境变量。
2. `retention=10` 自动删除旧文件，磁盘上限 200MB **每进程**。
3. 本节写明纪律：**查完即关**；文件不得复制出生产机、不得贴入工单或聊天；只在 `psi-agent-gateway` 一个容器开 —— 该容器两个进程，即约 400MB；7 个容器全开会到 2.8G 量级。

### 上线与回滚

**停机估计。** 改动只在 Python 源码，无 schema、无协议变更。生产代码烤进镜像，故需重建镜像 + 重启容器，参照近期同类操作（PR #726 实测 68s）估 **60–90s**。若只是事后调整定向模块列表，则无需重建镜像，改环境变量重启容器即可（约 10s）。

**三层核验**（8-18 事故缺的正是第三层，build 机 `src` 会漂移）：

1. build 机 `git log` / `git status` 确认工作树与目标 commit 一致；
2. 构建产物校验；
3. **进镜像验产物** —— `docker run --rm <image> grep PSI_DEBUG_MODULES /app/src/psi_agent/_logging.py`（实际路径以镜像布局为准），确认新代码在镜像里。

**回滚。** 两级：

- 一级（不重启进程无法生效，但零风险）：删掉 `PSI_DEBUG_MODULES` 重启容器 → 回到与改动前逐字节等价的行为。
- 二级：回滚镜像到上一 tag。

因「未配置即不装 sink」，一级回滚已覆盖绝大多数风险场景 —— 新代码在最坏情况下也只是一段不被执行的分支。

***

## T —— 测试与验收

### 本机质量门

- `uv run pytest -o testpaths= tests/psi_agent/test_logging.py tests/psi_agent/ai/test_server.py` —— **`-o testpaths=` 必须写在路径之前**，否则路径参数被静默忽略，只是数字变大而不报错。
- `uv run ty check .` —— main 基线实测 0 条诊断，不得引入新的。Windows 本机会多 2 条 `os.killpg` 平台差异，不计。
- 本机 `python` 是 3.7.9，低于仓库要求的 >=3.14，必须经 `uv run` 走项目环境。仓库代码用了 PEP 758 的 `except A, B` 语法，用低版本解释器读会报语法错误 —— 那不是缺陷。
- Windows 上 5 条 session 测试因 asyncio 子进程 `NotImplementedError` 恒失败，是基线不是回归。

### 待生产验证项

V7（日志里真的出现目标字段）与 V8（镜像内产物核验）只能在上线后判定，本机无法覆盖。文档交付时须如实标注其状态。

V10 的单测只验「本进程写本进程的文件」这一半 —— 真的两个进程并发写、互不丢行，要到生产 gateway 容器里 `ls logs/` 见到两个 PID 文件才算实证。

***

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-08-25 | 初稿：定向 DEBUG + 文件轮转 + SSE 字段清单行 |
| 1.1 | 2026-08-25 | 实现完成。补记一处实现期发现的顺序缺陷（见下）；`retention` 定为 10（负责人要求，从 3 上调，理由是「还没查到就没了」）|
| 1.2 | 2026-08-26 | 部署前发现生产一容器两进程，改为一进程一文件（V10）。原单文件版已随 `7f45d2fe` 合入 main，本次单独修正 |
| 1.3 | 2026-08-26 | 已上线（停机 69s），V7/V8/V10 生产实测通过。首次捕到泄漏样本，排除假设 (b)。补请求侧清单行 `_describe_messages`（V11），因请求体截断使根因仍不可观测 |
| 2.0 | 2026-08-26 | **根因坐实并修复**：any-llm 的 DeepSeek provider 默认关思维模式。落 `reasoning_effort` 默认值（V12–V14），端到端实测 `0/9 → 24/33`。观测任务至此闭环，泄漏部分不再只可观测 |

### 实现期补记：`logger.remove()` 的顺序

初稿把 `_setup_debug_file_sink()` 放在 `setup_logging` 开头，先于 stderr 分支。实测立刻暴露：裸 `logger.remove()` 清掉的是**所有** handler，所以文件 sink 装好后被下一行删掉，而守卫 `_file_handler_id` 已置位 → 一次性语义让它再也装不回来，**且不报任何错**。表现是「配了 `PSI_DEBUG_MODULES`，文件被创建了，但永远是空的」。

修法是把 `logger.remove()` 连同 stderr 安装整体前移到文件 sink 之前。已加回归测试 `test_stderr_removal_does_not_wipe_the_file_sink` 钉住顺序，并写入 `AGENTS.md` 约束第 2 条。

这条值得记下来的原因：它是「静默失效」类缺陷，且恰好发生在一个**为了消除静默失效而做的功能**里 —— 若没有 V2 那条断言落盘内容（而不是只断言 handler id 非空）的测试，它会一路带到生产，在下次泄漏复现时才以「日志开了但是空的」的形式暴露。

***

## 上线后实测：捕到样本，并暴露一个新的观测缺口

**结论先行：假设 (b) 已排除，(a) 得到直接证据但样本很窄；根因仍未坐实，因为请求侧看不见。**

### 实测数据

上线后 10:46–10:48 的窗口，`psi-debug-9.log`（gateway 进程）落了 7908 个 chunk：

| 量 | 值 |
|---|---|
| census 行总数 | 7908 |
| `reasoning` / `reasoning_content` / `thinking` 至少一个有值的 | **0** |
| 不同请求 id | 5 |
| 模型 | `deepseek-v4-flash`（单一） |

泄漏样本落在请求 `58c0bc8d-f968-40f5-9d35-fb392f9a1ce8`（789 个 chunk）。把它的 `content` 增量拼回，**开头约 60 字是自我对话**：复述用户上一句、自问对方要什么、确认自己的理解（「我看看上下文」「所以你是想让我…对吧?」这类口气），之后才转入正常的分层回答。

正文原文不抄进本文档 —— 那是真实用户对话，按下节的隐私约定只留结构描述；要看原文去日志里按上面这个请求 id 捞。判据也不靠这些词本身：`content` 的**最前面**出现「复述提问 + 自问自答 + 向自己确认理解」这一结构，才是泄漏的形状。该请求首个 census 行同样是三个 reasoning 字段全 `ABSENT`。

### 对两条假设的判定

- **(b) 模型发了 `reasoning_content`，但 `session/ai_client.py:104` 只读 `reasoning` 给丢了** —— **排除**。成立的前提是先看到 `reasoning_content` 有值，实测 7908/7908 都是 ABSENT。
- **(a) 模型没用 reasoning 通道，自我对话直接进 `content`** —— 与实测吻合，且这次是**直接证据**（此前 8-18 只有「40 个 thinking 轮次里 `reasoning` 与 `reasoning_content` 逐字节相同」这类间接证据）。

判据边界，不要外推：样本只覆盖 **8 分钟、5 个请求、1 个模型**。而且这是**回捞历史**，不是主动触发的复现，运气成分不小。换模型或换供应商，结论不一定照搬。

### 暴露的缺口：请求侧不可观测（V11 的由来）

现象查清了（泄漏从 `content` 出来），但根因要回答的是**模型为什么把自我对话写进 content**，而这需要看我们发上去的东西 —— 恰好看不见：`Request body` 那行截断在 1000 字符，实测 5 条请求**全部**是整 1000，system prompt 刚开头就断了。

关键矛盾在于：`session/AGENTS.md:289-295` 写明 DeepSeek V4 这类 reasoning model 在 tool call 轮次要求 `reasoning` 完整回传，且键名必须改成 `reasoning_content`（`history_display.py:264` 的 `_rename_reasoning_for_wire`）。于是请求侧本该带着 `reasoning_content` 出去，响应侧却一次不回。三种可能，靠现有日志无法区分：

| # | 可能 | 需要什么证据 |
|---|---|---|
| 1 | 其实没发出去（rename 没走到，或历史里没存 reasoning） | 请求侧 `reasoning_carriers=0` |
| 2 | 发出去了但形态不对（挂在不该挂的 message 上） | `reasoning_carriers>0` 且位置异常 |
| 3 | 发对了，是该模型压根不走 reasoning 通道 | `reasoning_carriers>0` 且位置正常 |

`_describe_messages`（V11）就是为了让这三条塌成一条：按 message 逐条报 role、三个 reasoning 类字段的长度、`tool_calls` 数、`content` 长度，行首给 `n=` 与 `reasoning_carriers=`，长度由**message 条数**而非历史大小决定，故永不截断。同时把 `Request body` 的上限从 1000 抬到与响应侧一致的 `_CHUNK_LOG_LIMIT`。

**刻意不做的事**：不改 `session/ai_client.py:104`、不改 `_rename_reasoning_for_wire`、不动任何行为。根因未坐实前只加观测 —— 这是负责人在本任务和后续追问里都明确要求的（「不要在未确定根因前乱修复」）。

### 下一步的观测顺序

1. 上线 V11，等自然流量攒样本。
2. 下次泄漏复现时，对着看两条清单行：请求侧 `reasoning_carriers` 与响应侧 `reasoning=ABSENT` 是否同时为零。上表三条可能应立刻塌成一条。
3. 若指向「我们发错了」，照搬 `session/AGENTS.md` 里已有的单变量实测法（键名作唯一变量、各三次、对线上端点实测），换成「带/不带 `reasoning_content`」作唯一变量。

**时间约束**：`psi-debug-9.log` 上线一小时已 6.0MB。空闲不涨（实测 120 秒 0 字节），但活跃对话每轮几 MB，单份 20MB 就轮转。靠自然流量攒样本别攒太久 —— 真出现泄漏那一轮的证据可能被后来的流量挤掉。

***

## 根因坐实：思维模式被上游默认关掉

**结论先行：不是「字段被丢」，是「思维根本没生成」。** any-llm 的 DeepSeek provider 把 `reasoning_effort` 的缺省值 `"auto"` 读成「调用方没要思维」，转而下发 `extra_body.thinking={"type": "disabled"}`。模型被关掉思维通道后仍要推理，就把自我对话写进 `content`。

上一节列的三条可能，实测塌成了**第 3 条**（我们发对了，模型没走 reasoning 通道），但原因不在模型 —— 在我们没要。

### 源码

`any_llm/providers/deepseek/deepseek.py:34-58`（1.26.0）：

```python
thinking_disabled = params.reasoning_effort in (None, "none", "auto")
extra_body = converted_params.setdefault("extra_body", {})
extra_body.setdefault("thinking", {"type": "disabled" if thinking_disabled else "enabled"})
```

其 docstring 写明理由：DeepSeek V4 官方默认**开**思维，any-llm 为对齐旧版 `deepseek-chat` 行为主动反转成**关**。这是有意的默认值，不是缺陷 —— 所以「给上游提 bug」这条路不成立。我们整个 `src/` 从不传该参数（`git grep reasoning_effort -- src/` 零命中），故恒定走 disabled 分支。

### 实测：2×2 矩阵 + 单变量

同 prompt（`"1+1 equals what? think briefly"`）、同模型 `deepseek-v4-flash`、同 key，只改一个变量，数带思维链的 chunk：

| 组 | provider | 上游 | chunk | 带思维链 |
|---|---|---|---|---|
| **A（ToB 生产实际）** | `deepseek` | api.deepseek.com | 10 | **0** ❌ |
| B | `deepseek` | litellm | 31 | 22 ✅ |
| C（ToC 形态） | `openai` | litellm | 31 | 22 ✅ |
| D | `openai` | api.deepseek.com | 43 | 33 ✅ |

四格里只有 A 坏。B 好是因为 litellm 重写请求体覆盖掉了 `disabled`；D 好是因为 openai provider 不加这个默认值。

固定 A 组配置，只加思维开关：

| 组 | 参数 | chunk | 带思维链 |
|---|---|---|---|
| E | 什么都不传（= 线上现状） | 9 | **0** |
| F | `reasoning_effort="medium"` | 23 | **20** |
| G | `extra_body={"thinking":{"type":"enabled"}}` | 37 | **28** |

### 版本来源

1.21.0 的同一文件里**没有** `thinking` 分支（已比对本机 venv 1.21.0 与生产 1.26.0）。依赖声明是 `any-llm-sdk>=1.21.0`，开区间 —— 这是一次静默的上游行为变更改掉了线上语义。收紧版本区间建议单独立项，本次不动（修法在 1.21.0 与 1.26.0 上都成立）。

### 两条被推翻的中间结论

调查过程中我曾得出两个错误结论，**记在此处防止被复用**：

- ❌「any-llm 把 `reasoning_content` 字段丢了」 —— 错。字段没丢，是压根没生成。当时只量了「穿过 any-llm 归零」，没拆开 provider 这个变量。
- ❌「ToC 装机版很可能也中」 —— 错。C 组实测 22/31 正常。

### ToC 为什么没事（是巧合，不是设计）

两条产品线**共用同一份 `psi_agent` 与同一个 any-llm**，分岔只在配置：ToC 的 `spa-v2/src/services/bootstrapAi.ts` 里 `DEFAULT_REMOTE_AI` 配 `provider: 'openai'`（因为它打的是云端 OpenAI 兼容网关），顺带绕开了 DeepSeek provider 的默认值；ToC 链路里的 litellm 还会再兜一道（B 组已证）。

**两重巧合。** 谁哪天把 ToC 的 provider「修正」成 `deepseek`，泄漏立刻出现在装机版上。

### 修法与不选的理由

| 方案 | 判断 |
|---|---|
| **1. 显式传 `reasoning_effort`（已采用）** | 一处改动，走公开接口，不绕库不打补丁。语义正确 —— 我们确实要思维模式 |
| 2. ToB 的 provider 改 `openai` | 零代码，但放弃 DeepSeek provider 的 `_reinject_reasoning_content`，而思维模式下 tool_call 轮次不回传 `reasoning_content` 会 400。**不可行** |
| 3. 给 any-llm 提 issue | 那是他们的有意默认值，不是 bug。**不成立** |

落点：`ai/server.py` 的 `body.setdefault("reasoning_effort", "medium")`。用 `setdefault` 而非赋值 —— 这里是转发层，兜底只补「谁都没表态」这一种情况，调用方显式给的值（含 `"none"`）优先。

### 影响面：不止泄漏

思维模式关着，**模型能力一直在打折**，泄漏只是最显眼的表征。前端 `reasoningDisplay.ts` 那套 `kind: 'thinking'` 面板在 ToB 上从来没有内容可渲染，同一个原因。

### 验收

| # | 判据 | 状态 |
|---|---|---|
| V12 | 不传时兜默认值 | 单测 `test_handler_requests_thinking_mode_by_default` ✅ |
| V13 | 调用方给的值优先（含 `"none"`） | 单测 `test_handler_keeps_caller_supplied_reasoning_effort` ✅ |
| V14 | 真实端点上思维链回来 | **端到端实测**：一次性容器挂载改后 `server.py`，跑真 handler 打真端点，`0/9 → 24/33` ✅ |
| V15 | `"none"` 真能关掉，不只是参数发出去 | **生产容器内三臂实测**（2026-08-27，同 prompt/模型/key，`reasoning_effort` 为唯一变量）✅ |

V14 是关键 —— 单测只能证明参数发出去了，证明不了泄漏被修好。

V15 补的是 V13 的盲区：单测断言的是「请求体里那个键还在」，证明不了这个值真被上游当回事。三臂在生产容器里打真端点：

| `reasoning_effort` | 带思维链的 chunk | 说明 |
|---|---|---|
| 不传（旧行为） | **0/60** | 就是这个 bug：思维通道全哑 |
| `"medium"`（新默认） | **42/62** | 修复生效 |
| `"none"`（调用方显式关） | **0/21** | 覆盖真的穿透到上游 |

第三臂说明 `setdefault` 保住的是端到端的调用方意图，不止是 dict 里的一个键 —— 转发层没有偷偷替上游做决定。

### 连带发现（各自立项，本次不做）

1. **压缩丢历史**：`messages_for_ai()` 从最后一个 `compacted` 标记往后重建，之前全部轮次不再上 wire。实测 3 个会话：磁盘 143/211/138 个带思维链的 tool_call 轮次，上 wire **0** 个；删掉标记后 143 个恢复。与本次泄漏无因果（已排除）。
2. **依赖开区间**：`any-llm-sdk>=1.21.0` 让上游默认值变更能直接改线上语义。
3. **泄漏检测判据不可靠**：现有脚本要求句子含问号，漏掉叙述式自言自语（「我先…」「让我…」）。换叙述式词表后同一时间窗 6/14 命中。正则判语气本就不可靠。

### 如实交代没验到的部分

- 该默认值具体是 1.22–1.26 哪个版本引入的，只确认了「1.21.0 没有、1.26.0 有」，没逐版本二分。
- 生产完整会话链路（Session→AI→模型，带真实长 history 与 tool_calls）尚未跑过；V14 是单轮对话的端到端验证。
- `reasoning_effort="medium"` 相对 `"high"` 的质量/成本差异未测，取值是判断而非实测结论。
