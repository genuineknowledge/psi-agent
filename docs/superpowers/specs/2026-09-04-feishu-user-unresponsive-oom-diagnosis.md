# 飞书用户 11:07 无响应排查记录（只读诊断）

- **排查对象**: 飞书用户 `ou_b1a3bdba5ed0e022cd06b492cd07f30e`（周玉洁）
- **反馈现象**: 2026-09-04 11:00 左右机器人无响应
- **排查时间**: 2026-09-04 11:40–12:00 CST
- **性质**: 只读诊断，**未改动生产任何文件**

## 结论

**机器人无响应的直接原因是 gateway 进程在 11:07:37 被内核 OOM 杀掉**，恰好发生在这位用户
那一回合的模型响应刚开始流式返回之后 1 秒。她的提问永久丢失、没有任何重放机制补偿，用户
侧表现就是「发出去了，再也没有回音」。

**这不是昨晚新版引入的缺陷。** 同一个 OOM 从 2026-09-02 起已发生 8 次，横跨新旧两个镜像。
新版（`deploy-4cef99ec`，昨晚 21:19 部署）实际上**缓解了**问题：9-02 一天 4 次，9-03 3 次，
新版上线后 14 小时才发生 1 次。但缓解不等于修复 —— 容器重启 48 分钟后内存已重新爬到
2.18 GiB / 3 GiB（72.5%），会再次撞顶。

**根因是架构性的，不是某一行代码的 bug**：单个 gateway 进程把 **66 个 session、19.5 万条
消息、375 MB 历史全部常驻内存**，而容器上限是 3 GiB。任何一个用户的一次正常请求都可能成为
压垮进程的最后一根稻草，而**被杀掉的那个用户与造成内存压力的用户往往不是同一个人**。这位
用户是第 8 大历史持有者，属于受害者而非肇因。

用户反馈里提到的「trigger 注入」类问题在本次排查中确认为**历史残留数据**，新版已修复，不是
本次无响应的原因（详见「已排除的病灶」）。

## 证据

### 1. OOM 杀进程（实测，内核日志）

```
/var/log/kern.log:
2026-09-04T11:07:37.169917+08:00 kernel: AnyIO worker th invoked oom-killer
2026-09-04T11:07:37.170226+08:00 kernel: oom-kill:constraint=CONSTRAINT_MEMCG,
  oom_memcg=/system.slice/docker-e972e81c….scope, task=psi-agent, pid=3827281
2026-09-04T11:07:37.170227+08:00 kernel: Memory cgroup out of memory:
  Killed process 3827281 (psi-agent) total-vm:5006136kB, anon-rss:2925100kB
```

`CONSTRAINT_MEMCG` 说明是**容器 cgroup 上限**触发，不是机器整体内存耗尽。上限来自
`docker-compose.yml:10` 的 `mem_limit: 3g`（该处注释说明设限意图是把爆炸半径关在本容器内）。

容器侧对应事实：

```
docker inspect psi-agent-gateway:
  StartedAt=2026-09-04T03:07:37Z  RestartCount=1  ExitCode=0  OOMKilled=false
  Memory=3221225472 (3 GiB)
```

注意 `OOMKilled=false` 是**误导性的**：被杀的是容器内的 `psi-agent` 子进程，不是容器 PID 1
（PID 1 是 `launch-gateway.sh`）。docker 只在 PID 1 被杀时才标 `OOMKilled=true`。**判断
本类故障不能看 `docker inspect` 的 OOMKilled 字段，必须查内核日志。**

### 2. 崩溃现场正是这位用户的回合（实测，容器日志）

