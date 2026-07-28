# Workflow 图的执行计划：DAG、状态图与循环

> 日期：2026-07-25
> 适用背景：`WorkflowGraph -> ExecutionPlan -> ExecutionTrace`
> 相关设计：`2026-07-23-workflow-graph-design.zh.md`

## 1. 核心结论

图中有环不意味着无法生成执行计划。无法预先生成的是一份包含全部运行实例的有限列表。

应区分三个对象：

```text
WorkflowGraph
    静态定义：Step、Artifact、Consumes、Produces、Foreach

ExecutionPlan
    有限程序：如何激活、等待、并行、跳转和结束

ExecutionTrace
    动态轨迹：实际 activation、iteration、attempt、结果和错误
```

循环计划本身可以保持有限：

```text
draft -> review
  ^         |
  +---------+
```

实际 trace 才会展开为：

```text
draft#1 -> review#1 -> draft#2 -> review#2 -> publish#1
```

## 算法名称与术语速查

最重要的名称对应关系是：

```text
DAG 依赖执行
    = Kahn 拓扑排序的异步调度版本
    = indegree + ready queue / worklist

LangGraph 状态图执行
    = BSP（Bulk Synchronous Parallel）的分轮节奏
    + Pregel 风格的 active nodes、channels 与消息传播

一般有向图的环分析
    = SCC（Strongly Connected Components）
    = 常用 Tarjan 算法求解

编译器中的循环
    = 带 back-edge 的 CFG
    + basic block、program counter、SSA phi
```

| 名称 | 中文或常见写法 | 在这里解决什么问题 |
| --- | --- | --- |
| Topological Sort | 拓扑排序 | 为 DAG 产生满足依赖关系的顺序 |
| Kahn's Algorithm | Kahn 拓扑排序算法 | 反复取出入度为 0 的节点；异步 executor 用它维护 ready queue |
| Ready Queue / Worklist | 就绪队列 / 工作列表 | 节点完成后，立即把新满足条件的下游加入可运行集合；它是调度实现形式，不是另一套图语义 |
| Bulk Synchronous Parallel（BSP） | 批量同步并行 | 把运行分成一轮轮 super-step，每轮并行计算后统一提交更新 |
| Pregel | Pregel 图计算模型 | 在 BSP 节奏上，以 active vertex、消息或 channel 驱动下一轮计算 |
| Super-step | 超步 / 同步轮次 | `Plan -> Execute -> Update` 的一轮；轮内读取稳定快照，轮末合并更新 |
| Strongly Connected Component（SCC） | 强连通分量 | 找出图中的循环区域 |
| Tarjan's SCC Algorithm | Tarjan 强连通分量算法 | 一次深度优先搜索求 SCC，复杂度为 `O(V + E)` |
| Condensation Graph | 缩点图 | 把每个 SCC 缩成一个节点后得到 DAG，用来排列循环区域之间的先后关系 |
| Control-Flow Graph（CFG） | 控制流图 | 用分支和回边有限地表示循环程序 |
| Basic Block | 基本块 | 没有内部跳转的一段顺序指令 |
| Back-edge | 回边 | 从循环体跳回循环头 |
| Static Single Assignment（SSA）/ `phi` | 静态单赋值 / φ 节点 | 合并首次进入循环的值与上一轮回边传来的值 |
| Program Counter（PC） | 程序计数器 | 解释器记录下一条指令或下一个 block |
| Quiescence | 静止 / 无活动状态 | 没有 active node、待处理消息或更新时结束 |
| Fixed Point | 不动点 / 收敛 | 新一轮不再改变状态时结束 |

需要特别区分：Kahn 是 DAG 的拓扑算法；BSP 是并行计算模型；Pregel 是建立在 BSP 思路上的图执行模型；Tarjan 是查找环状区域的静态分析算法，并不规定环内部如何执行。

## 2. 首先判断边的语义

同样是有向边，至少有两种完全不同的含义。

### 2.1 数据依赖

```text
A produces X
B consumes X
```

含义是 A 成功提交 Artifact X 后，B 才能执行。节点通常在一次 workflow run 中执行一次。

这适合 DAG ready-queue 调度。

### 2.2 状态转移或重新激活

```text
draft -> review -> draft
```

含义是 review 的结果可能再次激活 draft。节点可以在同一次 run 中执行多次。

这需要状态图或控制流解释器，不能只使用“全部上游完成”的 DAG 规则。

如果图只说明 `A waits for B` 与 `B waits for A`，又没有初始值或上一轮值，那么并发启动两个协程仍然会死锁。环至少需要明确：

- entry 或初始 token；
- 当前值与上一轮值的版本语义；
- 重新激活条件；
- 并行更新的合并规则；
- END、收敛条件或 `max_steps`。

## 3. DAG：Kahn 拓扑调度的异步 ready-queue 版本

