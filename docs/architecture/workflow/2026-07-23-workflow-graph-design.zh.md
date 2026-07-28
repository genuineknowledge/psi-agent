# Workflow Graph 初版设计：允许有环的声明式 Step–Artifact 图

> 状态：已评审；2026-07-23 补充确认 Executor 包含 Human、Agent、Program
> 日期：2026-07-23
> 目标 PR：独立于 FusionFlow Python 运行时 PR
> 图模型：`src/psi_agent/workflow_graph/`
> Core IR 后端：`examples/haitun-workspace/skills/fusion-flow-next/fusion_flow_next/graph_compiler.py`

## 1. 结论

Workflow Core IR 中与执行依赖相关的 assertion，应编译为一个**类型化、有向、允许
有环的 Step–Artifact 图**。

它不是 DAG，也不是一次运行的状态机，更不是旧 FusionFlow
`execution-graph.json` 中的动态 trace。

初版图只保存静态定义：

- 有哪些 Step；
- 有哪些 Artifact；
- Step 读取和产生哪些 Artifact；
- 哪个 List Artifact 在运行时展开 foreach slots；
- 已确认的 timeout、attempt、resource 和 workflow policy；
- 哪些 Core IR assertions 暂时不能进入图。

一个环可以被保存、序列化和分析，但没有 seed、Artifact version、Step activation、
commit 与 termination 合同之前，不能据此宣称该环已经可执行。

## 2. 三层模型

```text
Workflow Core IR
    └── assertion、term、formula、identity
            |
            | WorkflowGraphCompiler.compile()
            v
WorkflowGraph
    └── 静态 Step、Artifact、关系、policy；允许有环
            |
            | 未来 planner
            v
ExecutionPlan
    └── 某次 run 的 ready frontier、slot、activation、调度决定
            |
            | 未来 executor
            v
ExecutionTrace
    └── 实际 attempt、值、时间、错误、取消和事件
```

分层约束：

- `psi_agent.workflow_graph` 只定义通用图模型，不导入 example skill；
- `fusion_flow_next.graph_compiler` 继承 PR12 的 `CoreIRCompiler`，并单向导入图模型；
- 未来 `workflow_runtime` 可以同时依赖二者；
- Core IR 是语义事实的上游，Graph 是执行依赖的派生视图；
- 未编译进图的 Core IR 不能被假装已经进入 Graph。

## 3. 为什么选择 Step–Artifact 数据流图

| 模型 | 优点 | 不作为初版主模型的原因 |
| --- | --- | --- |
| DAG | lineage、并行度和拓扑调度直观 | 用户明确要求允许环；DAG 会错误排除反馈结构 |
| 整图 FSM | 控制状态直观 | 并行 Step 导致状态笛卡尔积，Artifact 退化为附属数据 |
| CFG | 分支和循环自然 | Artifact provenance、fan-out/fan-in 和隐式并行不自然 |
| Petri Net | 并发、join、choice、环表达力强 | 默认 token 消耗与当前非破坏性 `consumes` 不一致，会提前决定未闭合语义 |
| Step–Artifact 图 | 直接对应依赖、接口、foreach 和 provenance | 需要把运行状态、控制 Region 和环执行合同留给后续层 |

Petri Net、SCC 分析或 Region CDFG 可以成为以后独立的分析/lowering backend，不能
反向决定作者语法或让初版 Python 模型过度抽象。

## 4. 包结构

```text
src/psi_agent/workflow_graph/
├── __init__.py
└── model.py       # 不可变模型、结构校验、确定性序列化

tests/psi_agent/workflow_graph/
├── __init__.py
├── test_model.py
└── test_public_api.py

examples/haitun-workspace/skills/fusion-flow-next/
├── fusion_flow_next/graph_compiler.py  # CoreIRCompiler 的 WorkflowGraph 后端
└── test/test_graph_compiler.py
```

不创建 scheduler、store、analyzer、region 或 registry。编译遍历复用 PR12
`CoreIRCompiler`，不再为图后端另写一套遍历或通用基类。

## 5. 静态模型

### 5.1 标识

所有标识都是非空字符串，并保留上游 Core IR identity，不把 identity 当展示文本。

- `workflow_id`
- `step_id`
- `artifact_id`
- `name_id`
- `instruction_id`
- `executor_id`
- `resource_id`

Step 和 Artifact 位于不同的类型空间，但初版仍拒绝同名，以避免日志、序列化、
operation/value catalog 查找和将来诊断产生歧义。

### 5.2 StepNode

