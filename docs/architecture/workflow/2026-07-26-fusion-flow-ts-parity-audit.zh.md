# FusionFlow TypeScript / Python 对齐审计（PR15 后续校验）

日期：2026-07-26
目标分支：本地 `codex/fusion-flow-ts-parity`

## 1. 固定参考与证据

参考文件：

- `examples/haitun-workspace/skills/fusion-flow/runtime/agent-flow-core.bundle.mjs`
- 换行归一化为 LF 后的 SHA-256：
  `d6998574ad385674a51562413d9761f63ceee447fecbcff1be569795f9cd9da6`

证据分三级：

- **源码**：逐段核对 bundle 与 Python；
- **Py**：对应 Python 自动化测试已执行；
- **Node**：测试实际启动 Node 与 Python，对比返回值或运行产物。

29 个 `flow.*` 原语和 7 个 bundle export 均已完成源码核对。Python 测试覆盖
29 个原语及除 `pickEngine` 外的 6 个 export；`pickEngine` 由显式
`SessionRunner` 取代。真实 Node 差分当前覆盖 7 个原语和 4 个 export。

真实差分在
`examples/haitun-workspace/skills/fusion-flow-next/test/execution/test_ts_reference.py`。
测试在换行归一化后固定校验 bundle hash，避免 Git checkout 的 CRLF/LF 策略改变
参考身份；Node 不存在时才跳过。本轮 Windows 环境存在 Node，相关探针实际执行。

## 2. 29 个 flow 原语覆盖矩阵

| 原语 | 源码 | Py | Node | 当前结论 |
|---|---:|---:|---:|---|
| `agent` | ✓ | ✓ | — | 省略配置保留到调用边界；普通调用与 evaluator 使用各自默认值 |
| `session` | ✓ | ✓ | — | 参数、context、缓存身份和共享调用序号已覆盖；provider 由 runner 注入 |
| `service` | ✓ | ✓ | — | 注册一致；Python 提前拒绝重复参数名 |
| `call` | ✓ | ✓ | — | 参数、序号、并发预留、恢复命中/失配与失败回滚已覆盖 |
| `parallel` | ✓ | ✓ | — | 合法 `all/first/any` 返回一致；Python 等待取消清理是明确适配 |
| `if` | ✓ | ✓ | — | 两侧都要求真正的 bool |
| `ifElse` | ✓ | ✓ | — | 执行分支一致；Python 额外保存命中索引，参考 graph 缺口另记 |
| `forEach` | ✓ | ✓ | — | 顺序、索引、空输入与失败路径已覆盖 |
| `parallelForEach` | ✓ | ✓ | — | 并发执行并按输入顺序归集 |
| `evaluate` | ✓ | ✓ | — | 提示词与 `256/0` 默认一致；Python 在 runner 后本地严格解析 |
| `loopUntil` | ✓ | ✓ | — | do-until、轮次和上限已覆盖；Python 拒绝 truthy 非 bool |
| `loopWhile` | ✓ | ✓ | — | 零次执行、轮次和上限已覆盖；Python 拒绝 truthy 非 bool |
| `choice` | ✓ | ✓ | — | 选择与 fallback 已覆盖；Python 提前拒绝空/重复 label 和无效 default |
| `map` | ✓ | ✓ | — | 顺序、索引与空输入一致 |
| `pmap` | ✓ | ✓ | — | 输出保序，并固定启动时的输入长度 |
| `filter` | ✓ | ✓ | ✓ | predicate 修改输入的真实差分已覆盖；Python 要求 bool |
| `pfilter` | ✓ | ✓ | — | 按原位置保序；Python 要求 bool |
| `reduce` | ✓ | ✓ | — | 初值、索引与空输入一致 |
| `pipeline` | ✓ | ✓ | ✓ | 值传递、步骤子节点和空 label 已覆盖 |
| `retry` | ✓ | ✓ | — | 次数、逐轮 backoff、上限、停止与取消已覆盖 |
| `evaluateStatic` | ✓ | ✓ | ✓ | 共享 RegExp 场景使用显式 flags；Python 字符串 regex 是仓库扩展 |
| `use` | ✓ | ✓ | — | 按名称调用、缺失服务和参数错误已覆盖 |
| `block` | ✓ | ✓ | — | inline block 返回值与 trace 已覆盖 |
| `defineBlock` | ✓ | ✓ | — | 注册、重名和名称安全已覆盖 |
| `runBlock` | ✓ | ✓ | — | 参数、返回值、缺失 block 和失败路径已覆盖 |
| `repeat` | ✓ | ✓ | ✓ | `0`、正整数、索引和非法次数已覆盖 |
| `input` | ✓ | ✓ | ✓ | 默认值、显式输入、同名冲突、恢复和取消回滚已覆盖 |
| `output` | ✓ | ✓ | ✓ | 值落盘一致；Python 使用单赋值 metadata，不额外创建 output 节点 |
| `exec` | ✓ | ✓ | ✓ | argv、stdin、输出、退出码、超时、取消、截断和 Windows batch 已覆盖 |