DAG 调度中，每个节点最多执行一次。节点所有必需输入都可用时进入 ready 集合。下面的伪代码是 Kahn 拓扑排序的异步 worklist 版本：经典算法把入度为 0 的节点依次输出，executor 则把“输出节点”替换成“启动节点”，并在节点完成后降低下游的剩余依赖数。

```python
remaining = {
    step: number_of_unfinished_producers(step)
    for step in graph.steps
}
ready = {step for step, count in remaining.items() if count == 0}

while ready or running:
    for step in ready:
        running.start(step)

    completed = await running.next_completed()

    for consumer in consumers_of(completed):
        remaining[consumer] -= 1
        if remaining[consumer] == 0:
            ready.add(consumer)

if completed_count != step_count:
    raise CycleError
```

这里不应按拓扑层设置全局 barrier。某个 producer 一完成，就应立即解锁它自己的 consumers，而不等待同层无关任务。

例如：

```text
A -> C -> E
B -> D
```

如果 A 很快而 B 很慢，C 应立即开始，不需要等待 B。

DAG 模型的合同是：

| 项目 | 语义 |
| --- | --- |
| 节点实例 | 一次 run 中至多一次 |
| 边 | 完成依赖 |
| 并行 | 所有 ready 节点可并行 |
| 结束 | 所有目标完成 |
| 环 | 非法或交给另一种执行合同 |
| 失败 | 下游按 failure policy 跳过、失败或继续 |

## 4. LangGraph：BSP 节奏下的 Pregel 状态图运行时

状态图允许同一节点被多次激活。BSP 是底层并行执行节奏，Pregel 是基于这种节奏的 vertex-centric 图计算模型。LangGraph 借用了 Pregel 的名称和 actors/channels 模型，但不是 Google Pregel 系统本身。它将每一轮分成三个阶段：

1. Plan：根据本轮输入或上一轮 channel 更新选择 active nodes；
2. Execute：所有 active nodes 读取同一个 state snapshot，并行执行；
3. Update：合并本轮更新，再计算下一轮 active nodes。

```python
state = initial_state
active = entry_nodes

for step in range(max_steps):
    if not active:
        return state

    snapshot = state
    updates = await parallel(node(snapshot) for node in active)
    state = merge(state, updates)
    active = select_next_nodes(state, updates)

raise MaxStepsExceeded
```

本轮产生的更新在 Update 前对其他节点不可见，因此实际完成顺序不会改变本轮读取的状态。代价是每个 super-step 都有同步边界。

状态图模型的合同是：

| 项目 | 语义 |
| --- | --- |
| 节点实例 | 同一节点可多次 activation |
| 边 | 可能的激活、路由或 channel 订阅 |
| 并行 | 同一 super-step 的 active nodes 并行 |
| 状态 | 共享、版本化，并有 reducer |
| 结束 | END、无 active node、收敛或 `max_steps` |
| 环 | 原生允许 |
| 恢复 | 通常在 super-step 边界 checkpoint |

### 4.1 SCC / Tarjan 能做什么

面对任意有向图，可以先用 Tarjan 算法求强连通分量：

- 单节点且没有自环的 SCC 是无环区域；
- 多节点 SCC 或带自环的单节点 SCC 是循环区域；
- 把每个 SCC 缩成一个节点后得到 condensation DAG，可以用 Kahn 算法排列各区域。

但 SCC 只回答“环在哪里”。它不能回答首次激活从哪里来、一次迭代读哪个版本、并行更新如何合并以及何时停止，因此 Tarjan 不能替代 BSP/Pregel、CFG 解释器或业务定义的循环执行合同。

## 5. 编译器和解释器如何处理循环

编译器不会无限展开循环，而是将它保留为带回边的控制流图：

```text
entry
  |
  v
loop_header <---------+
  | condition         |
  +-- false -> exit   |
  |                   |
  +-- true -> body ---+
```

LLVM IR 使用 basic blocks 和条件分支表达回边；SSA 的 `phi` 节点在首次进入值与上一轮回边值之间选择。

解释器则维护程序计数器：

```python
pc = plan.entry

while pc is not END:
    block = plan.blocks[pc]
    result = await execute(block.operations)
    pc = evaluate(block.terminator, result)
```

Python 字节码中的 `JUMP_BACKWARD` 就是这种有限程序中的回跳指令。

因此，控制流式执行计划可以写成：

```python
ExecutionPlan(
    entry="draft",
    blocks={
        "draft": Block(Invoke("draft"), Goto("review")),
        "review": Block(
            Invoke("review"),
            Branch(
                condition=Output("review", "approved"),
                if_true="publish",
                if_false="draft",
            ),
        ),
        "publish": Block(Invoke("publish"), Return()),
    },
)
```

计划有限，但 block 可以执行多次。

## 6. `await` 能做什么，不能做什么

`await` 是执行和同步原语，不会自动补全循环语义。

无环依赖可以直接表达为：

```python
research = await invoke("research")
draft, review = await parallel(
    invoke("draft", research),
    invoke("review", research),
)
await invoke("publish", draft, review)
```