```
11:07:35.335 | feishu-ou_b1a3bdba… | system_prompt:ensure - System prompt rebuilt (182535 chars)
11:07:36.366 | feishu-ou_b1a3bdba… | agent:run - TMPFIX-M2 tools_exposed=45 of 210
11:07:36.367 | feishu-ou_b1a3bdba… | prompt_budget - 45 tools, 55615 chars (~13903 tokens)
11:07:36.385 | feishu-ou_b1a3bdba… | agent:run - Sending request to AI via AiClient
11:07:36.418 | feishu-ou_b1a3bdba… | ai.server - ai-turn open
11:07:36.542 | feishu-ou_b1a3bdba… | ai_client:stream - AI response status: 200
11:07:37.546 | [Lark] [WARNING] markdown-stream: producer raised: Response payload is
              not completed: <TransferEncodingError: 400, message='Not enough data to
              satisfy transfer length header.'>
11:07:37.549 | /workspace/launch-gateway.sh: line 38: 9 Killed  psi-agent gateway …
```

时序很干净：模型已回 200 并开始流式（11:07:36.542）→ 1 秒后进程被杀（11:07:37.549）。那条
`TransferEncodingError` 是**结果不是原因** —— 进程死了，正在读的流自然断在半路。

崩溃前 25 行日志里，14 行属于这位用户的 session，3 行属于另一个用户，8 行是无 session 归属
的定时刷新。她的回合是当时唯一在跑 LLM 的重活。

### 3. 她的提问永久丢失（实测，历史文件）

历史文件 `/workspace/.psi/appdata/histories/feishu-ou_b1a3bdba….jsonl` 的**最后一条记录**
就是她 11:07:35 那条消息，之后没有任何 assistant 回复：

```
line 6371  role=user  kind=chat  len=321  ts=2026-09-04 11:07:35
  你把这里的表格，想办法按能粘贴进word的格式给我，我要直接粘贴成word里的表格。
  文字部分你优化一下可以精简一点，文字即可
```

重启后（11:08:34 session 恢复完成）**该 session 零次 LLM 调用**
（`grep -cE "Sending request to AI|ai-turn open"` = 0，统计范围为重启之后的全部日志）。
即：这条消息不会被自动重试，用户不再发新消息就永远等不到回复。

### 4. 内存被什么吃掉（实测）

重启后一次性恢复 **66 个 session**，逐个把历史全量读入内存：

```
共 195279 条消息载入（重启后日志中所有 "(N messages)" 求和）
histories/ 目录 375 MB，401 个 .jsonl 文件

单个最大者：
  ou_046fc017747124e5bb999b4f1d45932b   12864 条   66 MB
  ou_d9545bb486018ef1472d7856272a116b   10553 条   46 MB
  ou_3d5539ff58b49de05d29787700796246    9873 条
  …
  ou_b1a3bdba5ed0e022cd06b492cd07f30e    6371 条   41 MB  ← 本次受害者，排第 8
```

当前内存（重启后 48 分钟）：`psi-agent-gateway 2.176GiB / 3GiB (72.54%)`。宿主机整体
`available` 仅 2.0 GB，也没有 swap，横向扩容余量很小。

### 5. OOM 是反复发作的，新版缓解了但没修复（实测）

内核日志里全部 8 次 OOM，每次 RSS 都精准撞在 2.92–2.94 GB：

| 时间 | anon-rss | 镜像 |
| --- | --- | --- |
| 2026-09-02 09:30 | 2938660 kB | 旧版 |
| 2026-09-02 11:55 | 2943248 kB | 旧版 |
| 2026-09-02 14:57 | 2939056 kB | 旧版 |
| 2026-09-02 18:20 | 2940560 kB | 旧版 |
| 2026-09-03 10:52 | 2939612 kB | 旧版 |
| 2026-09-03 15:44 | 2924832 kB | 旧版 |
| 2026-09-03 20:01 | 2930252 kB | 旧版（部署前 1h18m） |
| **2026-09-04 11:07** | **2925100 kB** | **新版 deploy-4cef99ec** |

发作频率：9-02 四次、9-03 三次、新版上线后 14 小时一次。**趋势向好，但稳态内存仍在上限
边缘，撞顶只是时间问题。**