## 3. 7 个 bundle export 覆盖矩阵

| Export | 源码 | Py | Node | 当前结论 |
|---|---:|---:|---:|---|
| `Agent` | ✓ | ✓ | — | 独立调用与 run 内调用均覆盖；默认值为 `8192/1` |
| `aggregateTokens` | ✓ | ✓ | ✓ | cached 节点跳过自身但继续遍历 children |
| `assertSafeName` | ✓ | ✓ | ✓ | 共享范围一致；Python 额外覆盖 Windows 文件名边界 |
| `formatTokenCount` | ✓ | ✓ | ✓ | 数值阈值一致；Python 区分未知量与 `0` |
| `gcRuns` | ✓ | ✓ | — | retention、排除、异常候选已覆盖；参考目录识别问题另记 |
| `pickEngine` | ✓ | 不适用 | 不适用 | 仓库以显式 `SessionRunner` 取代 |
| `run` | ✓ | ✓ | ✓ | happy path 产物直接对比；Python 另测失败、取消、恢复、GC 与原子写 |

## 4. 本轮修复的 Python 回归

### 4.1 agent、session 与 evaluator

- `AgentConfig.max_tokens` / `temperature` 未填写时保留 `None`：
  - `session()` 与旧 `Agent` 在调用 runner 前解析为 `8192 / 1`；
  - evaluator 解析为 `256 / 0`；
  - 显式传入默认数值不会被当成“未填写”。
- evaluator 使用参考侧完整 system/user prompt；`number` / `choice` 的边界和
  格式在本地严格校验。
- 空 `context_schema=()` 与参考侧一样表示不校验 context。
- session 缓存中 tools 排序；仅换 allowlist 顺序不会重复调用。
- session、evaluate 与旧 `Agent` 共用每个 agent 的调用序号；失败不消耗序号，
  成功、缓存命中或已保存的旧 trace 才提交序号。
- 旧 `Agent` 在 resume 时保留旧 trace，并把实际序号写入 metadata。

### 4.2 binding、恢复与取消

- binding 名称预留、内容、metadata、调用计数使用同一取消安全提交路径。
- metadata 是 commit marker：内容先写，metadata 后写；任一步失败会恢复旧
  内容或清除半成品。
- 同一 run 内显式同名只允许覆盖一次旧 resume 产物；本次执行再次同名会失败。
- resume 对单个损坏/不可读 binding 或 metadata 记 warning 并按 cache miss
  处理，不让诊断缓存阻断新计算。
- progress 的 `node_start` / `node_end` 分别记录成功状态；取消窗口不会产生
  重复终态，也不会把取消写成普通 error。

### 4.3 控制流、集合与模型

- 串行 `filter()` 先保存 flags，再从 predicate 执行后的 items 取结果，覆盖
  predicate 原地修改输入的参考行为。
- 条件、loop、filter、retry predicate 与 static predicate 要求真正的 bool；
  参考侧 truthiness 被列为原始问题。
- retry 每次按原始配置重新计算指数 delay，再应用 `max_delay`。
- choice/evaluate 提前检查非空、唯一、数值有限和范围关系。
- token 只接受非负整数或 `None`，拒绝 bool、负数和非标准 JSON 数值。

### 4.4 exec 与多平台

- 非 Windows batch 始终使用 argv/no-shell，参数不经过 shell 解释。
- Windows `.cmd` / `.bat` 使用受控 shell 路径，并延迟还原字面量 `%`。
- batch 参数中的双引号、`!`、CR/LF 默认拒绝：
  - 双引号和换行无法在当前路径无损穿过 `cmd.exe`；
  - `!` 在被调脚本开启 delayed expansion 后会被吃掉，默认拒绝避免静默改参。
- stdin 提前关闭产生的 BrokenPipe/closed-resource 错误被忽略，stdout/stderr
  仍正常收尾。
- stdout 截断保留真实 child exit code；进程是否在 kill 前自然退出存在竞态，
  `truncated` 才是稳定标志。
- CRLF 去尾不会残留 `\r`；失败/取消不提交 binding。
- Windows batch 会尝试用 Job Object，失败时退回 `taskkill /T`；由于进程已启动
  后才挂 job，存在很小的启动窗口，因此文档不声称绝对捕获所有后代。

### 4.5 run 与最终产物

- 自动 run ID 与参考侧一样为秒级时间戳加 6 位 base36 后缀，但使用
  `secrets.choice()`。
- fresh run 排他创建；显式 resume 必须已存在；`run_id="last"` 被保留为
  resume 哨兵。
- program snapshot 按原始字节复制，失败只降低诊断完整度，不阻断 program。
- 最终 graph/meta 在 shield 中封存；progress 与单节点 trace 保持 best-effort。
- GC 参数在创建目录前校验，`0/0` 表示禁用。

## 5. 明确保留的仓库适配

这些差异不是遗漏：

1. Python API 使用 snake_case；provider 由 `SessionRunner` 注入，不读取参考侧
   的 CLI engine 环境配置。
