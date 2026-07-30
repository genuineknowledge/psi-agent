# Workflow Execution Plan 初版：全量启动、显式 Await 与 eager Select

> 状态：初版实现
> 日期：2026-07-25

## 1. 目标

`WorkflowGraph` 保存声明式 Step–Artifact 关系、无数据传递的显式 Step 顺序约束，
但不保存运行状态。初版
`fusion_flow.workflow_execution` 把其中能够无歧义执行的 one-shot 子集编译为可检查的
`ExecutionPlan`，再通过调用方注入的 dispatcher 执行 Step。

计划不是原图的另一份依赖表。每个 Step 被编译成一个同时启动的 `Fiber`，其中包含
显式控制指令：

```text
Fiber(research): Invoke(research)
Fiber(draft):    Await(research) -> Invoke(draft)
Fiber(review):   Await(research) -> Invoke(review)
Fiber(publish):  Await(draft, review) -> Invoke(publish)
Fiber(primary):  Invoke(primary)
Fiber(fallback): Invoke(fallback)
Fiber(selected): Await(primary, fallback) -> Select(selected)
Fiber(final):    AwaitSelections(selected) -> Invoke(final)
Fiber(cleanup):  Await(final) -> Invoke(cleanup)  # 来自 depends_on，无需 Artifact 边
```

执行器不再遍历图计算 ready frontier；它只解释已经生成的 `Await`、
`AwaitSelections`、`Invoke` 与 `Select`。

## 2. 计划生成

`generate_plan(graph)` 做四件事：

1. 扫描 `ProducesEdge`，建立 `artifact_id -> producer step_id`；
2. 扫描 `ConsumesEdge`，把 producer 与 `StepNode.depends_on` 中的显式前驱合并，
   编译成 consumer fiber 中的 `Await`；
3. 为每个 `SelectNode` 建独立 fiber，等待条件及两个候选的 Step/Select producer；
   消费 Select 输出的 Step 用 `AwaitSelections` 等待；
4. 用 Kahn 算法检查 Step 和 Select 操作组成的等待图是否形成环。

主体只是两次 edge 遍历和一次标准环检测，不需要为 Python `for` 循环设计新的 DSL。
除确定性排序外，时间与空间复杂度都是 `O(steps + edges)`。

初版计划生成是确定性的：fiber、await target 都按 ID 排序。相同
`WorkflowGraph` 总是得到相同 `ExecutionPlan`。

## 3. 执行

`execute_plan()` 使用一个 anyio task group 同时启动全部 fiber：

执行前会验证每个 Step 恰好被调用一次，且其 Artifact producer 和显式
`depends_on` 前驱均已由前序 `Invoke` 或 `Await` 覆盖；额外的无环等待可用于
其他 planner 选择更保守的顺序。

1. `Await(step_ids)` 等待对应 Step 的 completion event；
2. `AwaitSelections(artifact_ids)` 等待对应 Select 输出可用；
3. `Invoke(step_id)` 收集该 Step 消费的 Artifact，调用 dispatcher，并发布声明产物；
4. `Select(output_artifact_id)` 计算条件，将被选候选的值发布为输出 Artifact；
5. 标记对应 Step 或 Select 完成，唤醒等待它的 fiber。

Select fiber 会等待条件引用和两个候选 Artifact 的 producer，因此所有候选 Step
都会执行。这是 eager 值选择；它不跳过未选 producer，也不创建控制流 Region。

任一 fiber 失败时，anyio task group 取消其余 fiber。`WorkflowPolicy` 的
`max_concurrency` 与资源 allocator 在同一个 admission 临界区中限制进入
dispatcher 的调用；workflow 和 step timeout 分别包住整次运行和单次调用。

资源容量由 runner 以正整数或具体实例 ID 列表提供。Step 获取资源时一次性检查并
保留全部 `resource_requirement`，避免部分持有后再等待另一资源；等待资源的 Step
也不会提前占用全局并发位。`DispatchContext.resource_lease` 向 contextual
dispatcher 暴露具体实例。退出 dispatch 的 success、exception、timeout 或
cancellation 路径都会在 shielded cleanup 中归还资源。当前 allocator 是进程内
固定资源池，不提供跨进程锁或 shared/exclusive 租约模式。

