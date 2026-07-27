# FusionFlow Python 执行运行时：待讨论点与迁移歧义

> 本文不是实现完成说明，而是第二条 PR 在评审时需要逐项确认的设计账本。
> 默认原则：保留旧公开能力；会导致挂死、数据损坏、路径逃逸或取消泄漏的旧行为不保留。

## 1. 最重要的架构问题

### 1.1 执行运行时应属于 psi-agent 核心包，还是示例 skill？

当前 Python 编译器原型位于：

`examples/haitun-workspace/skills/fusion-flow-next/fusion_flow_next/`

并且仓库明确记录过“嵌套 Python 包留在示例 skill 内”。本设计则把执行运行时放在：

`src/psi_agent/fusion_flow/`

支持理由：

- 用户希望在普通 Python 中直接使用；
- 后续声明式图执行器不应依赖某个示例 workspace 的 `sys.path`；
- 运行原语可以成为多个 workspace 共用的库；
- 能纳入正常 wheel、全局 lint/type/test 范围。

反对理由：

- psi-agent 的既有定位是微内核，核心包不应吞入 workflow 产品层；
- FusionFlow 目前仍是一个具体 skill；
- `src/psi_agent` 会让尚未稳定的 API 看起来像框架承诺；
- 编译器留在 skill、运行时进核心，会形成割裂布局。

**本 PR 暂定选择**：进入 `src/psi_agent/fusion_flow`，但不从 `psi_agent` 顶层重新
导出，也不接入 CLI。若评审不接受，应在编码前改为一个独立可安装包，而不是靠
运行时改 `sys.path`。

### 1.2 静态图、执行计划和动态 trace 是否会混为一谈？

必须保持三个模型：

| 模型 | 回答的问题 | 是否允许有环 | 是否包含运行值 |
| --- | --- | --- | --- |
| `WorkflowGraph` | 用户声明了什么 | 是 | 否 |
| `ExecutionPlan` | 本次准备怎么执行 | 取决于 planner | 只含引用/决策 |
| `ExecutionTrace` | 实际发生了什么 | 可重复访问同一节点 | 是 |

旧 TS 运行时会输出 `execution-graph.json`，名字容易让人误以为它就是声明式图。建议
保留文件名兼容，但模型名明确叫 `ExecutionTrace` 或 `ObservedExecutionGraph`。

### 1.3 本 PR 要不要同时实现图执行器？

不实现。原因不是图最终不执行，而是 planner 仍有关键语义没有定论：

- 有环图何时触发下一轮；
- Artifact 是单值、版本流还是事件；
- `consumes` 是读取最新值还是消费一个 token；
- 多生产者如何选择；
- 何时终止；
- `if` 和 `choice` 如何激活边。

在这些问题未定前把图接到运行时，只会把临时假设固化成 API。

### 1.4 human executor 由谁执行？

当 `executor_id` 指向的 Core IR/catalog identity 属于 `Human` concept 时，表示执行
需要人的参与；静态图不另存 `executor_kind`，它也不应映射成某个自动调用的
`flow.*` 函数。图读取与计划层需要把它降解为：

1. 暂停当前计划；
2. 发出审批或外部事件请求；
3. 持久化可恢复状态；
4. 收到人的结果后恢复并产生对应 Artifact。

这也说明 planner/executor adapter 仍是独立新模块；`fusion_flow` 只提供可直接执行的
Agent、Python callable、subprocess 及运行记录原语。

## 2. 旧 TS API 的冗余是否保留

### 2.1 `Agent` 与 `flow.agent` / `flow.session`

三者重叠，但旧调用方可能分别依赖：

- `Agent`：v0.1 包装器；
- `flow.agent`：一次性具名 Agent 步骤；
- `flow.session`：延续会话身份。

**暂定**：都保留，底层共享 `SessionRunner`。文档明确 `Agent` 是兼容入口，不再为
它发展第二套能力。

### 2.2 `service` / `call` / `use`

`use` 本质上是按服务名调用，和 `call` 重叠。

**暂定**：三者保留；`use` 内部直接委托 `call`，只保留一个实现路径。

### 2.3 `forEach` 与 `map`

旧 `forEach` 只表达副作用、返回 `void`；`map` 返回结果。不能因为 Python list
comprehension 方便就合并，否则会改变调用者对内存和输出的预期。