## 已排除的病灶

按预设的几类已知病灶逐条核对，结果如下。

### 压缩摘要塌缩 —— 未命中

110 条 `role: compacted` 记录，最短 955 字符、最长 16385 字符，内容都是结构完整的中文摘要。
对比历史事故里 3653 条塌成 12 字符 `HEARTBEAT_OK` 的形态，本会话压缩健康。

### 每回合压缩耗时过高 —— 本轮无法证实也无法排除

容器 11:07 才重启，日志里 `grep -i compact` **零命中**，量不到耗时。压缩耗时的历史结论
（41.5s × 22 次）来自另一次排查，本次没有新数据。

### trigger 空 filter 绕过 / 注入洪水 —— 历史残留，非本次原因

实测到大量注入痕迹，但都是**旧数据**：

- `[trigger tool] assignment-delivery-refresh` 注入 **872 次**（8-24 至 9-03）
- 同一条「持续监测：高帅群聊新任务」提示词重放 **110 次**（8-21 至 9-02），其中 93 次真的
  驱动了 LLM 回合，累计产生 1440 条 assistant 消息
- 按 `kind` 统计字节占比：`trigger.silent` **73.9%**、`chat` 仅 **22.4%**、`compacted` 3.2%

新版行为已修正 —— 现网日志里该 trigger 每次都走
`reported no changes; skipping history write`，不再写历史、不再驱动 LLM：

```
11:07:21 | trigger_registry:_fire_tool - Trigger tool result: {"ok": true, "checked": 0, …}
11:07:21 | trigger_registry:_fire_tool - reported no changes; skipping history write
```

**但残留数据本身仍是活的成本**：这 4666 条 `trigger.silent` 记录（31.7 MB，占该文件 73.9%）
每次 session 恢复都要重新载入内存，直接喂给本次 OOM 的根因。清理它们能立刻回收内存，属于
见效最快的一步。

### thinking 泄漏到 content —— 命中，但量小且非本次原因

3024 条 assistant 消息中 1790 条带 `reasoning` 字段，说明该字段总体是通的。但确有泄漏个例，
例如倒数第二条（line 6370）：`reasoning` 字段缺失，自我对话直接进了 `content`：

```
用户选了推荐的第 3 项，但她说的是"接进实施方案之后…"——选项3 是放文档末尾作附录3。
等等，推荐3是我给的，但她要求位置"实施方案之后"。让我重新想一下…
…我应该等用户回复。刚才只是调用了 clarify 并输出了问题…
```

按自我对话特征词统计，711 条 `kind: chat` 的 assistant 消息中 3 条命中。**这会让用户看到
机器人的内心独白，是真实的体验缺陷**，但它不造成无响应，与本次故障无因果关系。

### 回合失控霸占锁 —— 未命中

单个 trigger 后最多只有 1 条 assistant 消息。历史上最长的一个回合有 190 条 assistant
（8 月的专利问题回合），但那是旧数据，且本次崩溃前该 session 只跑了正常的单回合。

## 建议改法（本轮未落任何生产改动）

按投入产出排序。前两条只动数据与配置，第三条要改代码。

### 1. 清理历史里的 trigger 残留（立刻见效，风险低）

`trigger.silent` 占该用户历史 73.9% 的字节。66 个 session 普遍如此，全局清理预计能砍掉
可观的常驻内存。建议做法：

- 先备份（该目录已有 `.bak-*` 惯例）；
- 按 `kind == "trigger.silent"` 且非 `compacted` 过滤掉记录，保留 `chat` 与 `compacted`；
- 逐个 session 处理，处理完重启容器再量一次 RSS 做前后对照。

**注意**：过滤要保留 `compacted` 记录，那是唯一保存早期语境的载体。另外 assistant 的
`[trigger tool …] ok` 存根与其 user 侧注入要成对删除，否则会留下孤立的 assistant 消息。

### 2. 给 OOM 装上可观测与告警（不改行为，只补眼睛）

