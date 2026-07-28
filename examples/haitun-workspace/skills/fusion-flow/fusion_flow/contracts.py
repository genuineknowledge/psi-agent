"""Result contracts shared by the FusionFlow parsing and checking phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .core_ir import WorkflowFile

type DiagnosticSeverity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class SourcePosition:
    """One-based source location without encoding-dependent character offsets."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open source range ``[start, end)``."""

    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Phase diagnostic optionally tied to source and design-review locations.

    ``design_reference`` is an identifier such as ``S01``; it is not a
    diagnostic code, URL, or source span.
    """

    severity: DiagnosticSeverity
    message: str
    span: SourceSpan | None = None
    design_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Parser-only result; ``core_ir`` exists exactly when parsing has no errors."""

    core_ir: WorkflowFile | None
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Checker-only result created from Core IR produced by a successful parse."""

    core_ir: WorkflowFile
    diagnostics: tuple[Diagnostic, ...]