### 2.4 `block` 与 `defineBlock` / `runBlock`

`block` 是立即执行的内联边界，label 可以重复；后两者提供注册与复用，只有
`define_block` 的定义名需要查重。三者共享 trace 和安全名称校验机制。

## 3. 并行语义

### 3.1 `parallel(any=n)` 的 `any` 是“任意完成”还是“任意成功”？

旧实现存在挂死边界，且失败是否计数不够明确。

**首版暂定**：保留源码行为——按完成顺序取前 `n` 个结果，但任一任务失败会立即使
整体失败；同时补上 `n` 的整数、下界、上界和空列表校验，消除永久等待。

需要讨论：调用者是否更需要“前 n 个成功”。如果需要，应使用不同 mode 名，而不
静默改变旧 `any` 的失败传播。

### 3.2 `parallel(first)` 是最先完成还是最先成功？

旧实现近似 `Promise.race`：最先失败也使整体失败。

**暂定**：忠实保留“第一个 settle”。
**替代方案**：改为第一个成功，更符合容错竞速。
若修改，必须记录为行为变化并补上全部失败时的聚合错误。

### 3.3 取消落后任务后，trace 状态是什么？

旧实现可能把主动取消的 laggard 记成 error。建议状态独立为 `cancelled`，并记录
原因如 `parallel-first-laggard`，否则错误率和重试判断都会失真。

### 3.4 fail-fast 还是等待全部 settle？

AnyIO task group 天然适合结构化取消。建议：

- `all`：一个失败时取消兄弟任务，清理完成后抛出；
- `first` / `any`：满足选择条件后取消落后任务；
- trace 必须等待所有子任务进入终态后再封存。

这避免旧实现中顶层已返回、后台任务仍写 bindings 的“幽灵写入”。

### 3.5 Python callback 无法被强制取消

任意 callback 如果阻塞 event loop、没有取消检查点或吞掉取消，AnyIO 也不能安全
强杀它。Python 首版不提供固定 laggard grace 配置，而是依赖结构化取消，等待受控的
Agent runner、subprocess 和兄弟任务完成清理。

首版要求 callback cancellation-cooperative；运行时不会 detach 一个仍能写 bindings
的任务。是否需要把不可控工作统一隔离到子进程，留给后续设计。

## 4. 条件、选择与新 DSL 的不对齐

### 4.1 `flow.if_` 是执行控制流，不等于 Core IR 的值表达式 `if`

当前 `FusionFlow.g4` 把 `if(condition, then, else)` 固定为产生一个值的表达式；旧
`flow.if` 则执行回调。二者不能用同一个 lowering 规则直接对应。

后续 compiler 至少要区分：

- 值级条件表达式；
- 控制流分支；
- 图中的条件激活 Region/Edge。

### 4.2 `flow.if_else` 的默认分支

旧源码合同已经明确：没有任何条件命中、且没有 else 时返回 `None`。第一版保留；
它不等于静态图已经接受“可选输出 Artifact”，图 lowering 仍需单独定义未选分支的
输出闭合方式。

### 4.3 `flow.choice` 与静态图分支

`choice` 可能由 Agent 动态决定，静态分析只能知道候选集合，不能在运行前知道所选
分支。图模型后续需要“候选边 + 运行时决策记录”，不能把它伪装成普通确定性边。

### 4.4 `evaluate` 的返回契约

旧 `evaluate` 同时承担表达式求值、布尔判断、数字范围和枚举选择，职责偏宽。

**暂定**：为兼容保留一个入口和相同模式，但内部使用明确 discriminated config，
拒绝空 choice、重复 choice 和反向数值范围。未来可新增窄 API，不能删除旧入口。

`evaluate_static` 则使用独立的判别联合
`RegexRule | ContainsRule | EqualsRule | RangeRule | PredicateRule`。调用方每次只能
传入一种规则；predicate 是零参数 callable，持久化结果同时包含 `value` 与 `rule`。
它不复用 `evaluate` 的扁平可选参数，也不调用 Agent。

## 5. 循环与有环图不是一回事

`flow.loop_until` / `flow.loop_while` 是结构化、局部、有最大次数的动态控制流。
声明式 WorkflowGraph 的环则只是静态拓扑事实。

二者差异：

