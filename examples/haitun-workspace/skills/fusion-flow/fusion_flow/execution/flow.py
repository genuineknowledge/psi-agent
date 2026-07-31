"""FusionFlow 的动态执行原语。

本文件保留旧 TypeScript ``flow.ts`` 的六批 API 分组。这里记录的是一次运行如何
执行与生成 trace; 声明式 WorkflowGraph、计划生成和 human/agent/program
executor 分派属于独立模块。
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from os import PathLike, environ
from typing import Any, TypeVar, cast

import anyio
from loguru import logger

from .model import (
    AgentConfig,
    AgentHandle,
    AgentInvocation,
    BlockHandle,
    ContainsRule,
    EqualsRule,
    ExecResult,
    PipelineStep,
    PredicateRule,
    RangeRule,
    RegexRule,
    ServiceHandle,
    ServiceParam,
    SessionResult,
    StaticRule,
    TokenUsage,
    _with_agent_defaults,
    assert_safe_name,
)
from .runtime import current_run_context, stable_payload_hash

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
    )
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

T = TypeVar("T")
R = TypeVar("R")

# ============================================================
# 第三批基础设施: 内建 evaluator agent + JSON 解析
# ============================================================

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
# 默认 evaluator 只供 flow.evaluate/choice 内部使用, 不注册成用户 agent。
# ponytail: SessionRunner 暂无结构化输出协议; 先用提示词约束, 再按旧 TS 规则解析和归一化。
_EVALUATOR_SYSTEM_PROMPT = """你是一个严谨的结构化判断器。

你只输出 JSON\uff0c不要任何解释、前后缀、Markdown 代码块。

根据用户给的 `kind` 字段\uff0c输出对应格式\uff1a

- kind = "boolean"\uff1a输出 {"value": true} 或 {"value": false}
- kind = "number"\uff1a输出 {"value": <number>}\uff0c必须是数字字面量
- kind = "choice"\uff1a输出 {"value": "<候选项原文>"}\uff0cvalue 必须严格等于 options 中的某一项