```text
StepNode
├── step_id
├── name_id
├── executor_id
├── instruction_id?
├── timeout_seconds?
├── max_attempts
├── resources[(resource_id, amount)]
├── depends_on[predecessor_step_id]
└── independent
```

合同：

- 每个被引用 Step 必须恰好有一个 `step_name`；
- 每个可分派 Step 必须恰好有一个 `step_executor`；
- `step_instruction` 第一版允许缺省；
- `max_attempts >= 1`，含第一次执行；
- timeout 和 resource amount 为正整数；
- resources 以 `(step_id, resource_id)` 识别，不使用可碰撞的字符串拼接 key；
- `depends_on` 保存不伴随 Artifact 值传递的显式顺序前驱，引用必须存在且不能重复；
- `independent` 是非约束 metadata，不能覆盖 Artifact 依赖或 `depends_on`。

Artifact 边回答“这个值从哪里来”，`depends_on` 回答“即使不传值，谁也必须先完成”。
结构模型允许保存两者共同形成的环；one-shot planner 决定是否能够执行。

`executor_id` 仍是 Core IR 中的具体 constant identity，不把 Python callable、命令或
人工审批配置塞进静态图。上游已确认 `Executor` 的三个直接子 concept 是 `Human`、
`Agent`、`Program`；每个 executor identity 必须且只能属于其中一类。Graph 不增加
`ExecutorKind` 或 `executor_kind` 字段，避免与 Core IR concept 类型形成第二份真源。
未来 dispatcher 通过原始 Core IR/catalog 解析 identity 的类型与配置。
`program_path(program) = path_identity` 与
`agent_system_prompt(agent) = instruction_identity` 属于 catalog/dispatcher
配置，保留在 residual 中，不复制进 `StepNode` 或 `WorkflowGraph`。

### 5.3 ArtifactNode

```text
ArtifactNode
├── artifact_id
├── is_input
├── is_output
└── binding_step_id?
```

`binding_step_id` 非空时，该 Artifact 是 foreach invocation-local item binding：

- 只在指定 Step 的一个 slot 内可见；
- 不能作为 workflow input/output；
- 不能由普通 Step 产生；
- 不能被其他 Step 消费；
- 不能再次作为 foreach source。

初版不在 ArtifactNode 中复制 Concept/type、运行值、版本、checksum 或存储位置。
上游 Core IR/checker 仍负责 Concept compatibility；是否把 type reference 下沉到图，
留作待讨论项。

### 5.4 三类边

```text
ConsumesEdge(artifact_id, step_id)
ProducesEdge(step_id, artifact_id)
ForeachEdge(artifact_id, step_id, item_binding_id)
```

语义：

- `ConsumesEdge`：非破坏性读取；同一 Artifact 可以 fan-out；
- 多条普通 consumes：all-ready / AND；
- `ProducesEdge`：Step 成功后提交一个 Artifact；
- 一个 Artifact 初版最多一个静态 producer；
- `ForeachEdge`：source 是有限 List，运行时按 index 建 slot；
- 一个 Step 初版最多一个 foreach source；
- item binding 是 slot-local，不静态复制未知数量的 Step。

不生成 Entry、Exit、Fork、JoinAll、Aggregate、Decision、Merge 或 State 专用节点：

- ready 且无依赖关系的 Step 天然可以并发；
- 多 consumes 已表达 all-ready；
- 需要独立审计、失败、retry 边界的聚合应是普通 Step；
- first/any、lazy branch 和条件控制流尚未闭合，不能用假节点占位。

### 5.4.1 SelectNode

`SelectNode` 是图中唯一的条件选择结构：

```text
SelectNode
├── output_artifact_id
├── when_true_artifact_id
├── when_false_artifact_id
└── condition
    ├── ComparisonCondition(eq | lt | lte | gt | gte)
    └── LogicalCondition(not | and | or)
```

条件操作数只能是全局 Artifact 或 JSON scalar literal。Select 会等待条件引用及两个
候选 Artifact 全部可用，再把被选候选的值发布为 `output_artifact_id`。因此它是
**eager 值选择**，不是控制流：两个候选 producer 都会运行，不能据此承诺未选分支
不启动。

### 5.5 WorkflowPolicy

初版只保留已确认的 run-level 上界：

- `max_concurrency`；
- `timeout_seconds`。

不提供任意 `attrs`/`config` 字典。开放语义应留在 residual 或新版本模型中，不应先
进入一个无人能静态检查的 escape hatch。

### 5.6 WorkflowGraph