- loop 有一个明确 body 和 condition；
- 图环可能跨多个 Step/Artifact；
- loop 的状态通常在 Python 闭包里；
- 图环需要 Artifact 版本、激活规则和终止判定；
- loop 可以直接限制次数，图环可能按收敛、事件耗尽或外部取消终止。

因此，未来 planner 不能看到一个 SCC 就机械 lower 成 `flow.loop_until`。只有当图中
明确声明了循环控制策略时，才可能做这种转换。

### 5.1 达到最大次数算成功还是失败？

旧实现可能只 warning 然后成功结束。

**首版暂定**：为逐项迁移，保留旧行为——warning 后正常返回；同时在 trace 中记录
`hit_max_iterations=true`。
**建议的未来严格模式**：条件未满足即失败。否则下游无法区分“真的完成”和“被
安全阀截断”。不能在兼容入口中未经确认直接切换默认。

### 5.2 `repeat(0)` 是否允许？

建议允许并返回空/`None`，因为固定次数零次是良定义的；负数和非整数拒绝。

## 6. retry 策略冲突

旧运行时默认 retry 3 次；拟议 WorkflowGraph `RetryPolicy` 默认 `max_attempts=1`。
这里还存在“重试次数”是否包含第一次尝试的命名歧义。

建议统一使用 `max_attempts`，包括第一次执行：

- `max_attempts=1`：不重试；
- `max_attempts=3`：最多执行 3 次。

运行时兼容入口可以默认 3，但从图 lower 时必须显式传图策略，不能悄悄套运行时
默认值。

需要定义哪些错误不重试：

- 取消：永不重试；
- 参数/名称/权限/安全错误：永不重试；
- Agent 拒绝或业务校验失败：由策略决定；
- timeout/临时网络错误：通常可重试；
- subprocess 非零码：需显式 opt-in。

## 7. binding、Artifact 与输出

旧 `flow.output` 实际写 bindings，不等于声明式图的 Artifact store。未来对接必须
选择：

1. 每个 Artifact 映射到一个 binding；
2. binding 只是一次 Step 的局部值，Artifact 由独立 store 管理；
3. 两者都存在，通过 adapter 映射。

**建议 2/3**：Artifact 可能有多个版本或多次激活，而 binding 默认单写一次。把两者
直接等同会立刻与有环执行冲突。

### 7.1 重复写策略

旧实现有些入口检查重复、有些入口会覆盖。统一为默认单写。若未来需要循环中多版本
写入，应使用 Artifact activation/version，不要给 binding 加一个模糊的 overwrite。

### 7.2 操作失败时何时提交

所有操作遵循两阶段：

1. 预留名字；
2. 执行并验证结果；
3. 成功后提交；
4. 失败/取消释放预留。

尤其 `exec` 非零退出、Agent 返回校验失败时不得留下“看起来成功”的值。

### 7.3 诊断写入失败是否应回滚业务结果？

首版明确区分关键状态与诊断状态：

- binding、最终 graph 和 meta 是关键状态，写入失败则本次运行失败；
- `progress.jsonl` 与单节点 trace 文件只是增量诊断，best-effort 写入，失败只记日志。

否则会出现 binding 已提交，却因为随后写一条 progress 失败而向调用者报告整个业务
操作失败的“幽灵失败”。最终 graph/meta 从内存 trace 汇总，仍是完整运行事实的关键
落盘。

## 8. resume 的真实含义

旧代码的 resume 容易被理解为“从任意一行继续执行”，但普通 Python 协程不能仅靠
JSON trace 恢复调用栈。

旧源码的 resume 行为是：

- 选择一个既有 `run_id/run_dir`；
- 读取其中可复用的 binding 与 metadata；
- 重新执行顶层程序；
- 只有 `session`、`call` 会用稳定 binding 名和输入摘要尝试命中并跳过实际调用；
- `evaluate`、`exec`、静态判断及任意 callable 会重新执行；
- graph/meta 最终仍写回同一 run 目录。

第一版为“逐项翻译”暂时保留这个外部合同，同时增加安全名称、规范化摘要和原子
写入。它不承诺：

- 恢复正在运行的 subprocess；
- 恢复 Agent 流式连接；
- 恢复闭包内部状态；
- 在旧 trace 的任意子节点恢复调用栈；
- 反序列化任意 callable。

