# Workflow Execution Plan 初版：全量启动与显式 Await

> 状态：初版实现
> 日期：2026-07-25

## 1. 目标

`WorkflowGraph` 保存声明式 Step–Artifact 关系，但不保存运行状态。初版
`workflow_execution` 把其中能够无歧义执行的 one-shot 子集编译为可检查的
`ExecutionPlan`，再通过调用方注入的 dispatcher 执行 Step。

计划不是原图的另一份依赖表。每个 Step 被编译成一个同时启动的 `Fiber`，其中包含
显式控制指令：

```text
Fiber(research): Invoke(research)
Fiber(draft):    Await(research) -> Invoke(draft)
Fiber(review):   Await(research) -> Invoke(review)
Fiber(publish):  Await(draft, review) -> Invoke(publish)
```

执行器不再遍历图计算 ready frontier；它只解释已经生成的 `Await` 与 `Invoke`。

## 2. 计划生成

`generate_plan(graph)` 做三件事：

1. 扫描 `ProducesEdge`，建立 `artifact_id -> producer step_id`；
2. 扫描 `ConsumesEdge`，把 producer 编译成 consumer fiber 中的 `Await`；
3. 用 Kahn 算法检查这些 await 是否形成环。

主体只是两次 edge 遍历和一次标准环检测，不需要为 Python `for` 循环设计新的 DSL。
除确定性排序外，时间与空间复杂度都是 `O(steps + edges)`。

初版计划生成是确定性的：fiber、await target 都按 ID 排序。相同
`WorkflowGraph` 总是得到相同 `ExecutionPlan`。

## 3. 执行

`execute_plan()` 使用一个 anyio task group 同时启动全部 fiber：

执行前会验证每个 Step 恰好被调用一次，且其 producer 已由前序 `Invoke` 或
`Await` 覆盖；额外的无环等待可用于其他 planner 选择更保守的顺序。

1. `Await(step_ids)` 等待对应 Step 的 completion event；
2. `Invoke(step_id)` 收集该 Step 消费的 Artifact；
3. 调用 dispatcher；
4. 严格校验 dispatcher 返回带字符串键的 mapping，并发布该 Step 声明产生的 Artifact；
5. 标记 Step 完成，唤醒等待它的 fiber。

任一 fiber 失败时，anyio task group 取消其余 fiber。`WorkflowPolicy` 的
`max_concurrency` 限制同时进入 dispatcher 的数量；workflow 和 step timeout
分别包住整次运行和单次调用。

## 4. 与 FusionFlow Python runtime 的边界

`StepNode.executor_id` 只是稳定身份，不包含 Agent 配置、ServiceHandle、argv 或
可调用对象，因此通用执行器不能仅凭图决定调用哪个 `flow.*` 原语。调用方负责提供：

```python
async def dispatch(step, inputs):
    if step.executor_id == "writer":
        text = await flow.session(writer, str(inputs["notes"]))
        return {"draft_text": text}
    if step.executor_id == "formatter":
        result = await flow.exec("formatter", ["formatter"])
        return {"article": result.stdout}
    raise LookupError(step.executor_id)
```

dispatcher 可以调用 PR15 的 `flow.session`、`flow.call` 或 `flow.exec`；计划执行器只负责
并发、等待、输入收集和输出提交。这样不会在 graph package 中复制 executor catalog。

## 5. 初版明确拒绝

- `ForeachEdge`：需要 slot、index 稳定聚合和 partial failure 合同；
- resource requirement：需要 allocator；
- `max_attempts != 1`：应复用 PR15 `flow.retry`，但 retry/iteration 组合尚未确定；
- input Artifact 同时有 producer：可能表示 seed + feedback，需要 Artifact version；
- producer/consumer 环：直接全量启动会形成 circular await。

例如 `A Await(B)`、`B Await(A)` 时，没有任何 fiber 能首先发布结果。`await` 是等待
机制，不会自行创造 seed、版本或退出条件，所以初版在运行前报错，不把死锁留到执行期。

## 6. 后续一般图语义

一般图继续由 `WorkflowGraph` 合法保存。只有在相应合同确定后，planner 才增加
`ForEach`、`Branch`、`Loop` 等指令：

- feedback loop：seed、每轮 Artifact version、commit、termination；
- branch：predicate、未选分支输出和 join；
- foreach：slot identity、并发配额、按输入 index 聚合；
- retry：attempt 与 loop iteration 的组合。

这些是计划语义，不应由一个隐式 `while` 或 circular await 猜出来。
