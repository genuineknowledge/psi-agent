# FusionFlow Python 运行时注释覆盖设计

## 目标

在不改变运行逻辑、公开接口和测试行为的前提下，为 PR #15 的 Python
FusionFlow 运行时补齐中文 docstring 和关键步骤注释，使读者能够从源码理解每个
函数的职责、状态变化与异常边界。

## 范围

覆盖以下生产文件：

- `src/psi_agent/fusion_flow/__init__.py`
- `src/psi_agent/fusion_flow/model.py`
- `src/psi_agent/fusion_flow/runtime.py`
- `src/psi_agent/fusion_flow/flow.py`

每个模块、数据类型、公开函数和私有 helper 都应有与复杂度匹配的说明：

- 简单值对象或一行 helper：一句话说明职责。
- 有状态或异步函数：说明输入输出、持久化副作用、关键不变量和主要异常。
- 取消、并发、resume/cache、binding 提交、原子写入和子进程生命周期：在非直观
  分支前增加行内注释，解释“为什么这样做”。

测试文件不逐函数补注释；测试名称和断言继续作为行为说明。

## 文件职责

### `__init__.py`

说明包级公开入口、旧 `Agent` 兼容包装器，以及它对活动 `run()` 上下文和注入
runner 的依赖。

### `model.py`

说明配置、句柄、规则、结果和 trace 数据结构的语义；在校验函数处记录名称安全、
范围约束和 token 聚合规则。

### `runtime.py`

按运行生命周期解释目录建立、路径约束、原子落盘、输入与 binding 单赋值、
resume 元数据加载、trace 持久化、取消安全清理和 run GC。

### `flow.py`

保留现有六组公开原语说明，并补齐内部 evaluator 解析、并发汇合、stream 排空与
其他 helper。只在缓存探测、binding 预留/提交、异常释放、超时和子进程清理等关键
步骤增加行内注释，避免逐行复述代码。

## 注释风格

- 使用简体中文，保留 Python/TypeScript 标识符、异常名和协议字段原文。
- docstring 描述当前实现，不承诺尚未实现的兼容能力。
- 行内注释优先解释约束和原因，不解释显而易见的赋值或分支。
- 不借补注释之机重构、修 bug、调整类型或修改默认值。

## 验证

1. 检查 diff，确认生产代码的可执行语句未改变。
2. 运行 `uv run ruff format --check .`。
3. 运行 `uv run ruff check .`。
4. 运行 `uv run ty check`。
5. 运行 `uv run pytest -q tests/psi_agent/fusion_flow`。