### 3.1 checkpoint / resume

`execute_plan()` 可接收 `ExecutionCheckpoint(workflow_id, plan_digest, values,
completed_step_ids, completed_selection_ids)`。其中 `workflow_id` 必须与当前 workflow
一致；`plan_digest` 是对规范化的图语义与显式 plan fiber 结构序列化后计算的 SHA-256，
所以仅仅复用相同的 Step / Artifact ID 不能把另一张图或旧 plan 的 checkpoint
移植过来。

checkpoint 值只接受严格、有限的 JSON 类型：`null`、字符串、布尔、整数、有限浮点数、
数组，以及字符串键对象；非有限数、非 JSON 对象和循环引用均被拒绝。恢复时递归比较
JSON 类型和值，不使用 Python 的宽松相等语义，因此 `true` 与 `1` 不相等。此外还会
验证 ID 已知且无重复、已完成操作对计划依赖闭包封闭、`values` 恰好包含 workflow
inputs 与所有已完成操作物化的 Artifact。通过验证后，执行器预先 set 已完成事件、
跳过相应 `Invoke` / `Select`，并只为未完成资源 Step 做 allocator preflight。

调用方可注入 async `checkpoint_observer`。每个 Step/Select 的输出先在执行器内提交，
observer 成功持久化完整快照后，completion event 才对依赖者可见；observer 失败会让
本次执行失败，不能发布一个未持久化的前驱。这是 one-shot DAG 操作级 checkpoint，
不是任意缓存命中、后台 worker 协议或 legacy `flow.*` run-directory resume。

核心执行器不读取 workflow 源文件；workspace 的公开 `run_flow_resume` adapter 在
此合同之外另行校验当前 `.workflow` 内容是否仍匹配创建 run 时保存的 source digest。
Human run 使用严格的 state-v2 JSON schema 保存上述 workflow/plan 身份字段，并以
OS 自动释放的 advisory file lock 加进程内 reservation guard 串行化同一个 run 的
恢复。锁文件存在本身不代表持有租约；进程异常退出时内核会释放实际 advisory lock。

## 4. 与 FusionFlow Python execution 子包的边界

`StepNode.executor_id` 只是稳定身份，不包含 Agent 配置、Human channel、argv 或
可调用对象，因此通用执行器不能仅凭图决定实际能力。调用方负责提供 dispatcher：

```python
async def dispatch(step, inputs, context):
    if step.executor_id == "writer":
        text = await complete_agent(step, inputs, context)
        return {"draft_text": text}
    if step.executor_id == "formatter":
        stdout = await run_program(step, inputs, context)
        return {"article": stdout}
    raise LookupError(step.executor_id)
```

需要具体资源实例时，contextual dispatcher 从
`DispatchContext.resource_lease` 读取 grant。计划执行器只负责并发、等待、资源
admission、输入收集、输出提交与 checkpoint；它不依赖 parser、compiler、runner，
也不会在 graph package 中复制 executor catalog。官方 G4 runner 的
Agent/Program/Human adapter 与隔离保留的 `fusion_flow.execution` 兼容层没有运行时耦合。

## 5. 初版明确拒绝

- `ForeachEdge`：需要 slot、index 稳定聚合和 partial failure 合同；
- `max_attempts != 1`：应复用 PR15 `flow.retry`，但 retry/iteration 组合尚未确定；
- input Artifact 同时有 producer：可能表示 seed + feedback，需要 Artifact version；
- producer/consumer 环：直接全量启动会形成 circular await。

例如 `A Await(B)`、`B Await(A)` 时，没有任何 fiber 能首先发布结果。`await` 是等待
机制，不会自行创造 seed、版本或退出条件，所以初版在运行前报错，不把死锁留到执行期。

## 6. 后续一般图语义

一般图继续由 `WorkflowGraph` 合法保存。只有在相应合同确定后，planner 才增加
`ForEach`、lazy `Branch`、`Loop` 等指令：

- feedback loop：seed、每轮 Artifact version、commit、termination；
- lazy branch：未选 Step 的 activation、输出和 join；
- foreach：slot identity、并发配额、按输入 index 聚合；
- retry：attempt 与 loop iteration 的组合。

这些是计划语义，不应由一个隐式 `while` 或 circular await 猜出来。