```text
WorkflowGraph
├── workflow_id
├── steps: tuple[StepNode, ...]
├── artifacts: tuple[ArtifactNode, ...]
├── edges: tuple[ConsumesEdge | ProducesEdge | ForeachEdge, ...]
├── policy: WorkflowPolicy
└── selectors: tuple[SelectNode, ...]
```

模型使用 frozen dataclass 和 tuple，构造后不可变。`to_dict()` 返回仅由 JSON
primitive 构成的精确 TypedDict 合同。

三个 edge payload 的 `kind` 使用 `Literal["consumes"]`、
`Literal["produces"]`、`Literal["foreach"]`，不是宽泛 `str`。

## 6. 允许环

结构校验**不执行 acyclic 检查**。以下结构合法保存：

```text
Artifact A -> Step S1 -> Artifact B -> Step S2 -> Artifact A
```

但是静态合法只说明：

- 节点和边引用一致；
- 每个 Artifact producer 合同没有冲突；
- 图可以 round-trip 和做 SCC/lineage 分析。

它不说明：

- 第一次激活从哪里来；
- S1/S2 是否会执行一次还是多次；
- A/B 读哪个版本；
- 一次 attempt 的输出何时提交；
- 新版本是否重新激活消费者；
- 何时收敛或停止；
- retry 和循环 iteration 如何组合。

任意有向图的 SCC 缩点图必然是 DAG，可以用于以后分析组件间的依赖顺序；这不等于
原始 WorkflowGraph 是 DAG。

## 7. Core IR 编译边界

### 7.1 复用 PR12 编译器

`fusion_flow_next.graph_compiler.WorkflowGraphCompiler` 继承
`CoreIRCompiler`，输入是真实的 `WorkflowFile`、`Workflow`、`Assertion`、
`CompoundTerm`、`Constant` 和 `ListTerm`。它不解析 BNF 文本，也不接受
duck-typed DTO。

`CoreIRCompiler.compile()` 负责遍历文件、声明和 workflow；图后端只实现
`_compile_*` node hooks 以及 `_build_workflow()`、`_build_program()`。未知 term
不会被近似处理。图后端只认领顶层的命名 Artifact 选择；内联或递归 `IfTerm`
仍沿共享编译器的 fail-closed 路径显式报错。

`Assertion` 类型本身表示 equality，不再读取源 token 或兼容额外 relation 字段。
`step_executor` 的类型信息直接来自 `Constant.belong_concepts`；值非空时，图后端
检查它在 `Human`、`Agent`、`Program` 中恰好命中一类。空值只保留
`executor_id`，完整 catalog 类型校验仍属于上游 checker。Graph 自身不复制该分类。

### 7.2 已知 operator

FusionFlow grammar catalog 仍有 21 个 preset operator；runner 的 typed context
另外注入 `independent` 和 `depends_on`。图后端当前识别：

- `input_workflow`
- `output_workflow`
- `step_name`
- `step_instruction`
- `step_executor`
- `consumes`
- `produces`
- `foreach_item`
- `step_timeout`
- `max_attempts`
- `resource_requirement`
- `max_concurrency`
- `workflow_timeout`
- `independent`
- `depends_on`

已知 operator 如果 arity、RHS、owner 或类型形状错误，直接产生
`WorkflowGraphCompilationError`，不能伪装成 residual。

`program_path` 与 `agent_system_prompt` 有意留给未来 catalog/dispatcher，按源码
顺序进入 residual。

arity 以 `CompoundTerm.arguments` 的实际长度为准，不使用 `Operator.arity`。当前
Core IR 中 `Operator.arity` 来自可选 catalog 签名，手工构造或尚未经过 checker 的
operator 默认可能为 0，不能代表应用实参数量。

### 7.3 mapping

`Assertion` 表示等式而不是赋值。已知图算子可以位于等式任意一侧；compiler 先将唯一的
图算子规范化为 `graph_call = value`，再复用同一 lowering 路径。若等式两侧都是已知
图算子，则显式抛出 `WorkflowGraphCompilationError`，不能进入 residual。

