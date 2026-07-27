"""公开 FusionFlow 的运行入口、执行原语与稳定数据模型。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from .flow import flow
from .model import (
    AgentConfig,
    AgentHandle,
    AgentInvocation,
    BlockHandle,
    ContainsRule,
    EqualsRule,
    ExecResult,
    ExecutionTrace,
    PipelineStep,
    PredicateRule,
    RangeRule,
    RegexRule,
    RunResult,
    ServiceHandle,
    ServiceParam,
    SessionResult,
    SessionRunner,
    StaticRule,
    TokenSummary,
    TokenUsage,
    _with_agent_defaults,
    aggregate_tokens,
    assert_safe_name,
    format_token_count,
)
from .runtime import RunContext, _now_iso, current_run_context, gc_runs, run


def _legacy_agent(
    config: AgentConfig,
    *,
    runner: SessionRunner | None = None,
) -> Callable[[AgentInvocation], Awaitable[str]]:
    """创建可调用 Agent; 显式 runner 可脱离 ``run()`` 独立执行。"""

    handle = flow.agent(config)

    async def invoke(invocation: AgentInvocation) -> str:
        """优先调用显式 runner, 并在 active run 中只写独立诊断 trace。"""

        try:
            context = current_run_context()
        except RuntimeError:
            context = None
        selected_runner = runner if runner is not None else context.runner if context else None
        if selected_runner is None:
            raise RuntimeError("Agent requires an injected runner")

        started_at = _now_iso()
        started = time.perf_counter()
        raw = await selected_runner(
            _with_agent_defaults(
                config,
                max_tokens=8192,
                temperature=1.0,
            ),
            invocation,
        )
        result = raw if isinstance(raw, SessionResult) else SessionResult(text=raw)
        if not isinstance(result.text, str):
            raise TypeError("Agent runner must return SessionResult or str")

        if context is not None:
            trace = ExecutionTrace(
                trace_id=f"session-{uuid4().hex[:12]}",
                kind="session",
                label=handle.name,
                started_at=started_at,
                status="ok",
                finished_at=_now_iso(),
                duration_ms=(time.perf_counter() - started) * 1_000,
                input_summary=invocation.prompt,
                output_summary=result.text,
                tokens=TokenUsage(
                    calls=1,
                    input=result.input_tokens,
                    output=result.output_tokens,
                ),
                metadata={"agent": handle.name},
            )
            await context._commit_legacy_agent_call(handle.name, trace)
        return result.text

    vars(invoke).update(
        __agentName=handle.name,
        __config=handle.config,
        agent_name=handle.name,
        config=handle.config,
    )
    return invoke


Agent = _legacy_agent


__all__ = [
    "Agent",
    "AgentConfig",
    "AgentHandle",
    "AgentInvocation",
    "BlockHandle",
    "ContainsRule",
    "EqualsRule",
    "ExecResult",
    "ExecutionTrace",
    "PipelineStep",
    "PredicateRule",
    "RangeRule",
    "RegexRule",
    "RunContext",
    "RunResult",
    "ServiceHandle",
    "ServiceParam",
    "SessionResult",
    "SessionRunner",
    "StaticRule",
    "TokenSummary",
    "TokenUsage",
    "aggregate_tokens",
    "assert_safe_name",
    "flow",
    "format_token_count",
    "gc_runs",
    "run",
]
