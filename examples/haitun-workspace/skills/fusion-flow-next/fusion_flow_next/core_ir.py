"""Target-neutral syntax objects shared by FusionFlow phases and backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Concept:
    """Catalog-owned concept referenced by workflow constants and operators."""

    name: str


@dataclass(frozen=True, slots=True)
class Constant:
    """Typed identity or literal declared or used by a workflow."""

    symbol: str
    belong_concepts: tuple[Concept, ...] = ()


@dataclass(frozen=True, slots=True)
class Operator:
    """Catalog-owned operator signature."""

    name: str
    input_concepts: tuple[Concept, ...] = ()
    output_concept: Concept | None = None

    @property
    def arity(self) -> int:
        return len(self.input_concepts)


@dataclass(frozen=True, slots=True)
class CompoundTerm:
    """Operator applied to recursive term arguments."""

    operator: Operator
    arguments: tuple[Term, ...]


@dataclass(frozen=True, slots=True)
class ListTerm:
    """Ordered list value represented as an ordinary Core IR term."""

    items: tuple[Term, ...]


@dataclass(frozen=True, slots=True)
class Assertion:
    """Atomic equality between two terms."""

    lhs: Term
    rhs: Term


@dataclass(frozen=True, slots=True)
class ConnectiveFormula:
    """Workflow condition built from assertions with NOT, AND, or OR."""

    formula_left: Formula
    connective: LogicalConnective
    formula_right: Formula | None = None

    def __post_init__(self) -> None:
        if self.connective == "NOT" and self.formula_right is not None:
            raise ValueError("NOT cannot have a right formula")
        if self.connective != "NOT" and self.formula_right is None:
            raise ValueError(f"{self.connective} requires a right formula")


@dataclass(frozen=True, slots=True)
class IfTerm:
    """Conditional term with explicit true and false branches."""

    condition: Formula
    when_true: Term
    when_false: Term


@dataclass(frozen=True, slots=True)
class Workflow:
    """Named workflow block passed between the parser and checker."""

    name: str
    assertions: tuple[Assertion, ...]


@dataclass(frozen=True, slots=True)
class WorkflowFile:
    """Parsed file containing global constants and named workflows."""

    constants: tuple[Constant, ...]
    workflows: tuple[Workflow, ...]


type LogicalConnective = Literal["NOT", "AND", "OR"]
type Term = Constant | CompoundTerm | ListTerm | IfTerm
type Formula = Assertion | ConnectiveFormula