| Core IR assertion | WorkflowGraph |
| --- | --- |
| `input_workflow(w) == [a, ...]` | 每项成为 `ArtifactNode(a, is_input=True)` |
| `output_workflow(w) == [a, ...]` | 每项成为 `ArtifactNode(a, is_output=True)` |
| `step_name(s) = n` | `StepNode(s).name_id = n` |
| `step_instruction(s) = i` | `StepNode(s).instruction_id = i` |
| `step_executor(s) = e` | `StepNode(s).executor_id = e` |
| `consumes(s) == [a, ...]` | 每项成为 `ConsumesEdge(a, s)` |
| `produces(s) == [a, ...]` | 每项成为 `ProducesEdge(s, a)` |
| `foreach_item(s, source) = item` | `ForeachEdge(source, s, item)` + local Artifact |
| `step_timeout(s) = n` | `StepNode(s).timeout_seconds = n` |
| `max_attempts(s) = n` | `StepNode(s).max_attempts = n` |
| `resource_requirement(s, resource) = n` | `StepNode(s).resources += (resource, n)` |
| `independent(s) == True` | `StepNode(s).independent = True`，仅保留 metadata |
| `depends_on(s, predecessor) == True` | `StepNode(s).depends_on += predecessor` |
| `max_concurrency(w) = n` | `WorkflowPolicy.max_concurrency = n` |
| `workflow_timeout(w) = n` | `WorkflowPolicy.timeout_seconds = n` |
| `selected == if(condition, a, b)` | `SelectNode(selected, a, b, condition)` |

其中所有 `n` 都从 `Constant.symbol` 显式解析为正整数。resource identity 使用
`(step_id, resource_id)` 结构化键，不把 resource 或 amount 建成 Artifact。

命名选择要求 `selected`、`a`、`b` 都是已声明的全局 Artifact。比较节点支持
`=`、`<`、`<=`、`>`、`>=`；表层 `!=` 规范化为 `!(a = b)`，不新增独立比较类型。
条件还支持 `!`、`AND`、`OR`。多级优先级通过多个命名 Artifact 选择串联；
`consumes(s) == [if(...)]` 和分支中的嵌套 `IfTerm` 均拒绝。

普通值 assertion，例如 `files = [file_a, file_b]`，不展开进静态图，留在 residual 供
值求解或 runtime 使用。

### 7.4 residual

未知、图无关且结构合法的 assertions 保留在：

```text
WorkflowGraphCompilation
├── graph
└── residual_assertions
```

`WorkflowGraphCompiler.compile(WorkflowFile)` 为每个 workflow 返回一个
`WorkflowGraphCompilation`。residual 保留真实 `Assertion`，因此 compilation 本身
不是持久化 payload。需要持久化时，调用者分别保存原始 Core IR 和
`graph.to_dict()`。

普通 equality、未知 compound operator，以及 catalog/dispatcher-owned 的
`program_path` 与 `agent_system_prompt` 可以进入 residual；已识别的图关系或共享
编译器不支持的递归节点都必须显式失败。

## 8. canonical dataflow ListTerm 的信息边界

`input_workflow`、`output_workflow`、`consumes`、`produces` 都从真实
`ListTerm.items` 读取。图后端把它们编译为关系：

- List 项展开为普通边或 I/O 标记；
- 重复项报错，避免静默丢 multiplicity；
- 该 List 只是批量书写关系的载体，项的源码顺序没有执行含义；
- WorkflowGraph 保留全部依赖关系，但不保留载体的源码顺序；
- 因此这不是从 repository Core IR 到 Graph 的无损 round-trip；
- 原始 Core IR 必须由调用者继续保存。

这里的“非无损”仅指无法从 Graph 还原 `[a, b]` 还是 `[b, a]`，不表示丢失执行依赖。
不得再用“List 无重复时无损”描述它，因为两种写法会编译为同一组关系。

## 9. 结构校验

构造完成后检查：

1. workflow、Step、Artifact identity 非空且各自唯一；
2. Step/Artifact identity 不冲突；
3. 所有 Step 都有且只有一个 name 和 executor；
4. instruction 至多一个；
5. 所有边端点存在且方向正确；
6. 相同关系边不重复；
7. 普通 consumed、foreach source 或 workflow output 的全局 Artifact，必须是
   workflow input 或有 producer；
8. 一个 Artifact 最多一个静态 producer；
9. 一个 foreach Step 最多一个 source；
10. local binding 不跨 Step 泄漏，也不参与 workflow I/O、普通 produces 或 nested
    foreach；
11. timeout、concurrency、resource amount 为正整数；
12. `max_attempts >= 1`；
13. 每个 Select 的输出和输入 Artifact 都存在、均为 global，且输出没有其他 producer；
14. Select 输出不重复，条件引用的 Artifact 可用；
15. `depends_on` 必须是 Step ID tuple，前驱存在且不重复；
16. 不检查 acyclic；自依赖与多 Step 环由 planner 拒绝。

“input Artifact 同时有 producer”初版允许表示，因为它可能是 seed + feedback；
但它的运行时版本语义尚未定义，所以 planner 不能仅凭结构直接执行。

## 10. 确定性序列化

