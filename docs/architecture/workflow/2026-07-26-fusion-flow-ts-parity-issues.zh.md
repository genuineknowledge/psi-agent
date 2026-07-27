# FusionFlow TypeScript 待跟踪问题（PR15 后）

更新时间：2026-07-26

本页只记录固定参考 bundle 自身的问题。已确认属于参考实现的问题，不在
Python 侧单方面“修成另一套行为”；Python 为安全性或仓库接口做出的调整另见
同日对齐审计。

参考文件：

- `examples/haitun-workspace/skills/fusion-flow/runtime/agent-flow-core.bundle.mjs`
- 换行归一化为 LF 后的 SHA-256：
  `d6998574ad385674a51562413d9761f63ceee447fecbcff1be569795f9cd9da6`

## 已有 issue

- [#16](https://github.com/msg-bq/psi-agent/issues/16)：参数失效、缓存身份、
  并行挂起、binding 安全和数值边界等参考侧问题。
- [#19](https://github.com/msg-bq/psi-agent/issues/19)：GC 可能删除仍在执行的
  run。

## #16 后续补充草案

以下内容已完成源码定位，适合追加到 #16：

### 1. callback 返回值依赖 JavaScript truthiness

`loopUntil` / `loopWhile`、`filter` / `pfilter`、`retry.shouldRetry` 与
`StaticRule.predicate` 没有要求 callback 真正返回 boolean。空字符串、数字、
对象等会被静默转换，回调写错类型时不会快速失败。

`flow.if()` 与 `flow.ifElse()` 已显式检查 boolean，不属于此问题。

证据：bundle `1409-1466`、`1625-1665`、`1733-1778`、`1818-1828`。

### 2. `ifElse` 的命中分支无法从 graph 区分

所有命中项都把父节点 `takenBranch` 和子节点 `branch` 写为 `then`，没有保存
命中索引。多个条件为真时虽然执行第一个，但 graph 无法说明是哪一个。

证据：bundle `1315-1351`。

### 3. `parallel` 的失败与 grace period 会留下仍在运行的任务

- `join="all"` 使用裸 `Promise.all()`；一个任务失败后立即返回，其他任务既不
  取消，也不等待清理。
- `first` / `any` 只通过 `AbortSignal` 发出协作式取消；任意 callback 可以完全
  忽略它。
- `settleLaggards()` 到 grace period 后直接返回，未结束任务仍能在 `run()`
  封口后继续运行，并可能尝试写 binding/trace。

证据：bundle `650-659`、`1220-1282`。

### 4. `choice` / `retry` 会把取消类错误当普通失败处理

`choice` 捕获 evaluator 的所有错误后可回退到 default；`retry` 捕获所有错误后
可再次执行。两处都没有先识别 AbortError / abort signal，取消请求可能被回退
或重试吞掉。

证据：bundle `1531-1544`、`1751-1778`。

### 5. `choice` 的空 label 与 default 处理不完整

分支 label 只要求 branches 非空，没有要求 label 非空或唯一。合法分支若使用
空字符串 label，`defaultLabel=""` 又会因 truthiness 检查而永远不能作为回退。
无效 default 也不会在调用 evaluator 前报告配置错误。

证据：bundle `1509-1559`。

### 6. Windows batch 与提前关闭 stdin

- `flow.exec()` 固定 `useShell:false`；Windows 直接执行 `.cmd` / `.bat` 会被
  Node `spawn()` 拒绝。
- child 提前关闭 stdin 时，代码直接 `write()` / `end()`，没有处理 stdin
  stream 的异步 `error` / EPIPE，错误可绕开 Promise 的 `fail()`。

证据：bundle `388-402`、`493-498`、`1965-1975`。

### 7. `exec` 的 CRLF 与失败 binding

- stdout 只做 `replace(/\n+$/, "")`，Windows CRLF 会留下末尾 `\r`。
- 非截断的非零退出会先写 binding、提交调用计数，再抛错；失败运行因此留下
  看似成功的 binding，并可能影响后续 resume。

证据：bundle `1981-2013`。

### 8. `flow.output()` 会静默覆盖同名 binding

`flow.output()` 不走 `registerBindingName()`；同一运行中重复写同名 binding
会直接覆盖。`ctx.save()` 还有同类问题，并且不写 metadata。

证据：bundle `1926-1940`、`2347-2355`。

### 9. session/evaluate trace 的 duration 恒为 0

命名 trace 在 `withGraphNode()` 回填 `node.durationMs` 之前写入，队列回调读取
到的仍是 `undefined`，最后使用 `0`。graph 中的节点时长随后才得到真实值。

证据：bundle `811-836`、`1084-1118`、`1425-1461`。

### 10. 旧 `Agent()` 的 token 不进入汇总

旧入口会写独立 trace，但不创建 execution graph 节点；`aggregateTokens()` 只
遍历 graph，因此这些调用不会进入 `llmCalls`、`totalTokens` 和用户 token
汇总。

证据：bundle `2044-2072`、`2208-2260`、`2400-2418`。

## 尚未发布的 issue 1

### 标题

`bug(fusion-flow): run 目录识别、resume=last 与创建冲突需要统一校验`

### 正文

`run()`、`pickLatestRunId()` 与 `gcRuns()` 没有共享一套可识别的 run 身份
检查：

1. `pickLatestRunId()` 和 GC 把任意直接子目录当作 run，普通目录、目录链接与
   Windows junction 都可能被选中或删除。
2. run ID 只有秒级时间戳，后缀是随机值；同秒创建多个 run 时，字典序最后
   不代表实际最新。
3. 不存在的显式 `resumeFromRunId` 会被 `{recursive:true}` 静默创建，然后以
   空 cache 打印 `resuming`。
4. resume ID 未做安全单层名称与目录边界检查，路径片段可能把写入引向
   `runsDir` 外。
5. 随机 ID 没有碰撞重试；fresh 初始化允许目录已存在，碰撞时会复用并覆盖
   旧 run。

源码证据：

- bundle `2074-2079`：秒级时间戳与随机后缀；
- bundle `2136-2156`：latest 只检查 direct child 是否为目录并按名称排序；
- bundle `2158-2199`：GC 对任意直接子目录执行保留/递归删除；
- bundle `2269-2283`：resume ID 直接拼路径并递归创建；
- bundle `2296-2314`：复用目录并写 program snapshot。

此问题与 #19 不同：#19 是活动 run 识别；本项是 run 身份、路径、选择顺序和
fresh/resume 创建条件。

最小修复方向：

- 共用一个不跟随链接的 run 身份检查；
- run ID 必须是解析后仍位于 `runsDir` 的安全直接子项；
- fresh 采用排他创建，碰撞后重试；
- explicit resume 要求目标已存在且可识别；
- latest 使用可靠的创建顺序，不按同秒随机后缀猜测；
- 混合 run、普通目录、链接、同秒创建和随机碰撞都有确定性测试。

## 尚未发布的 issue 2

### 标题

`bug(fusion-flow): 并发调用与同目录 resume 会破坏运行产物的一致性`

### 正文

当前 runtime 没有把调用序号、binding、trace、progress、graph 和 meta 作为
同一个可恢复状态管理。

可复现问题：

1. 同一 `parallel()` 内并发调用同名 session/service/evaluator/exec 时，各调用
   都可能在前一次提交前读到相同计数，选择同一个 binding/trace 名，随后静默
   覆盖。
2. resume 复用原目录，但 session/service/node 计数都从 0 开始；
   `progress.jsonl` 继续追加旧内容，而 node ID 重复，最终 graph 又被覆盖。
3. 两个进程可同时 resume 同一个 run，没有目录锁；两边都按自己的内存计数
   写入，最后写入者覆盖 graph/meta。
4. 非 progress 的 `writeQueue` 任务一旦拒绝，后续 `.then()` 链可被污染；
   `run()` 的 `finally` 先 `await internal.writeQueue`，拒绝时不会继续
   `sealed=true`，也不会写最终 graph/meta。`flow.output()` 与旧 `Agent()`
   可稳定进入该路径。
5. graph 和 meta 是两次普通 `writeFile()`；进程在两次写入之间退出时，会留下
   新 graph 配旧 meta。
6. resume 没有拒绝预先放置的 artifact symlink/hardlink；append/write 可跟随
   它们修改预期 run 目录外的文件。

源码证据：

- bundle `673-688`：progress 通过共享 write queue 追加；
- bundle `711-712`：node ID 使用进程内递增计数；
- bundle `1020-1038`、`1160-1188`、`1383-1401`、`1941-1954`：调用计数先读
  后提交；
- bundle `2296`、`2316-2336`：resume 复用目录，但 graph/counters/queue
  全部重建；
- bundle `2369-2372`：finalize 先等待 queue，再 seal；
- bundle `2379-2418`：graph 与 meta 分两次写入。

最小修复方向：

- 同一 run 用锁原子预留调用序号与 artifact 名；
- 跨进程对同目录采用排他锁，或明确拒绝并发 resume；
- resume 延续 node 命名空间，或为每次执行增加 epoch；
- queue 失败后仍分别尝试 seal、graph、meta，并保留最初错误；
- graph/meta 用临时文件加原子替换，定义两者的提交标记；
- 所有 artifact 目标在打开前验证为 run 目录内的普通文件；
- 增加并发同名调用、queue 失败、同目录双 resume、崩溃窗口和链接测试。

## 写入状态

已在 GitHub 检索去重，没有发现与 #16、#19 或其他现有 issue 重复。2026-07-26
尝试创建第一项时，GitHub 连接器返回
`403 Resource not accessible by integration`；本机 `gh auth status` 同时确认
默认账号 token 已失效。因此本页保留可直接发布的完整草案，不编造 issue URL，
也不反复提交明知会失败的写操作。权限恢复后应先追加 #16，再创建上述两项。