2. Python `cache_key` 与 TypeScript `inputHash` 的输入和长度不同，两个语言的
   run 目录不保证互相命中缓存；camelCase metadata 别名只保证字段可读。
3. Python 的 string/compiled `RegexRule` 使用原生 `re` 语义。参考 API 只接受
   JavaScript `RegExp`；需要共享 `\w` 等行为时，调用方应显式选 flags，例如
   Python 使用 `re.ASCII`。
4. evaluator prompt 与解析结果对齐，但 `SessionRunner` 没有 provider JSON
   Schema 参数；结构限制在 runner 返回后本地执行。
5. 命名 `trace/*.json` 使用通用 `ExecutionTrace`，不是参考侧扁平
   provider/model/prompt schema；graph/meta 同样各用本语言字段。只有
   `progress.jsonl` 保持共享事件格式。
6. 单节点 trace/progress 写失败只记录日志；最终 graph/meta 是权威结果。参考侧
   会让部分诊断写失败中止业务调用，Python 不复制该失败耦合。
7. `flow.output()` / `ctx.save()` 写 metadata 并执行单赋值，不额外创建 output
   graph node；参考侧的静默覆盖已记录为原始问题。
8. AnyIO 结构化并发在 `first/any` 取消落后任务后等待清理，不采用 grace
   period 后脱离任务。
9. 未知 token 保持 `None`，不累计为 `0`。
10. resume 保留旧式 `Agent` trace，跳过的文件序号会占入共享调用序号；参考侧
    会从 1 重新开始并覆盖旧诊断文件。
11. 实际 runner 实现身份无法自动进入 cache key；若 runner/provider 行为变更，
    调用方必须同步改变 `AgentConfig.engine`、model 或其他版本字段。
12. Python 已安全拒绝不存在的 explicit resume 和 fresh 目录碰撞；但
    `resume_from_run_id="last"` / GC 仍沿用参考侧“任意直接目录均为候选”的
    行为。该原始问题已单独形成 issue 草案，不在 Python 先猜 marker 格式。

## 6. TypeScript 原始问题

- [#16](https://github.com/msg-bq/psi-agent/issues/16) 已记录参数失效、缓存身份、
  并行挂起、binding 安全、数值边界和旧 `Agent` 等问题。
- [#19](https://github.com/msg-bq/psi-agent/issues/19) 已记录 GC 可能删除活动
  run。
- 本轮新增：
  - #16 补充：truthy callback、`ifElse` graph、并发遗留任务、取消吞掉、
    choice 空 label、Windows batch/stdin、CRLF、失败 binding、trace duration
    与旧 `Agent` token 汇总；
  - run 目录身份、同秒 latest、路径与创建碰撞；
  - 并发调用序号、同目录 resume、write queue 与最终产物一致性。

完整可发布正文见
[TypeScript 待跟踪问题](2026-07-26-fusion-flow-ts-parity-issues.zh.md)。

GitHub 连接器创建 issue 返回
`403 Resource not accessible by integration`，本机 `gh auth status` 也确认默认
token 已失效。因此没有编造 issue URL；权限恢复后按本地草案发布。

## 7. 验证状态

PR26 原始 Windows 验证结果：

- FusionFlow 全量（包含真实 Node 探针）：`230 passed, 2 skipped`；两项 skip
  都是当前 Windows 账户没有创建符号链接的权限。
- `ruff check . --no-cache`：通过。
- `ruff format --check . --no-cache`：304 个文件均已格式化。
- `ty check .`：通过。
- `uv build`：sdist 与 wheel 均成功生成。
- `git diff --check`：通过。
- 独立只读复核覆盖核心实现、测试、文档与固定 bundle，未发现新的未分类
  Python 功能差异。
- 其余不涉及 FusionFlow 的 Windows 可运行模块：`245 passed`；另有 17 项
  全部因 `aiohttp.web.UnixSite` 调用
  `ProactorEventLoop.create_unix_server()` 抛 `NotImplementedError`。

全仓 pytest 也实际启动过，但会在同一批 Unix socket 用例中失败并在后续
socket 生命周期测试中挂起，因此没有把不完整计数写成通过结果。上述平台边界
与 FusionFlow 全量结果分开记录。

迁移到 `fusion_flow_next.execution` 后，Linux 从 FusionFlow Next Skill 目录运行
完整测试得到 `271 passed, 11 skipped`；11 项均为 Windows 专用 batch、进程树或
目录 junction 场景，真实 Node 差分探针已执行。换行归一化的 bundle hash 同时在
LF checkout 上验证通过。

## 8. 当前结论

在固定 bundle、全部公开项源码核对、Python 回归和真实 Node 差分覆盖范围内，
当前发现的 Python 自有差异均已修复或明确记录为仓库适配；参考实现自身的问题
均进入 issue 或本地待发布草案。尚未做真实 Node 双运行的项目在矩阵中保留
`—`，不把源码核对写成动态穷举证明。