同目录更新会抹去“恢复前”的最终 graph/meta，审计性较弱。更安全的替代方案是新建
派生 run 并记录 `resume_from_run_id`，但这是行为变化，需要单独确认，不能在翻译
PR 中默认替换。

### 8.1 实现身份如何计算

只 hash 参数不够：函数体变了仍会错误复用。可选方案：

- `inspect.getsource()`：对动态/打包函数不稳定；
- 模块文件 hash + qualified name：相对稳定但成本高；
- 调用者显式提供 version/cache key：最可靠；
- 第一版不复用自定义 callable，只复用少数内建操作。

**暂定**：显式 key 优先；无法可靠识别时不复用。

### 8.2 哪一层决定副作用是否重放

旧 TS 不缓存 `evaluate` 与 `exec`。这意味着 resume 时可能重新选择分支，也可能
重复执行脚本副作用。Python 保留这个可观察边界：resume 后第一次重跑会替换同名
历史 binding，本次执行再次写同名才失败；后续调用按共享调用序号生成新 binding。

不建议把所有原语一律改成自动缓存：

- `exec` 可能是查询，也可能是部署、发消息、写数据库，运行时无法仅凭 argv 判断；
- `evaluate` 的重新判断有时是缺陷，有时是调用者希望使用最新上下文；
- Human 节点还需要审批状态、外部事件与恢复令牌，不能折叠为字符串 cache。

**待决定**：未来 planner 是否为每个计划动作生成稳定 action id 与显式
`replay_policy`（例如 `reuse`、`rerun`、`require_confirmation`）。在此之前，
调用方不能把 `resume_from_run_id` 当作端到端 exactly-once 保证。

### 8.3 路径安全

`resume_from_run_id="../../..."` 必须在读文件前被拒绝。`gc_runs` 也必须解析并核验
每个目标仍位于 runs 根目录内。

## 9. Agent/session 边界

### 9.1 为什么不直接调用 `SessionAgent`

`SessionAgent` 负责完整对话历史、workspace tools、schedule 和 AI SSE 协议适配。
把它直接塞进 flow step 会带来：

- 一次 flow run 与一份 conversation 的生命周期冲突；
- session id 与 flow session label 重名；
- tool trace 重复；
- 取消时两套持久化的一致性问题。

建议通过 `SessionRunner` 协议注入 adapter，之后另 PR 决定 adapter 如何访问现有
socket API。

旧 `flow.session(agent, prompt, context)` 每次直接发起独立 provider 调用；它没有
聊天 history，也没有“同名 agent 自动延续同一 session identity”的合同。

### 9.2 未来 session 延续范围

如果 adapter 以后接入有状态 Session，才需要讨论 session identity 是：

- 仅当前 run 内；
- 跨 run 持久；
- 由调用者明确提供外部 session id。

**首版暂定**：不引入 session identity 或历史复用；每次都是独立 invocation。
scope、共享、foreach/retry 是否复用都保持 OPEN。

### 9.3 token 统计

Agent provider 可能不给 usage 或给出不同字段。模型应允许未知 token，聚合时不能把
未知误写成 0。格式化函数应区分 `None` 与 `0`。

## 10. exec 语义与安全

### 10.1 环境变量

旧 TS 类型文档要求干净环境并仅保留 `PATH`，Python 首版则明确继承父环境，再覆盖
调用方传入的值，符合普通 subprocess 预期。这是兼容性差异，不再表述为实现不明。
完全隔离可以在确有需求时增加 `inherit_env=False`。

### 10.2 stdout/stderr 截断

只在内存中停止读取会再次导致 PIPE 阻塞。正确策略是持续消费，但超过上限后丢弃
额外字节并标记 `truncated=True`。

### 10.3 timeout 与进程树

Windows 和 POSIX 的进程树终止不同。当前实现始终终止并 wait 直接子进程；Windows
batch 另使用新进程组，并尝试通过 Job Object、失败时通过 `taskkill /T` 清理进程树。
Job Object 在进程启动后挂接，仍有很小的启动窗口，因此不能宣称绝对捕获所有后代。

### 10.4 shell

第一版接口为 `flow.exec(name, argv, ...)`：`name` 是稳定操作身份、trace label 和
默认 binding 前缀，`argv` 才是逐项传给子进程的命令，不提供 `shell=True`。如果未来
确有 shell pipeline 需求，必须是显式危险 API，并重新设计转义和审计边界。