本次故障之所以要靠人工反馈才发现，是因为**没有任何告警**：docker 的 `OOMKilled` 是 false，
容器状态显示 `Up`，只有内核日志和「用户说没反应」两处能看出问题。建议：

- 采集 `kern.log` 的 `oom-kill` 事件并告警；
- 采集容器 RSS / limit 比值，超 80% 预警；
- 在 gateway 启动日志里打印「本次是重启第 N 次」，让日志自身能暴露异常重启。

### 3. session 历史不再全量常驻（真正的修复，需设计）

根因是 66 个 session × 全量历史常驻单进程。三个方向，都需要负责人定方案：

- **惰性加载 / LRU 淘汰**：只把活跃 session 的历史留在内存，冷 session 落盘按需读回。改动
  面在 `session/conversation.py` 的加载路径与 `runtime/_session_manager.py` 的恢复逻辑。
- **启动时不恢复全部 session**：现在是重启即恢复 66 个。改成首次消息到达再恢复，能把冷启动
  内存压到接近零，代价是首条消息变慢。
- **拆容器**：现有部署已经在给个别用户单开容器（`-luolin` / `-chengxx`）。把大户拆出去是
  最省事的止血，但按已记录的判断，这只是「手工多开容器」而非租户模型，且宿主机
  `available` 仅 2.0 GB，能拆的次数有限。

另外值得单独考虑：**被 OOM 打断的回合应当可恢复**。目前用户消息已落盘但回复丢失，重启后
没有任何补偿机制。若能在恢复 session 时检测「最后一条是 user 且无对应 assistant 回复」并
重新驱动一次，用户侧就不会出现「石沉大海」。这需要谨慎设计幂等性，避免重复执行带副作用的
工具。

### 4. thinking 泄漏（独立问题，低优先）

3/711 的泄漏率不影响可用性，但影响观感。已记录的根因是上游不给 `reasoning` 时自我对话直接
进 `content`。本轮没有新证据，建议并入既有的 thinking 泄漏议题，不单开。

## 实测与未验的边界

**实测到的**（有命令输出或文件内容支撑）：

- OOM 时间、进程、RSS、cgroup 约束 —— 内核日志原文
- 崩溃与该用户回合的时序关系 —— 容器日志逐行
- 该用户提问是历史最后一条、重启后零次 LLM 调用 —— 历史文件 + 日志计数
- 66 session / 195279 消息 / 375 MB / 当前 2.176 GiB —— 日志统计与 `docker stats`
- 8 次 OOM 的时间与 RSS —— 内核日志全量
- 各类 `kind` 的条数与字节占比、110 次重放、872 次注入 —— 解析历史文件
- 压缩记录长度分布、`reasoning` 字段覆盖率、泄漏个例 —— 解析历史文件
- 新版 trigger 已走 `skipping history write` —— 现网日志原文

**没验到的**：

- **压缩耗时**：容器新重启，日志无压缩记录，本轮量不到。
- **内存增长曲线**：只有「48 分钟到 2.18 GiB」这一个采样点，没有连续曲线，无法推算下次撞顶
  的确切时间。
- **是哪个 session 的哪次分配触发了最后那次越界**：OOM 只报进程总量，不报归属。说「她的
  回合是最后一根稻草」有时序支撑，但**说不出她贡献了多少内存**。
- **新版相比旧版究竟改善了多少**：只有发作频率的对比（4/天 → 3/天 → 14 小时 1 次），没有
  两版稳态内存的直接对照实验。频率差异也可能只是负载波动。
- **清理 trigger 残留能回收多少内存**：按字节占比 73.9% 推算，但**未做实验**。JSON 解析后
  的内存放大比例与磁盘字节不成正比。
- **其他 65 个 session 是否也有 thinking 泄漏和 trigger 残留**：只详细解析了这一个用户的
  历史文件，其余仅有条数/大小的统计。
