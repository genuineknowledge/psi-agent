# ruff: noqa
"""通用 workflow 错误内核；不依赖任何具体 agent。"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str | None = None
    workflow_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    code: str
    message: str
    origin: str | None = None
    retryable: bool | None = None
    run_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "origin": self.origin,
            "retryable": self.retryable,
            "run_id": self.run_id,
            "details": dict(self.details),
            "extensions": dict(self.extensions),
        }


def normalize_error(
    exc: BaseException,
    ctx: RunContext,
    *,
    code: str = "runtime.unhandled",
    origin: str = "runtime",
    retryable: bool | None = None,
) -> ErrorInfo:
    return ErrorInfo(
        code=code,
        message="execution failed" if code == "runtime.unhandled" else str(exc),
        origin=origin,
        retryable=retryable,
        run_id=ctx.run_id,
        details={"exception_type": type(exc).__name__},
    )


def error_payload(
    *,
    phase: str,
    kind: str,
    message: str,
    attempts: list[dict[str, Any]],
    ctx: RunContext | None = None,
    code: str = "workflow.execution_failed",
    retryable: bool | None = None,
    details: Mapping[str, Any] | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    info = ErrorInfo(
        code=code,
        message=message,
        origin=phase,
        retryable=retryable,
        run_id=ctx.run_id if ctx else None,
        details={"kind": kind, "attempts": attempts, **(dict(details) if details else {})},
        extensions=extensions or {},
    )
    return info.as_dict()