但以下结构会死锁：

```python
async def run_a():
    await b_done

async def run_b():
    await a_done
```

有环时需要由外层循环提供初始状态和重新激活：

```python
state = initial_state

while True:
    state = await execute_iteration(state)
    if is_finished(state):
        break
```

所以 FusionFlow 风格的 `await` 仍可作为底层执行机制，但 planner/runtime 必须明确谁先开始、何时反馈以及何时结束。

## 7. “逻辑计划”与“物理执行计划”

下面的结构只是逻辑依赖图：

```python
PlanAction(
    step_id="publish",
    depends_on=("draft", "review"),
)
```

这里的 `PlanAction` 仍是解释逻辑图的伪代码，不是运行时类型。实际作者入口是
`StepNode.depends_on`（FusionFlow assertion 为
`depends_on(step, predecessor) == True`）；`generate_plan()` 会把它与 Artifact
producer 前驱合并，降低成物理计划里的 `Await`。

如果 executor 还要遍历依赖、计算 ready frontier，它并不是已经排好的物理执行程序。

更操作性的计划会直接包含：

- `Invoke`：调度某个 Step；
- `Await`：等待已有 activation；
- `Parallel`：并行启动一组操作；
- `Branch`：按运行结果选择下一位置；
- `Goto`：跳转到另一个 block；
- `Return`：结束；
- 状态图模式下的 channel update、reducer 和 active-node selection。

不过不应在第一版同时实现所有指令。计划 IR 应由已经闭合的执行语义驱动，而不是为未知需求预设完整虚拟机。

## 8. DAG 与状态图的选择标准

| 问题 | 选择 DAG | 选择状态图 |
| --- | --- | --- |
| 一个 Step 是否每次 run 只执行一次？ | 是 | 可能多次 |
| 边是否表示 producer 完成后 consumer 执行？ | 是 | 否 |
| 数据是否为一次提交的 Artifact？ | 是 | 主要是反复更新的 state/channel |
| 是否需要反馈、修正或 agent loop？ | 很少 | 是 |
| 是否需要 reducer 合并并行更新？ | 通常不需要 | 需要 |
| 结束是否可由目标完成判断？ | 是 | 需要 END/条件/上限 |

不要仅因为静态图“允许有环”就自动选择状态图。允许保存和分析一个环，不等于已经定义了它的执行合同。

## 9. 对 psi-agent 的分阶段建议

### 第一阶段：最小、可验证

只执行无环的普通 `ConsumesEdge` / `ProducesEdge` 子集：

- 根据 Artifact producer 建立 Step 依赖；
- 使用异步 ready-queue，完成一个节点就立即解锁其 consumers；
- 调度原子 Step 时可由注入的 dispatcher 调用
  `fusion_flow_next.execution` 的 `flow.session`、`flow.call` 或 `flow.exec`；
  核心 planner/runtime 不直接依赖该示例 Skill 子包；
- 使用 `WorkflowPolicy.max_concurrency` 限流；
- 遇到 cycle、尚未闭合的 Foreach 或不明确输入版本时 fail closed；
- trace 记录实际开始、完成、失败和取消，不把 trace 混进静态 graph。

### 第二阶段：只有真实场景要求时再增加

为反馈循环新增独立且明确的执行合同：

- entry/seed；
- activation identity 与 iteration；
- Artifact version 或 state snapshot；
- conditional transition；
- reducer/commit；
- END/收敛条件与 `max_steps`；
- checkpoint 与恢复边界。

届时可以选择：

1. CFG 风格：`Block + Branch/Goto/Return`，适合明确控制流；
2. Pregel 风格：`active set + channels + reducer + super-step`，适合并行状态传播。

不要让普通数据依赖边同时暗含控制跳转和状态反馈。

## 10. 参考资料

- [A. B. Kahn, Topological sorting of large networks（1962）](https://doi.org/10.1145/368996.369025)
- [Leslie G. Valiant, A Bridging Model for Parallel Computation（BSP，1990）](https://doi.org/10.1145/79173.79181)
- [Google Research, Pregel: A System for Large-Scale Graph Processing（2010）](https://research.google/pubs/pregel-a-system-for-large-scale-graph-processing/)
- [Robert Tarjan, Depth-First Search and Linear Graph Algorithms（1972）](https://doi.org/10.1137/0201010)
- [Apache Airflow Architecture Overview](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/overview.html)
- [Apache Airflow Scheduler](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/scheduler.html)
- [LangGraph Pregel Runtime](https://docs.langchain.com/oss/python/langgraph/pregel)
- [LangGraph Graph Recursion Limit](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)
- [Temporal Python SDK](https://github.com/temporalio/sdk-python)
- [Temporal Architecture and Event Sourcing](https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md)
- [LLVM Control Flow Tutorial](https://llvm.org/docs/tutorial/MyFirstLanguageFrontend/LangImpl05.html)
- [Python Bytecode `dis`](https://docs.python.org/3/library/dis.html)