确定性不只依赖 compiler。即使调用者直接以不同 tuple 顺序构造语义相同的
`WorkflowGraph`，`to_dict()` 也必须：

- 按 identity 排 steps；
- 按 identity 排 artifacts；
- 按 `(kind, endpoints...)` 排 edges；
- 按 output Artifact identity 排 selectors；
- 按 resource identity 排 resources；
- 按 predecessor Step identity 排 `depends_on`；
- 使用固定字段顺序。

这保证 hash、diff、snapshot 和缓存稳定。

第一版只提供 `to_dict()`，不在没有真实跨进程消费者前提前冻结 `from_dict()`、
schema migration 或 `schema_version`。

## 11. Foreach 静态合同

已确认并进入初版的部分：

- source 是有限、有序、允许重复的 List；
- 每个 index 产生一个 invocation-local slot；
- 相同值位于不同 index 时仍执行多次；
- item binding 属于 slot，不是新静态 Step；
- 空 List 产生零 slot；
- slots 无隐含顺序，可并行；
- 结果聚合必须按输入 index，而非完成顺序；
- 一个 Step 初版只允许一个 foreach source；
- source 在 activation 时冻结；
- 不支持 nested foreach、zip、range、笛卡尔积、break/continue 或 multi-Step body。

Graph 只保存 `ForeachEdge` 和 local binding。slot FSM、并发配额、attempt、partial
failure、结果聚合与提交由未来 runtime 负责。

## 12. 与 FusionFlow Python 执行原语的关系

旧 `flow.*` Python 移植是 imperative runtime：

- `flow.session`、`flow.call`、`flow.exec` 是可能的原子执行能力；
- `flow.parallel`、`flow.if_`、loop、map/filter 等是 Python 作者直接使用的组合器；
- 它们产生动态 `ExecutionTrace`。

未来静态图 executor 不应把所有 combinator 再套一遍：

- 图的普通依赖已决定 all-ready、fan-out 和隐式并行；
- planner 应按图决定 frontier；
- dispatcher 通过原始 Core IR/catalog 判断 `executor_id` 属于 Human、Agent 还是
  Program；
- Agent handler 可以内部调用 `flow.session`，Program handler 可以调用
  `flow.call/exec` 或其他程序适配器；
- Human handler 必须持久保存未完成任务、释放 worker，并在收到人类结果后提交
  produces Artifact；
- 图的控制语义未闭合时，不从普通数据边猜测 lazy `if`、first/any 或 while；
  `SelectNode` 只执行已显式建模的 eager 值选择。

## 13. 测试策略

### 13.1 模型测试

- 允许包含 cycle 的图；
- direct construction 的确定性 `to_dict()`；
- `Literal` edge payload；
- identity/端点/producer/foreach/positive constraints；
- local binding 不泄漏；
- input + producer 结构可保存；
- 不可变性。

### 13.2 graph compiler 测试

- 每个已知 operator 的成功映射；
- arity、owner、RHS、类型错误；
- 未知 assertion 进入 residual；
- 四个 canonical dataflow operator 的真实 `ListTerm` 显式擦除顺序、拒绝重复；
- 未支持的 term 经 `CoreIRCompiler` fail closed；
- cycle 编译后仍保留；
- resource key 不发生 `"a:b" + "c"` 与 `"a" + "b:c"` 碰撞；
- 结果排序稳定。

## 14. 非目标

本 PR 不实现：

- parser 或生成 ANTLR Python parser；
- checker 的完整语义；
- scheduler、Artifact Store、event history；
- Step activation、attempt/slot FSM；
- 可执行 feedback loop；
- Artifact version、commit、termination；
- SCC analyzer 或 deadlock 判定；
- lazy `if/else`、choice、while、after、Region；
- join-any、fallback、optional input；
- nested/multi-Step foreach；
- dynamic graph mutation；
- Petri Net/TLA+/MLIR lowering；
- executor callable registry；
- FusionFlow Python 运行时。

## 15. 验收标准

- 图模型明确允许有环，代码中没有 DAG 校验；
- 静态定义与运行状态彻底分离；
- PR12 `CoreIRCompiler` hook 边界、residual 和 List 关系映射的信息损失显式；
- 模型结构检查完整；
- 直接构造和编译构造都能确定性序列化；
- tests、Ruff、ty 通过；
- 根 AGENTS 的代码结构和设计说明同步；
- 初版 one-shot planner/executor 已独立实现，边界见
  `2026-07-25-workflow-execution-plan-design.zh.md`；本图模型仍允许保存 planner
  暂不支持的有环和 Foreach 结构。