## 11. 不能照搬的 TS 缺陷清单

以下项目应视为迁移时的修复，而不是兼容性回归：

1. `parallel(any)` 的非法数量或空任务导致等待永不结束；
2. binding 在重复检查前已覆盖旧值；
3. `evaluate`、`evaluateStatic`、`exec` 的显式 binding 没有统一登记重名，
   `output` 还缺少统一安全名称校验；
4. session trace 在计时结束前写 duration，结果长期为 0；
5. loop/retry/repeat 不验证次数和退避参数；
6. `evaluate(choice)` 接受空 options，`choice` 接受重复 label/无效 default，
   数字范围接受 `min > max`；
7. loop condition 使用 truthy，而 `if`/`ifElse` 已经严格检查 bool；
8. service resume 摘要受对象键顺序影响且不包含实现身份；
9. parallel 失败后兄弟任务继续运行并产生幽灵写入；
10. 主动取消的 laggard 被统计成 error；
11. retry 吞掉取消或永久错误；
12. exec 在确认退出成功前提交 binding；
13. env 的文档语义与实现语义不一致；
14. `resume_from_run_id` 可能参与不安全路径；
15. `ifElse` trace 不记录所选分支；
16. 失败后部分 JSON 已落盘，形成不可解析 run。

## 12. 历史文档与当前源码的漂移

以下能力在旧 spec/README 中出现过，但不属于本次“逐项翻译”的当前实现合同：

- `flow.resume` 从未成为公开实现；
- `flow.use(path)` 导入外部程序未实现，当前 `use` 只是按名字调用 service；
- 链式 `flow.pipeline(data).map().filter().run()` 未实现，当前合同是
  `pipeline(input, steps)`；
- `evaluateStatic` 并非加载期缓存，而是每次调用执行静态 rule；
- `output` 实际写 `bindings/`，没有独立 `outputs/` 和 output graph node；
- `discretion/<id>.json` 未实现；
- README 中 `flow.call(agentHandle, ...)` 与真实类型冲突；Agent 必须走 `session`；
- `flow.spawn` 已被有意拆除：LLM 路径归入 `session`，命令路径归入 `exec`。

Python PR 不补造这些历史能力。若未来需要，应作为新的、单独评审的 API，而不是
借“翻译”名义悄悄恢复。

`pickEngine` 和 CLI provider 栈是本次唯一明确缩减的当前实现能力：它们与 psi-agent
已有 AI/Session 层重复，且包含 CLI 交互和进程级行为。Python 运行时改为注入
`SessionRunner`。这项缩减需要在书面设计评审中明确接受；其余 `RunContext.input`
和 `RunContext.save` 等运行容器能力继续保留。

## 13. 需要在 PR 评审中明确回答的问题

1. 是否接受运行时进入 `src/psi_agent`，从而扩展微内核发行物？
2. `parallel(first)` 首版保留 first-settle 后，未来是否另增 first-success？
3. `parallel(any=n)` 首版保留“任一失败则失败”后，未来是否另增 first-n-success？
4. `parallel(all)` 是否接受“失败前先取消并收尾兄弟任务”的安全偏差？
5. 循环首版保留“达到上限 warning 后返回”后，是否需要显式严格模式？
6. `retry` 的次数是否统一定义为包含首次执行的 `max_attempts`？
7. resume 是否继续更新同一 run，还是未来改成新建派生 run？
8. `exec` 是否需要进程树级终止作为首版验收项？
9. 旧 `Agent` 兼容入口预计保留多久，是否标记 deprecated？
10. `execution-graph.json` 是否为了兼容保留文件名，但内部模型改名为 trace？
11. 图执行器将 Artifact 与 binding 映射时，是否确认使用独立 Artifact store？
12. 是否确认不翻译 `pickEngine`/旧 CLI provider 栈，改用注入 runner？
13. human executor 的暂停、审批、外部事件与恢复协议由哪个图执行模块负责？
14. 是否接受 progress/单节点 trace 为 best-effort，而 binding、最终 graph/meta 为关键状态？
15. 是否接受 Python `exec` 继承父环境这一项与旧 TS 类型文档不同的明确偏差？
