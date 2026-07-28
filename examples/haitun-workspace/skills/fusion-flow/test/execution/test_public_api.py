from __future__ import annotations

import inspect

import fusion_flow.execution as fusion_flow

FLOW_METHODS = {
    "agent",
    "block",
    "call",
    "choice",
    "define_block",
    "evaluate",
    "evaluate_static",
    "exec",
    "filter",
    "for_each",
    "if_",
    "if_else",
    "input",
    "loop_until",
    "loop_while",
    "map",
    "parallel",
    "parallel_for_each",
    "pfilter",
    "pipeline",
    "pmap",
    "reduce",
    "repeat",
    "retry",
    "run_block",
    "service",
    "session",
    "output",
    "use",
}

PACKAGE_EXPORTS = {
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
}


def test_flow_exposes_exactly_the_documented_methods() -> None:
    methods = {
        name for name, member in inspect.getmembers(type(fusion_flow.flow), callable) if not name.startswith("_")
    }

    assert len(FLOW_METHODS) == 29
    assert methods == FLOW_METHODS


def test_package_exports_public_helpers_and_models() -> None:
    assert set(fusion_flow.__all__) == PACKAGE_EXPORTS
    assert all(getattr(fusion_flow, name, None) is not None for name in PACKAGE_EXPORTS)


def test_typescript_spellings_are_not_parallel_public_aliases() -> None:
    typescript_only_names = {
        "defineBlock",
        "evaluateStatic",
        "forEach",
        "if",
        "ifElse",
        "loopUntil",
        "loopWhile",
        "parallelForEach",
        "runBlock",
    }

    assert not typescript_only_names.intersection(dir(fusion_flow.flow))


def test_exec_is_an_async_public_primitive() -> None:
    assert inspect.iscoroutinefunction(fusion_flow.flow.exec)