如果信息不足以判断\uff0c按你的最佳推测给出 value\uff0c但保持 JSON 格式。
绝对不要输出额外字段。"""

# ============================================================
# 内部注册类型与通用工具
# ============================================================


@dataclass(slots=True)
class _RegisteredService:
    """保存已注册服务的公开句柄及其异步实现。"""

    handle: ServiceHandle
    body: Callable[[dict[str, str]], Awaitable[str]]


@dataclass(slots=True)
class _RegisteredBlock:
    """保存已注册 block 的名称、说明及其异步实现。"""

    name: str
    description: str | None
    body: Callable[[dict[str, str]], Awaitable[object]]


async def _await_maybe(value: object) -> object:
    """等待 awaitable 值, 其他值原样返回。"""

    if isinstance(value, Awaitable):
        return await value
    return value


def _preview(value: object) -> str:
    """生成长度受限的 trace 摘要。"""

    text = repr(value)
    return text if len(text) <= 60 else f"{text[:57]}..."


def _normalize_string_mapping(value: Mapping[str, str] | None) -> dict[str, str]:
    """复制可选字符串映射, 并在边界处验证键和值类型。"""

    if value is None:
        return {}
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError("mapping keys and values must be strings")
        normalized[key] = item
    return normalized


def _config_payload(config: AgentConfig) -> dict[str, object]:
    """提取参与 session 缓存键计算的稳定 agent 配置字段。"""

    return {
        "name": config.name,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "thinking_budget_tokens": config.thinking_budget_tokens,
        "engine": config.engine,
        "tools": sorted(config.tools),
        "max_turns": config.max_turns,
        "context_schema": list(config.context_schema or ()),
    }


def _build_evaluate_prompt(
    *,
    question: str,
    context: Mapping[str, str],
    kind: str,
    choices: Sequence[str],
    minimum: float | None,
    maximum: float | None,
    integer: bool,
) -> str:
    """按 evaluator 类型和约束构造结构化判断提示。"""

    lines = ["# 任务", question, ""]
    if context:
        lines.append("# 上下文")
        for key, value in context.items():
            lines.extend((f"## context.{key}", value, ""))
    lines.append("# 输出格式")
    if kind == "boolean":
        lines.append('kind = "boolean"\uff0c输出 {"value": true} 或 {"value": false}。')
    elif kind == "number":
        constraints: list[str] = []
        if minimum is not None:
            constraints.append(f"min={minimum}")
        if maximum is not None:
            constraints.append(f"max={maximum}")
        if integer:
            constraints.append("必须为整数")
        suffix = "\uff08" + "\uff0c".join(constraints) + "\uff09" if constraints else ""
        lines.append(f'kind = "number"\uff0c输出 {{"value": <number>}}{suffix}。')
    else:
        lines.append('kind = "choice"\uff0c必须从下列候选项中选一个\uff1a')
        lines.extend(f"- {choice}" for choice in choices)
        lines.append('输出 {"value": "<候选项原文>"}。')
    return "\n".join(lines)


def _ensure_bool(value: object, *, label: str) -> bool:
    """确认回调返回严格的 bool, 而非依赖真值转换。"""

    if not isinstance(value, bool):
        raise TypeError(f"{label} must return bool")
    return value


def _extract_json_payload(text: str) -> object:
    """解析完整 JSON 文本, 或 Markdown JSON fence 中的完整内容。"""

    stripped = text.strip()
    if not stripped:
        raise ValueError(f"evaluate result is empty; raw={_preview(text)}")
    fenced = _JSON_FENCE.fullmatch(stripped)
    payload = fenced.group(1) if fenced is not None else stripped
    try:
        return json.loads(payload)
    except ValueError as error:
        raise ValueError(
            f"evaluate result must be valid JSON; raw={_preview(text)}",
        ) from error


def _parse_evaluate_result(
    *,
    text: str,
    kind: str,
    choices: tuple[str, ...],
    minimum: float | None,
    maximum: float | None,
    integer: bool,
) -> bool | int | float | str:
    """解析 evaluator JSON, 并按 kind 归一化和约束 value。"""

    payload = _extract_json_payload(text)
    if not isinstance(payload, dict) or "value" not in payload:
        raise ValueError(
            f"evaluate result must be a JSON object with value; raw={_preview(text)}",
        )
    value = cast("dict[str, object]", payload)["value"]
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if value in {"true", "false"}:
            return value == "true"
        raise TypeError("boolean evaluate must resolve to bool")
    if kind == "number":
        if value is None:
            number = 0.0
        elif isinstance(value, int | float) and not isinstance(value, bool):
            number = float(value)
        elif isinstance(value, str):
            try:
                number = float(value.strip()) if value.strip() else 0.0
            except ValueError as error:
                raise TypeError("number evaluate must resolve to a number") from error
        else:
            raise TypeError("number evaluate must resolve to a number")
        if not math.isfinite(number):
            raise ValueError("number evaluate must resolve to a finite number")
        if integer:
            # 先整数化, 再应用上下界, 保持旧版 evaluator 的处理顺序。
            number = math.floor(number + 0.5)
        if minimum is not None:
            number = max(number, minimum)
        if maximum is not None:
            number = min(number, maximum)
        return int(number) if integer and number.is_integer() else number
    if kind == "choice":
        if not isinstance(value, str):
            raise TypeError("choice evaluate must resolve to a string")
        text = value.strip()
        if text in choices:
            return text
        lowered = text.lower()
        # 仅接受唯一的大小写无关候选, 避免模糊匹配改变分支选择。
        matches = [choice for choice in choices if choice.lower() == lowered]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"choice {text!r} is not one of the allowed values")
    raise ValueError(f"unsupported evaluate kind: {kind}")


async def _drain_stream(
    stream: Any,
    *,
    limit: int | None,
    on_limit: Callable[[], Awaitable[None]] | None = None,
    tail: bytearray | None = None,
) -> tuple[bytes, bool]:
    """读取至 EOF, 保留上限内字节并标记是否截断。"""

    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    kept = 0
    truncated = False
    while True:
        try:
            chunk = await stream.receive()
        except anyio.EndOfStream:
            break
        if tail is not None:
            tail.extend(chunk)
            if len(tail) > 300:
                del tail[:-300]
        if limit is None:
            chunks.append(chunk)
            continue
        if kept < limit:
            remaining = limit - kept
            chunks.append(chunk[:remaining])
            kept += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True
                if on_limit is not None:
                    await on_limit()
                    on_limit = None
        elif chunk:
            truncated = True
            if on_limit is not None:
                await on_limit()
                on_limit = None
    return b"".join(chunks), truncated


async def _terminate_process(process: Any) -> None:
    """终止进程及其 Windows 子树, 并等待直接子进程回收。"""

    with anyio.CancelScope(shield=True):
        job = _take_process_job(process)
        try:
            job_terminated = bool(job is not None and sys.platform == "win32" and _kernel32.TerminateJobObject(job, 1))
            if process.returncode is None and sys.platform == "win32" and not job_terminated:
                with suppress(OSError):
                    await anyio.run_process(
                        (
                            "taskkill",
                            "/PID",
                            str(process.pid),
                            "/T",
                            "/F",
                        ),
                        check=False,
                    )
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
        finally:
            _close_process_job(job)


def _take_process_job(process: Any) -> object | None:
    """取出并清空挂在进程对象上的 Windows Job handle。"""

    job = getattr(process, "_psi_agent_job", None)
    if job is not None:
        process._psi_agent_job = None
    return job


def _close_process_job(job: object | None) -> None:
    """关闭 Windows Job handle。"""

    if job is not None and sys.platform == "win32":
        _kernel32.CloseHandle(cast("int", job))


def _attach_batch_job(process: Any) -> None:
    """给 Windows batch 进程挂一个 job, 仅供异常/超时路径显式杀树。"""

    if sys.platform != "win32":
        return
    job = _kernel32.CreateJobObjectW(None, None)
    if not job or job == _INVALID_HANDLE_VALUE:
        return
    handle = _kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        process.pid,
    )
    if not handle or handle == _INVALID_HANDLE_VALUE:
        _close_process_job(job)
        return
    try:
        if not _kernel32.AssignProcessToJobObject(job, handle):
            _close_process_job(job)
            return
    finally:
        _kernel32.CloseHandle(handle)
    process._psi_agent_job = job


async def _run_parallel_tasks[T](
    tasks: Sequence[Callable[[], Awaitable[T]]],
    *,
    join: str,
    required: int,
) -> tuple[list[T], tuple[int, ...]]:
    """并发运行任务, 按 join 策略聚合结果与选中输入索引。"""

    # 事件依次为输入索引、成功标志和结果或异常。
    send_stream, receive_stream = anyio.create_memory_object_stream[tuple[int, bool, object]](
        len(tasks),
    )

    async def worker(
        index: int,
        task: Callable[[], Awaitable[T]],
        sender: Any,
    ) -> None:
        """执行一个任务, 并将其成功值或异常发送给汇聚端。"""

        async with sender:
            try:
                value = await task()
            except Exception as error:
                await sender.send((index, False, error))
            else:
                await sender.send((index, True, value))

    results: dict[int, T] = {}
    completed: list[T] = []
    selected_indexes: list[int] = []
    failure: Exception | None = None
    async with receive_stream, anyio.create_task_group() as task_group:
        for index, task in enumerate(tasks):
            task_group.start_soon(worker, index, task, send_stream.clone())
        await send_stream.aclose()

        expected = len(tasks) if join == "all" else required
        while len(completed) < expected:
            index, ok, payload = await receive_stream.receive()
            if not ok:
                failure = cast("Exception", payload)
                task_group.cancel_scope.cancel()
                break
            value = cast("T", payload)
            if join == "all":
                results[index] = value
                completed.append(value)
            else:
                # first/any 的结果按完成顺序保留, 而不是按输入索引重排。
                selected_indexes.append(index)
                completed.append(value)
        if join != "all" or failure is not None:
            # 已满足 first/any, 或观察到失败后不再等待同组任务。
            task_group.cancel_scope.cancel()

    if failure is not None:
        raise failure
    if join == "all":
        return [results[index] for index in range(len(tasks))], ()
    return completed, tuple(selected_indexes)


# ============================================================
# FlowAPI 工厂
# ============================================================


class Flow:
    """绑定当前 ``run(...)`` 上下文的动态工作流原语。

    除 ``agent`` 外, 方法都在一次活动运行中使用。它们一边执行 Python callable,
    一边记录 trace、binding 和可恢复元数据; 它们本身不是声明式图节点。
    """

    # ============================================================
    # 第一批: 核心调用 (agent / session / service / call)
    # ============================================================

    def agent(self, config: AgentConfig) -> AgentHandle:
        """创建不可变的 agent 句柄; 此时不会调用模型或注册全局 agent。"""

        return AgentHandle(name=config.name, config=config)

    async def session(
        self,
        agent: AgentHandle,
        prompt: str,
        context: Mapping[str, str] | None = None,
        *,
        binding_name: str | None = None,
    ) -> str:
        """通过注入的 runner 执行一次 agent session, 并持久化成功结果。

        ``context_schema`` 存在时, context 的 key 必须精确匹配。恢复运行只会复用
        agent 完整配置、prompt 与 context 哈希均一致的旧 binding。
        """

        run = current_run_context()
        if run.runner is None:
            raise RuntimeError("flow.session requires an injected runner")
        normalized_context = _normalize_string_mapping(context)
        config = _with_agent_defaults(
            agent.config,
            max_tokens=8192,
            temperature=1.0,
        )
        schema = config.context_schema
        if schema:
            expected = set(schema)
            actual = set(normalized_context)
            if actual != expected:
                raise ValueError(
                    f"context keys must match exactly: expected {sorted(expected)}, got {sorted(actual)}",
                )
        cache_key = stable_payload_hash(
            {
                "operation": "session",
                "config": _config_payload(config),
                "prompt": prompt,
                "context": normalized_context,
            }
        )
        async with run._trace(
            "session",
            agent.name,
            input_summary=prompt,
            metadata={"agent": agent.name},
        ) as trace:
            reserved, call_base, call_count = await run._reserve_call_binding(
                agent.name,
                binding_name,
            )
            trace.metadata.update(
                {
                    "binding_name": reserved,
                    "trace_file": f"trace/{reserved}.json",
                }
            )
            # 名称和序号是一笔事务: 缓存命中或结果完整落盘才提交;
            # runner 失败则释放预留, 让同一逻辑调用的重试继续使用原序号。
            try:
                cached = (
                    run._resume_lookup(
                        reserved,
                        cache_key=cache_key,
                        operation="session",
                    )
                    if run.resumed
                    else None
                )
                if cached is not None:
                    trace.cached = True
                    trace.output_summary = cached
                    await run._commit_reserved_call(
                        reserved,
                        call_base,
                        call_count,
                        call_owner=agent.name,
                    )
                    return cached

                raw = await run.runner(
                    config,
                    AgentInvocation(prompt=prompt, context=normalized_context or None),
                )
                result = raw if isinstance(raw, SessionResult) else SessionResult(text=raw)
                trace.tokens = TokenUsage(
                    calls=1,
                    input=result.input_tokens,
                    output=result.output_tokens,
                )
                trace.output_summary = result.text
                metadata = run._binding_metadata(
                    reserved,
                    produced_by=agent.name,
                    tokens={
                        "input": result.input_tokens,
                        "output": result.output_tokens,
                    },
                    operation="session",
                    agent=agent.name,
                    cache_key=cache_key,
                    input_hash=stable_payload_hash(
                        {"prompt": prompt, "context": normalized_context},
                    ),
                )
                await run._commit_reserved_binding(
                    reserved,
                    result.text,
                    metadata=metadata,
                    call_base=call_base,
                    call_count=call_count,
                    call_owner=agent.name,
                )
            except BaseException:
                await run._release_binding(
                    reserved,
                    call_base=call_base,
                    call_count=call_count,
                )
                raise
        # 诊断 trace 仅在业务结果已成功提交后落盘。
        await run._write_trace_file(reserved, trace)
        return result.text

    def service(
        self,
        name: str,
        body: Callable[[dict[str, str]], Awaitable[str]],
        *,
        params: Sequence[ServiceParam] = (),
        description: str | None = None,
    ) -> ServiceHandle:
        """在当前运行中注册一个命名异步服务并返回句柄, 不立即执行服务体。"""

        run = current_run_context()
        declared_params = tuple(params)
        param_names = [param.name for param in declared_params]
        if len(set(param_names)) != len(param_names):
            raise ValueError("duplicate service parameter names are not allowed")
        handle = ServiceHandle(
            name=name,
            params=declared_params,
            description=description,
        )
        registered = _RegisteredService(handle=handle, body=body)
        normalized = run._register(
            run.services,
            handle.name,
            registered,
            kind="service",
        )
        return ServiceHandle(
            name=normalized,
            params=handle.params,
            description=description,
        )

    async def call(
        self,
        service: ServiceHandle,
        args: Mapping[str, str] | None = None,
        *,
        binding_name: str | None = None,
    ) -> str:
        """校验参数并调用已注册服务, 然后持久化字符串结果。

        恢复身份只包含 service 名称和参数, 不包含服务体代码; 同名服务实现发生变化
        时, 旧结果仍可能被复用, 这是从 TS 版本保留下来的兼容语义。
        """

        run = current_run_context()
        normalized_args = _normalize_string_mapping(args)
        registered = run.services.get(service.name)
        if not isinstance(registered, _RegisteredService):
            raise ValueError(f'service "{service.name}" is not defined')

        declared = {param.name: param for param in registered.handle.params}
        for name, param in declared.items():
            if param.required and name not in normalized_args:
                raise ValueError(f'missing required argument "{name}"')
        if declared:
            unknown = set(normalized_args) - set(declared)
            if unknown:
                raise ValueError(f"unknown arguments: {sorted(unknown)}")

        cache_key = stable_payload_hash(
            {
                "operation": "call",
                "service": service.name,
                "args": normalized_args,
            }
        )
        async with run._trace(
            "call",
            service.name,
            input_summary=_preview(normalized_args),
        ) as trace:
            reserved, call_base, call_count = await run._reserve_call_binding(
                service.name,
                binding_name,
            )
            trace.metadata.update(
                {
                    "service": service.name,
                    "args": dict(normalized_args),
                    "binding_name": reserved,
                }
            )
            try:
                cached = (
                    run._resume_lookup(
                        reserved,
                        cache_key=cache_key,
                        operation="call",
                    )
                    if run.resumed
                    else None
                )
                if cached is not None:
                    trace.cached = True
                    trace.output_summary = cached
                    await run._commit_reserved_call(
                        reserved,
                        call_base,
                        call_count,
                        call_owner=service.name,
                        service_call=True,
                    )
                    return cached

                result = await registered.body(dict(normalized_args))
                if not isinstance(result, str):
                    raise TypeError("service body must return a string")
                trace.output_summary = result
                await run._commit_reserved_binding(
                    reserved,
                    result,
                    metadata=run._binding_metadata(
                        reserved,
                        produced_by=service.name,
                        operation="call",
                        service=service.name,
                        cache_key=cache_key,
                    ),
                    call_base=call_base,
                    call_count=call_count,
                    call_owner=service.name,
                    service_call=True,
                )
                return result
            except BaseException:
                await run._release_binding(
                    reserved,
                    call_base=call_base,
                    call_count=call_count,
                )
                raise

    # ============================================================
    # 第二批: 控制流 (parallel / if_ / if_else / for_each / parallel_for_each)
    # ============================================================

    async def parallel(
        self,
        tasks: Sequence[Callable[[], Awaitable[T]]],
        *,
        join: str = "all",
        any_count: int | None = None,
    ) -> list[T]:
        """并发执行零参数异步任务, 并按 join 策略汇合。

        ``all`` 等待全部并按输入顺序返回; ``first``/``any`` 按完成顺序选取结果,
        达到数量后取消其余任务。任一已观察到的失败也会取消同组剩余任务。
        """

        required = 0
        if join == "all":
            required = len(tasks)
        elif join == "first":
            if not tasks:
                raise ValueError('parallel(join="first") requires at least one task')
            required = 1
        elif join == "any":
            if not tasks:
                raise ValueError('parallel(join="any") requires at least one task')
            if isinstance(any_count, bool) or not isinstance(any_count, int):
                raise TypeError("any_count must be an integer")
            if any_count < 1 or any_count > len(tasks):
                raise ValueError("any_count must satisfy 1 <= any_count <= len(tasks)")
            required = any_count
        else:
            raise ValueError(f"unsupported join mode: {join}")

        run = current_run_context()
        async with run._trace(
            "parallel",
            join,
            metadata={"task_count": len(tasks), "join": join, "any_count": any_count},
        ) as trace:
            results, selected_indexes = await _run_parallel_tasks(
                tasks,
                join=join,
                required=required,
            )
            if selected_indexes:
                trace.metadata["selected_indexes"] = list(selected_indexes)
                if join == "first":
                    trace.metadata["selected_index"] = selected_indexes[0]
            return results

    async def if_(
        self,
        condition: bool,
        then_fn: Callable[[], Awaitable[T]],
        else_fn: Callable[[], Awaitable[T]] | None = None,
    ) -> T | None:
        """按已经计算好的严格 bool 条件, 只执行 then 或 else 中的一个分支。"""

        if not isinstance(condition, bool):
            raise TypeError("condition must be bool")
        run = current_run_context()
        async with run._trace(
            "if",
            "if",
            metadata={"condition": condition},
        ) as trace:
            if condition:
                trace.metadata["selected_index"] = 0
                async with run._trace("ifBranch", "then") as branch:
                    value = await then_fn()
                    branch.output_summary = _preview(value)
                    return value
            if else_fn is not None:
                trace.metadata["selected_index"] = 1
                async with run._trace("ifBranch", "else") as branch:
                    value = await else_fn()
                    branch.output_summary = _preview(value)
                    return value
            trace.metadata["selected_index"] = None
            return None

    async def if_else(
        self,
        branches: Sequence[tuple[bool, Callable[[], Awaitable[T]]]],
        else_fn: Callable[[], Awaitable[T]] | None = None,
    ) -> T | None:
        """依次选择第一个条件为真的分支; 均不命中时可执行 else。"""

        for index, (condition, _) in enumerate(branches):
            if not isinstance(condition, bool):
                raise TypeError(f"branch {index} condition must be bool")
        run = current_run_context()
        async with run._trace("if", "ifElse") as trace:
            for index, (condition, fn) in enumerate(branches):
                if not condition:
                    continue
                trace.metadata["selected_index"] = index
                async with run._trace("ifBranch", f"branch-{index}") as branch:
                    value = await fn()
                    branch.output_summary = _preview(value)
                    return value
            if else_fn is not None:
                trace.metadata["selected_index"] = len(branches)
                async with run._trace("ifBranch", "else") as branch:
                    value = await else_fn()
                    branch.output_summary = _preview(value)
                    return value
            trace.metadata["selected_index"] = None
            return None

    async def for_each(
        self,
        items: Sequence[T],
        fn: Callable[[T, int], Awaitable[object]],
    ) -> None:
        """按输入顺序逐项执行, 向回调传入元素与从 0 开始的索引。"""

        run = current_run_context()
        async with run._trace("forEach", "forEach", metadata={"parallel": False}) as trace:
            trace.metadata["item_count"] = len(items)
            for index, item in enumerate(items):
                async with run._trace(
                    "iteration",
                    str(index),
                    input_summary=_preview(item),
                    metadata={"index": index},
                ):
                    await fn(item, index)

    async def parallel_for_each(
        self,
        items: Sequence[T],
        fn: Callable[[T, int], Awaitable[object]],
    ) -> None:
        """并发处理所有元素并等待全部完成; 各回调的完成顺序不保证。"""

        run = current_run_context()
        async with run._trace(
            "forEach",
            "parallelForEach",
            metadata={"parallel": True, "item_count": len(items)},
        ):
            tasks: list[Callable[[], Awaitable[object]]] = []
            for index, item in enumerate(items):

                async def visit(
                    item: T = item,
                    index: int = index,
                ) -> object:
                    """为一个并发元素记录 iteration trace 并调用回调。"""

                    async with run._trace(
                        "iteration",
                        str(index),
                        input_summary=_preview(item),
                        metadata={"index": index},
                    ):
                        return await fn(item, index)

                tasks.append(visit)
            await _run_parallel_tasks(tasks, join="all", required=len(tasks))

    # ============================================================
    # 第三批: 带 LLM 判断的高级控制流
    # (evaluate / loop_until / loop_while / choice)
    # ============================================================

    async def evaluate(
        self,
        *,
        question: str,
        kind: str,
        agent: AgentHandle | None = None,
        context: Mapping[str, str] | None = None,
        choices: Sequence[str] = (),
        minimum: float | None = None,
        maximum: float | None = None,
        integer: bool = False,
        binding_name: str | None = None,
    ) -> bool | int | float | str:
        """让默认或指定 evaluator 判断 boolean、number 或 choice。

        默认 evaluator 通过系统提示词要求 ``{"value": ...}``; 当前 runner 协议没有
        provider 级 JSON Schema 通道, 因此仍由本地解析器按旧 TS 兼容规则校验、
        取整和范围截断。结果会写入 binding, 但不会作为 resume 缓存直接复用。
        """

        if kind not in {"boolean", "number", "choice"}:
            raise ValueError(f"unsupported evaluate kind: {kind}")
        if kind == "choice":
            if not choices:
                raise ValueError("choice evaluate requires non-empty choices")
            if any(not isinstance(choice, str) for choice in choices):
                raise TypeError("choice evaluate choices must be strings")
            if len({choice.lower() for choice in choices}) != len(tuple(choices)):
                raise ValueError("choice evaluate choices must be unique")
        for name, bound in (("minimum", minimum), ("maximum", maximum)):
            if bound is not None and (isinstance(bound, bool) or not isinstance(bound, int | float)):
                raise TypeError(f"{name} must be a number or None")
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"{name} must be finite")
        if not isinstance(integer, bool):
            raise TypeError("integer must be a bool")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum must be <= maximum")

        run = current_run_context()
        if run.runner is None:
            raise RuntimeError("flow.evaluate requires an injected runner")
        normalized_context = _normalize_string_mapping(context)
        evaluator = agent or self.agent(
            AgentConfig(
                name="__evaluator__",
                system_prompt=_EVALUATOR_SYSTEM_PROMPT,
                max_tokens=256,
                temperature=0,
            )
        )
        evaluator_config = replace(
            _with_agent_defaults(
                evaluator.config,
                max_tokens=256,
                temperature=0,
            ),
            thinking_budget_tokens=None,
            tools=(),
            max_turns=None,
        )
        prompt = _build_evaluate_prompt(
            question=question,
            context=normalized_context,
            kind=kind,
            choices=choices,
            minimum=minimum,
            maximum=maximum,
            integer=integer,
        )
        # 提示, 解析和 binding 提交严格串行, 避免持久化未经约束的模型文本。
        reserved, call_base, call_count = await run._reserve_call_binding(
            f"evaluate.{evaluator.name}",
            binding_name,
            ordinal_base=evaluator.name,
        )
        try:
            async with run._trace(
                "evaluate",
                kind,
                input_summary=question,
                metadata={
                    "kind": kind,
                    "question": question,
                    "evaluator": evaluator.name,
                    "evaluator_agent": evaluator.name,
                    "options": list(choices),
                    "minimum": minimum,
                    "maximum": maximum,
                    "integer": integer,
                    "binding_name": reserved,
                    "trace_file": f"trace/{reserved}.json",
                },
            ) as trace:
                raw_result = await run.runner(
                    evaluator_config,
                    AgentInvocation(
                        prompt=prompt,
                        context=normalized_context or None,
                    ),
                )
                session_result = raw_result if isinstance(raw_result, SessionResult) else SessionResult(text=raw_result)
                trace.tokens = TokenUsage(
                    calls=1,
                    input=session_result.input_tokens,
                    output=session_result.output_tokens,
                )
                trace.metadata["raw_answer"] = session_result.text
                parsed = _parse_evaluate_result(
                    text=session_result.text,
                    kind=kind,
                    choices=tuple(choices),
                    minimum=minimum,
                    maximum=maximum,
                    integer=integer,
                )
                payload = json.dumps(
                    {"value": parsed},
                    ensure_ascii=False,
                    indent=2,
                )
                trace.output_summary = payload
                await run._commit_reserved_binding(
                    reserved,
                    payload,
                    metadata=run._binding_metadata(
                        reserved,
                        produced_by=evaluator.name,
                        tokens={
                            "input": session_result.input_tokens,
                            "output": session_result.output_tokens,
                        },
                        operation="evaluate",
                        kind=kind,
                        evaluator=evaluator.name,
                        question=question,
                    ),
                    call_base=call_base,
                    call_count=call_count,
                    call_owner=evaluator.name,
                )
        except BaseException:
            await run._release_binding(
                reserved,
                call_base=call_base,
                call_count=call_count,
            )
            raise
        await run._write_trace_file(reserved, trace)
        return parsed

    async def loop_until(
        self,
        condition: Callable[[], Awaitable[bool] | bool],
        fn: Callable[[int], Awaitable[object]],
        *,
        max_iterations: int = 8,
    ) -> None:
        """先执行循环体、再判断退出条件, 最多执行 ``max_iterations`` 次。

        条件必须返回真正的 bool。达到上限时记录 warning 后正常返回, 不抛异常。
        """

        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        run = current_run_context()
        async with run._trace(
            "loop",
            "loopUntil",
            metadata={
                "loop_kind": "until",
                "iterations": 0,
                "max_iterations": max_iterations,
                "hit_max_iterations": False,
            },
        ) as trace:
            iterations = 0
            while iterations < max_iterations:
                async with run._trace(
                    "iteration",
                    f"round-{iterations}",
                    metadata={"index": iterations},
                ):
                    await fn(iterations)
                iterations += 1
                trace.metadata["iterations"] = iterations
                if _ensure_bool(await _await_maybe(condition()), label="condition"):
                    return
            trace.metadata["hit_max_iterations"] = True
            logger.warning(
                f"FusionFlow loop_until reached max_iterations={max_iterations}",
            )

    async def loop_while(
        self,
        condition: Callable[[], Awaitable[bool] | bool],
        fn: Callable[[int], Awaitable[object]],
        *,
        max_iterations: int = 8,
    ) -> None:
        """每轮先判断条件、为真才执行循环体, 最多执行 ``max_iterations`` 次。

        条件必须返回真正的 bool。达到上限时记录 warning 后正常返回, 不抛异常。
        """

        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        run = current_run_context()
        async with run._trace(
            "loop",
            "loopWhile",
            metadata={
                "loop_kind": "while",
                "iterations": 0,
                "max_iterations": max_iterations,
                "hit_max_iterations": False,
            },
        ) as trace:
            iterations = 0
            while iterations < max_iterations:
                if not _ensure_bool(await _await_maybe(condition()), label="condition"):
                    return
                async with run._trace(
                    "iteration",
                    f"round-{iterations}",
                    metadata={"index": iterations},
                ):
                    await fn(iterations)
                iterations += 1
                trace.metadata["iterations"] = iterations
            trace.metadata["hit_max_iterations"] = True
            logger.warning(
                f"FusionFlow loop_while reached max_iterations={max_iterations}",
            )

    async def choice(
        self,
        *,
        question: str,
        branches: Sequence[tuple[str, Callable[[], Awaitable[T]]]],
        agent: AgentHandle | None = None,
        context: Mapping[str, str] | None = None,
        default_label: str | None = None,
        binding_name: str | None = None,
    ) -> T:
        """先用 evaluator 选择标签, 再只执行对应分支。

        为兼容旧 TS, ``default_label`` 会兜底 evaluate 阶段的任意普通异常 (包括
        runner 或解析失败), 但不会兜底被选中分支自身的异常, 也不会吞掉取消。
        """

        labels = [label for label, _ in branches]
        if not labels:
            raise ValueError("choice requires at least one branch")
        if len({label.lower() for label in labels}) != len(labels):
            raise ValueError("choice labels must be unique")
        if default_label is not None and default_label not in labels:
            raise ValueError("default_label must name an existing branch")

        run = current_run_context()
        async with run._trace(
            "choice",
            "choice",
            metadata={
                "question": question,
                "options": labels,
            },
        ) as trace:
            try:
                selected = await self.evaluate(
                    question=question,
                    kind="choice",
                    agent=agent,
                    context=context,
                    choices=tuple(labels),
                    binding_name=binding_name,
                )
            except Exception as error:
                if default_label is None:
                    raise
                logger.warning(
                    f"FusionFlow choice evaluation failed; using default {default_label!r}: {error}",
                )
                selected = default_label

            for index, (label, fn) in enumerate(branches):
                if label != selected:
                    continue
                trace.metadata["selected_index"] = index
                trace.metadata["chosen_index"] = index
                trace.metadata["chosen_label"] = label
                async with run._trace("choiceBranch", label) as branch:
                    value = await fn()
                    branch.output_summary = _preview(value)
                    return value
        raise ValueError(f"selected choice {selected!r} does not exist")

    # ============================================================
    # 第四批: 数据流原语 (map / pmap / filter / pfilter / reduce / pipeline)
    # ============================================================

    async def map(
        self,
        items: Sequence[T],
        fn: Callable[[T, int], Awaitable[R]],
    ) -> list[R]:
        """按输入顺序串行映射元素, 并向回调传入从 0 开始的索引。"""

        results: list[R] = []

        async def run_one(item: T, index: int) -> None:
            """映射一个元素并按串行执行顺序追加结果。"""

            results.append(await fn(item, index))

        await self.for_each(items, run_one)
        return results

    async def pmap(
        self,
        items: Sequence[T],
        fn: Callable[[T, int], Awaitable[R]],
    ) -> list[R]:
        """并发映射元素, 但按原输入顺序重排并返回结果。"""

        item_count = len(items)
        results: dict[int, R] = {}

        async def run_one(item: T, index: int) -> None:
            """映射一个元素并按输入索引暂存结果。"""

            results[index] = await fn(item, index)

        await self.parallel_for_each(items, run_one)
        return [results[index] for index in range(item_count)]

    async def filter(
        self,
        items: Sequence[T],
        predicate: Callable[[T, int], Awaitable[bool]],
    ) -> list[T]:
        """串行计算 predicate, 并保持被保留元素的输入顺序。"""

        flags: list[bool] = []

        async def decide(item: T, index: int) -> None:
            """判定一个元素是否保留。"""

            flags.append(_ensure_bool(await predicate(item, index), label="predicate"))

        await self.for_each(items, decide)
        return [item for item, keep in zip(items, flags, strict=False) if keep]

    async def pfilter(
        self,
        items: Sequence[T],
        predicate: Callable[[T, int], Awaitable[bool]],
    ) -> list[T]:
        """并发计算 predicate, 同时保持被保留元素的输入顺序。"""

        flags = await self.pmap(items, predicate)
        return [item for item, keep in zip(items, flags, strict=False) if _ensure_bool(keep, label="predicate")]

    async def reduce(
        self,
        items: Sequence[T],
        fn: Callable[[R, T, int], Awaitable[R]],
        initial: R,
    ) -> R:
        """从 ``initial`` 开始, 按顺序把元素折叠进累加值。"""

        value = initial

        async def accumulate(item: T, index: int) -> None:
            """把一个元素折叠进当前累加值。"""

            nonlocal value
            value = await fn(value, item, index)

        await self.for_each(items, accumulate)
        return value

    async def pipeline(
        self,
        value: T,
        steps: Sequence[PipelineStep],
    ) -> object:
        """让值依次经过带标签的 ``PipelineStep``, 并记录每一步的输入输出 trace。"""

        run = current_run_context()
        current: object = value
        async with run._trace(
            "pipeline",
            "pipeline",
            metadata={"step_count": len(steps)},
        ) as trace:
            for index, step in enumerate(steps):
                if not isinstance(step, PipelineStep):
                    raise TypeError("pipeline steps must be PipelineStep instances")
                label = step.label if step.label is not None else str(index)
                async with run._trace(
                    "pipelineStep",
                    label,
                    input_summary=_preview(current),
                    metadata={"index": index, "label": step.label},
                ) as branch:
                    current = await step.fn(current)
                    branch.output_summary = _preview(current)
            trace.output_summary = _preview(current)
            return current

    # ============================================================
    # 第五批: 工程化 (retry / evaluate_static / use)
    # ============================================================

    async def retry(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        max_attempts: int = 3,
        initial_delay: float = 0.2,
        backoff_factor: float = 2.0,
        max_delay: float = 8.0,
        should_retry: Callable[[Exception, int], Awaitable[bool] | bool] | None = None,
    ) -> T:
        """把一个工作流操作作为整体重试, 而不是给某个原语增加 retry 参数。

        ``operation`` 必须是可重复调用的零参数异步函数; ``max_attempts`` 包含首次
        执行。失败后按秒等待并指数退避, 等待时间始终不超过 ``max_delay``。
        ``should_retry(error, attempt)`` 可按异常和从 1 开始的失败次数提前终止。

        例如: ``await flow.retry(lambda: flow.session(agent, prompt))``。不要传
        ``flow.session(...)`` 已创建出的单次 coroutine, 因为重试时无法再次调用它。
        """

        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for name, value in (
            ("initial_delay", initial_delay),
            ("backoff_factor", backoff_factor),
            ("max_delay", max_delay),
        ):
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if initial_delay < 0 or max_delay < 0:
            raise ValueError("retry delays must be non-negative")
        if backoff_factor <= 0:
            raise ValueError("backoff_factor must be positive")
        run = current_run_context()
        async with run._trace(
            "retry",
            "retry",
            metadata={
                "max_attempts": max_attempts,
                "attempts": 0,
                "succeeded": False,
                "error_trail": [],
            },
        ) as trace:
            attempts = 0
            while True:
                attempts += 1
                trace.metadata["attempts"] = attempts
                try:
                    value = await operation()
                # AnyIO 取消异常继承 BaseException; 不能把取消误当成可重试失败。
                except Exception as error:
                    error_trail = cast("list[str]", trace.metadata["error_trail"])
                    error_trail.append(f"attempt {attempts}: {error}")
                    retryable = True
                    if should_retry is not None:
                        retryable = _ensure_bool(
                            await _await_maybe(should_retry(error, attempts)),
                            label="should_retry",
                        )
                    if attempts >= max_attempts or not retryable:
                        raise
                    logger.warning(
                        f"FusionFlow retry attempt {attempts}/{max_attempts} failed: {error}",
                    )
                    delay = min(
                        initial_delay * backoff_factor ** (attempts - 1),
                        max_delay,
                    )
                    await anyio.sleep(delay)
                else:
                    trace.metadata["succeeded"] = True
                    trace.output_summary = _preview(value)
                    return value

    async def evaluate_static(
        self,
        *,
        question: str,
        rule: StaticRule,
        binding_name: str | None = None,
    ) -> bool:
        """不调用 LLM, 按一种显式静态规则判断并持久化 JSON 结果。"""

        run = current_run_context()
        if not isinstance(
            rule,
            RegexRule | ContainsRule | EqualsRule | RangeRule | PredicateRule,
        ):
            raise TypeError("rule must be a StaticRule")
        reserved, call_base, call_count = await run._reserve_call_binding(
            "evaluate.static",
            binding_name,
            ordinal_base="__static__",
        )
        try:
            async with run._trace(
                "evaluate",
                "static",
                input_summary=question,
                metadata={
                    "kind": "static",
                    "question": question,
                    "static_rule": rule.kind,
                    "binding_name": reserved,
                    "evaluator_agent": "__static__",
                },
            ) as trace:
                if isinstance(rule, RegexRule):
                    result = re.search(rule.pattern, rule.on) is not None
                elif isinstance(rule, ContainsRule):
                    result = rule.needle in rule.on
                elif isinstance(rule, EqualsRule):
                    result = rule.on == rule.expected
                elif isinstance(rule, RangeRule):
                    result = True
                    if rule.minimum is not None:
                        result = result and rule.value >= rule.minimum
                    if rule.maximum is not None:
                        result = result and rule.value <= rule.maximum
                else:
                    result = _ensure_bool(
                        await _await_maybe(rule.fn()),
                        label="predicate",
                    )

                payload = json.dumps(
                    {"value": result, "rule": rule.kind},
                    ensure_ascii=False,
                    indent=2,
                )
                trace.output_summary = payload
                await run._commit_reserved_binding(
                    reserved,
                    payload,
                    metadata=run._binding_metadata(
                        reserved,
                        produced_by="__static__",
                        operation="evaluate_static",
                        question=question,
                        static_rule=rule.kind,
                    ),
                    call_base=call_base,
                    call_count=call_count,
                    call_owner="__static__",
                )
                return result
        except BaseException:
            await run._release_binding(
                reserved,
                call_base=call_base,
                call_count=call_count,
            )
            raise

    async def use(
        self,
        service_name: str,
        args: Mapping[str, str] | None = None,
        *,
        binding_name: str | None = None,
    ) -> str:
        """按名称调用已注册服务, 是构造 ``ServiceHandle`` 再调用 ``call`` 的便捷写法。"""

        return await self.call(
            ServiceHandle(name=assert_safe_name(service_name)),
            args,
            binding_name=binding_name,
        )

    # ============================================================
    # 第六批: 顶层结构与外部执行
    # (block / define_block / run_block / repeat / input / output / exec)
    # ============================================================

    async def block(
        self,
        label: str,
        fn: Callable[[], Awaitable[T]],
    ) -> T:
        """立即执行一个内联分组, 并用 ``label`` 把其子 trace 包在 block 节点下。"""

        run = current_run_context()
        async with run._trace(
            "block",
            label,
            metadata={"is_defined": False},
        ) as trace:
            value = await fn()
            trace.output_summary = _preview(value)
            return value

    def define_block(
        self,
        name: str,
        body: Callable[[dict[str, str]], Awaitable[object]],
        *,
        description: str | None = None,
    ) -> BlockHandle:
        """在当前运行中注册可复用 block 并返回句柄, 不立即执行其 body。"""

        run = current_run_context()
        block = _RegisteredBlock(name=name, description=description, body=body)
        normalized = run._register(run.blocks, name, block, kind="block")
        return BlockHandle(name=normalized, description=description)

    async def run_block(
        self,
        block: BlockHandle | str,
        args: Mapping[str, str] | None = None,
    ) -> object:
        """执行已注册 block, 并把全部字符串参数作为一个 dict 传给 body。"""

        run = current_run_context()
        name = block.name if isinstance(block, BlockHandle) else assert_safe_name(block)
        registered = run.blocks.get(name)
        if not isinstance(registered, _RegisteredBlock):
            raise ValueError(f'block "{name}" is not defined')
        values = _normalize_string_mapping(args)
        async with run._trace(
            "block",
            name,
            input_summary=_preview(values),
            metadata={"is_defined": True, "args": values},
        ) as trace:
            result = await registered.body(values)
            trace.output_summary = _preview(result)
            return result

    async def repeat(
        self,
        times: int,
        fn: Callable[[int], Awaitable[object]],
    ) -> None:
        """按顺序精确执行 ``times`` 次, 向回调传入从 0 开始的轮次。"""

        if isinstance(times, bool) or not isinstance(times, int) or times < 0:
            raise ValueError("times must be a non-negative integer")
        await self.for_each(range(times), lambda item, index: fn(item))

    async def input(self, name: str, default_value: str) -> str:
        """读取运行注入值或默认值, 并把最终输入持久化为 binding。"""

        return await current_run_context().input(name, default_value)

    async def output(self, name: str, value: str) -> None:
        """把字符串结果保存为指定 binding; 同一名称遵守单赋值约束。"""

        await current_run_context().save(name, value)

    async def exec(
        self,
        name: str,
        argv: Sequence[str],
        *,
        stdin: str | bytes | None = None,
        cwd: str | PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
        output_limit: int | float = 4 * 1024 * 1024,
        binding_name: str | None = None,
    ) -> ExecResult:
        """直接执行 argv, 成功后持久化 stdout。

        stdout/stderr 和 stdin 从进程启动起并发处理。有限 ``output_limit`` 只约束
        stdout: 一旦越界立即杀进程, 返回保留的前缀并在 binding 中追加截断标记;
        ``0`` 或正无穷关闭上限。超时或外部取消也会杀进程并等待回收。
        Windows 上显式的 ``.cmd``/``.bat`` 目标经转义后交给系统 shell。
        """

        normalized_name = assert_safe_name(name)
        if isinstance(argv, str | bytes):
            raise TypeError("argv must be a sequence of strings, not str or bytes")
        if not argv:
            raise ValueError("argv must not be empty")
        if any(not isinstance(item, str) for item in argv):
            raise TypeError("argv items must be strings")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if output_limit == math.inf:
            stdout_limit = None
        elif isinstance(output_limit, int) and not isinstance(output_limit, bool) and output_limit >= 0:
            stdout_limit = output_limit or None
        else:
            raise ValueError(
                "output_limit must be a non-negative integer or positive infinity",
            )

        run = current_run_context()
        command = list(argv)
        command_preview = " ".join(command)[:200]
        process_command: str | list[str] = command
        internal_env: dict[str, str] = {}
        windows_batch = sys.platform == "win32" and command[0].casefold().endswith((".cmd", ".bat"))
        if windows_batch:
            if any('"' in argument or "!" in argument or "\r" in argument or "\n" in argument for argument in command):
                raise ValueError(
                    'Windows batch argv cannot contain double quotes ("), exclamation marks (!), or line breaks',
                )
            percent_variable = "PSI_AGENT_EXEC_LITERAL_PERCENT"
            internal_env[percent_variable] = "%"
            percent_reference = f"%{percent_variable}%"
            process_command = " ".join(f'"{argument.replace("%", percent_reference)}"' for argument in command)
        merged_env = None
        if env is not None or internal_env:
            merged_env = {
                **environ,
                **_normalize_string_mapping(env),
                **internal_env,
            }
        reserved, call_base, call_count = await run._reserve_call_binding(
            normalized_name,
            binding_name,
        )
        process: Any = None
        try:
            async with run._trace(
                "exec",
                normalized_name,
                metadata={
                    "name": normalized_name,
                    "command": command_preview,
                    "binding_name": reserved,
                },
            ) as trace:
                started = time.perf_counter()
                process = await anyio.open_process(
                    process_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=merged_env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
                )
                if windows_batch:
                    _attach_batch_job(process)
                payload = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
                stderr_tail = bytearray()
                # 计时和两条输出 pipe 的消费必须先于 stdin 发送, 否则双方同时
                # 写满 pipe 时会互相等待, 而超时计时器也永远启动不了。
                with anyio.move_on_after(timeout_seconds) as scope:
                    stdout_bytes, stdout_truncated, stderr_bytes, return_code = await _read_process_streams(
                        process,
                        stdin_payload=payload,
                        output_limit=stdout_limit,
                        stderr_tail=stderr_tail,
                    )
                if scope.cancel_called:
                    detail = stderr_tail.decode("utf-8", errors="replace").strip()
                    raise TimeoutError(
                        f"process timed out after {timeout_seconds}s; stderr tail: {detail}",
                    )
                raw = stdout_bytes.decode("utf-8", errors="replace")
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                result = ExecResult(
                    stdout=raw.rstrip("\r\n"),
                    raw=raw,
                    stderr=stderr_text,
                    exit_code=return_code,
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    truncated=stdout_truncated,
                )
                trace.metadata["exit_code"] = result.exit_code
                trace.metadata["truncated"] = result.truncated
                # stdout 越界产生的非零退出码来自本运行时主动 kill, 属于携带
                # 部分结果的成功; 其他非零退出仍按执行失败处理。
                if result.exit_code != 0 and not result.truncated:
                    output_tail = (result.stderr or result.stdout)[-300:]
                    raise RuntimeError(
                        f"command exited with code {result.exit_code}: {output_tail}",
                    )
                truncation_note = (
                    f"\n\n... [truncated at {output_limit} bytes by "
                    "flow.exec output_limit; subprocess killed. raise output_limit "
                    "or narrow the command's output.]"
                    if result.truncated
                    else ""
                )
                await run._commit_reserved_binding(
                    reserved,
                    result.stdout + truncation_note,
                    metadata=run._binding_metadata(
                        reserved,
                        produced_by=f"exec:{normalized_name}",
                        operation="exec",
                    ),
                    call_base=call_base,
                    call_count=call_count,
                )
                _close_process_job(_take_process_job(process))
                return result
        except BaseException:
            await run._release_binding(
                reserved,
                call_base=call_base,
                call_count=call_count,
            )
            if process is not None:
                # 超时, 异常或取消时终止并等待子进程, 避免遗留进程。
                await _terminate_process(process)
            raise


async def _read_process_streams(
    process: Any,
    *,
    stdin_payload: bytes | None,
    output_limit: int | None,
    stderr_tail: bytearray,
) -> tuple[bytes, bool, bytes, int]:
    """从启动时并发处理三条 pipe, 并等待子进程退出。"""

    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    stdout_truncated = False

    async def read_stdout() -> None:
        """读取 stdout; 越过有限上限时立即终止进程。"""

        nonlocal stdout_bytes, stdout_truncated

        async def kill_at_limit() -> None:
            """在 stdout 首次越界时立即终止仍在运行的子进程。"""

            await _terminate_process(process)

        stdout_bytes, stdout_truncated = await _drain_stream(
            process.stdout,
            limit=output_limit,
            on_limit=kill_at_limit,
        )

    async def read_stderr() -> None:
        """完整排空 stderr; stdout 上限不适用于诊断输出。"""

        nonlocal stderr_bytes
        stderr_bytes, _ = await _drain_stream(
            process.stderr,
            limit=None,
            tail=stderr_tail,
        )

    async def write_stdin() -> None:
        """发送完整 stdin 后关闭 pipe; 子进程提前关闭时按 communicate 语义忽略。"""

        if process.stdin is None:
            return
        try:
            if stdin_payload is not None:
                await process.stdin.send(stdin_payload)
        except BrokenPipeError, anyio.BrokenResourceError, anyio.ClosedResourceError:
            pass
        finally:
            with suppress(
                BrokenPipeError,
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
            ):
                await process.stdin.aclose()

    async with anyio.create_task_group() as task_group:
        # 先调度三条 pipe, 再等待退出; 任何方向的大数据都不会堵住另一个方向。
        task_group.start_soon(read_stdout)
        task_group.start_soon(read_stderr)
        task_group.start_soon(write_stdin)
        return_code = await process.wait()
    return stdout_bytes, stdout_truncated, stderr_bytes, return_code


flow = Flow()
