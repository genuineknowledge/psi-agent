"""FusionFlow Step-Artifact graph values with eager structural validation.

The model is intentionally declarative: it describes workflow topology and
policy, but it does not schedule steps or assign runtime state.  Validation is
performed at construction so every ``WorkflowGraph`` instance is safe for
downstream serialization and execution planning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Literal, NotRequired, TypedDict


class ResourceRequirementDict(TypedDict):
    """JSON-ready resource requirement payload."""

    resource_id: str
    amount: int


class StepNodeDict(TypedDict):
    """JSON-ready step payload."""

    step_id: str
    name_id: str
    executor_id: str
    instruction_id: str | None
    timeout_seconds: int | None
    max_attempts: int
    resources: list[ResourceRequirementDict]
    independent: NotRequired[bool]
    depends_on: NotRequired[list[str]]


class ArtifactNodeDict(TypedDict):
    """JSON-ready artifact payload."""

    artifact_id: str
    is_input: bool
    is_output: bool
    binding_step_id: str | None


class ArtifactOperandDict(TypedDict):
    """JSON-ready artifact condition operand."""

    kind: Literal["artifact"]
    artifact_id: str


class LiteralOperandDict(TypedDict):
    """JSON-ready literal condition operand."""

    kind: Literal["literal"]
    value: str | int | float | bool


type ConditionOperandDict = ArtifactOperandDict | LiteralOperandDict
type ComparisonOperator = Literal["eq", "lt", "lte", "gt", "gte"]
type LogicalOperator = Literal["not", "and", "or"]


class ComparisonConditionDict(TypedDict):
    """JSON-ready comparison condition."""

    kind: Literal["comparison"]
    operator: ComparisonOperator
    left: ConditionOperandDict
    right: ConditionOperandDict


class LogicalConditionDict(TypedDict):
    """JSON-ready logical condition."""

    kind: Literal["logical"]
    operator: LogicalOperator
    conditions: list[SelectConditionDict]


type SelectConditionDict = ComparisonConditionDict | LogicalConditionDict


class SelectNodeDict(TypedDict):
    """JSON-ready eager artifact selection payload."""

    output_artifact_id: str
    when_true_artifact_id: str
    when_false_artifact_id: str
    condition: SelectConditionDict


class ConsumesEdgeDict(TypedDict):
    """JSON-ready artifact-to-step edge payload."""

    kind: Literal["consumes"]
    artifact_id: str
    step_id: str


class ProducesEdgeDict(TypedDict):
    """JSON-ready step-to-artifact edge payload."""

    kind: Literal["produces"]
    step_id: str
    artifact_id: str


class ForeachEdgeDict(TypedDict):
    """JSON-ready foreach source, step, and local-binding payload."""

    kind: Literal["foreach"]
    artifact_id: str
    step_id: str
    item_binding_id: str


type WorkflowEdgeDict = ConsumesEdgeDict | ProducesEdgeDict | ForeachEdgeDict


class WorkflowPolicyDict(TypedDict):
    """JSON-ready workflow policy payload."""

    max_concurrency: int | None
    timeout_seconds: int | None


class WorkflowGraphDict(TypedDict):
    """Complete JSON-ready workflow graph payload."""

    workflow_id: str
    steps: list[StepNodeDict]
    artifacts: list[ArtifactNodeDict]
    edges: list[WorkflowEdgeDict]
    policy: WorkflowPolicyDict
    selectors: list[SelectNodeDict]


class WorkflowGraphError(ValueError):
    """A graph value violates the static Step-Artifact model."""


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    """A positive quantity of one named resource required by a step."""

    resource_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class StepNode:
    """A declarative unit of work and its static execution metadata."""

    step_id: str
    name_id: str
    executor_id: str
    instruction_id: str | None = None
    timeout_seconds: int | None = None
    max_attempts: int = 1
    resources: tuple[ResourceRequirement, ...] = ()
    independent: bool = False
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject mutable or malformed nested collections early."""

        # Frozen dataclasses are only deeply immutable when nested collections
        # are immutable too; accepting a list here would leak caller mutation.
        if not isinstance(self.resources, tuple):
            raise WorkflowGraphError("resources must be a tuple")
        if not all(isinstance(requirement, ResourceRequirement) for requirement in self.resources):
            raise WorkflowGraphError("resources must contain only ResourceRequirement")
        if not isinstance(self.depends_on, tuple):
            raise WorkflowGraphError("depends_on must be a tuple")
        seen_dependencies: set[str] = set()
        for predecessor_id in self.depends_on:
            if not isinstance(predecessor_id, str) or not predecessor_id:
                raise WorkflowGraphError("depends_on must contain only non-empty step IDs")
            if predecessor_id in seen_dependencies:
                raise WorkflowGraphError(f"duplicate depends_on step: {predecessor_id}")
            seen_dependencies.add(predecessor_id)


@dataclass(frozen=True, slots=True)
class ArtifactNode:
    """A value flowing through steps or a step-local foreach item binding."""

    artifact_id: str
    is_input: bool = False
    is_output: bool = False
    binding_step_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactOperand:
    """A condition operand that reads one global artifact."""

    artifact_id: str


@dataclass(frozen=True, slots=True)
class LiteralOperand:
    """A condition operand containing one JSON scalar literal."""

    value: str | int | float | bool

    def __post_init__(self) -> None:
        """Reject values outside the condition model's literal boundary."""

        if type(self.value) not in (str, int, float, bool):
            raise WorkflowGraphError("literal value must be a string, number, or boolean")
        if type(self.value) is float and not isfinite(self.value):
            raise WorkflowGraphError("literal float value must be finite")


type ConditionOperand = ArtifactOperand | LiteralOperand


@dataclass(frozen=True, slots=True)
class ComparisonCondition:
    """A comparison between two artifact or literal operands."""

    operator: ComparisonOperator
    left: ConditionOperand
    right: ConditionOperand

    def __post_init__(self) -> None:
        """Keep comparison values inside the closed immutable condition model."""

        if self.operator not in ("eq", "lt", "lte", "gt", "gte"):
            raise WorkflowGraphError(f"unknown comparison operator: {self.operator}")
        for operand in (self.left, self.right):
            if type(operand) not in (ArtifactOperand, LiteralOperand):
                raise WorkflowGraphError("comparison operands must be condition operands")
            if type(operand) is LiteralOperand:
                operand.__post_init__()


@dataclass(frozen=True, slots=True)
class LogicalCondition:
    """A unary ``not`` or binary ``and``/``or`` condition."""

    operator: LogicalOperator
    conditions: tuple[SelectCondition, ...]

    def __post_init__(self) -> None:
        """Require an immutable condition tuple with the operator's exact arity."""

        if self.operator not in ("not", "and", "or"):
            raise WorkflowGraphError(f"unknown logical operator: {self.operator}")
        if not isinstance(self.conditions, tuple):
            raise WorkflowGraphError("conditions must be a tuple")
        expected = 1 if self.operator == "not" else 2
        if len(self.conditions) != expected:
            raise WorkflowGraphError(f"{self.operator} requires {expected} condition(s)")
        if not all(type(condition) in (ComparisonCondition, LogicalCondition) for condition in self.conditions):
            raise WorkflowGraphError("conditions must contain only select conditions")


type SelectCondition = ComparisonCondition | LogicalCondition


@dataclass(frozen=True, slots=True)
class SelectNode:
    """Eagerly choose one candidate artifact as a new output artifact."""

    output_artifact_id: str
    when_true_artifact_id: str
    when_false_artifact_id: str
    condition: SelectCondition

    def __post_init__(self) -> None:
        """Reject condition values outside the closed immutable tree."""

        self._require_artifact_id(
            self.output_artifact_id,
            "output_artifact_id",
        )
        self.input_artifact_ids()

    def input_artifact_ids(self) -> tuple[str, ...]:
        """Return sorted, deduplicated condition and candidate dependencies."""

        artifact_ids = {
            self._require_artifact_id(
                self.when_true_artifact_id,
                "when_true_artifact_id",
            ),
            self._require_artifact_id(
                self.when_false_artifact_id,
                "when_false_artifact_id",
            ),
        }
        artifact_ids.update(self._condition_artifact_ids(self.condition, set()))
        return tuple(sorted(artifact_ids))

    @staticmethod
    def _condition_artifact_ids(
        condition: object,
        active: set[int],
    ) -> set[str]:
        """Validate a condition tree while collecting artifact operands."""

        if type(condition) is ComparisonCondition:
            condition.__post_init__()
            artifact_ids: set[str] = set()
            for operand in (condition.left, condition.right):
                if type(operand) is ArtifactOperand:
                    artifact_ids.add(
                        SelectNode._require_artifact_id(
                            operand.artifact_id,
                            "condition artifact_id",
                        )
                    )
            return artifact_ids
        if type(condition) is not LogicalCondition:
            raise WorkflowGraphError("condition must be a select condition")
        condition.__post_init__()
        condition_id = id(condition)
        if condition_id in active:
            raise WorkflowGraphError("condition tree must not contain a cycle")
        active.add(condition_id)
        artifact_ids: set[str] = set()
        for child in condition.conditions:
            artifact_ids.update(SelectNode._condition_artifact_ids(child, active))
        active.remove(condition_id)
        return artifact_ids

    @staticmethod
    def _require_artifact_id(value: object, field_name: str) -> str:
        """Require a non-empty artifact identity before set operations."""

        if not isinstance(value, str) or not value:
            raise WorkflowGraphError(f"{field_name} must be a non-empty string")
        return value

    @staticmethod
    def _condition_to_dict(
        condition: SelectCondition,
    ) -> SelectConditionDict:
        """Serialize one already-validated condition tree."""

        if isinstance(condition, ComparisonCondition):
            operands: list[ConditionOperandDict] = []
            for operand in (condition.left, condition.right):
                if isinstance(operand, ArtifactOperand):
                    operands.append(
                        ArtifactOperandDict(
                            kind="artifact",
                            artifact_id=operand.artifact_id,
                        )
                    )
                else:
                    operands.append(
                        LiteralOperandDict(
                            kind="literal",
                            value=operand.value,
                        )
                    )
            return ComparisonConditionDict(
                kind="comparison",
                operator=condition.operator,
                left=operands[0],
                right=operands[1],
            )
        return LogicalConditionDict(
            kind="logical",
            operator=condition.operator,
            conditions=[SelectNode._condition_to_dict(child) for child in condition.conditions],
        )


@dataclass(frozen=True, slots=True)
class ConsumesEdge:
    """An artifact-to-step dependency."""

    artifact_id: str
    step_id: str
    # ``kind`` is fixed by the Python type and cannot be supplied by callers.
    kind: Literal["consumes"] = field(default="consumes", init=False)


@dataclass(frozen=True, slots=True)
class ProducesEdge:
    """A step-to-artifact production relation."""

    step_id: str
    artifact_id: str
    # ``kind`` is fixed by the Python type and cannot be supplied by callers.
    kind: Literal["produces"] = field(default="produces", init=False)


@dataclass(frozen=True, slots=True)
class ForeachEdge:
    """A foreach source consumed by a step through one local item binding."""

    artifact_id: str
    step_id: str
    item_binding_id: str
    # ``kind`` is fixed by the Python type and cannot be supplied by callers.
    kind: Literal["foreach"] = field(default="foreach", init=False)


type WorkflowEdge = ConsumesEdge | ProducesEdge | ForeachEdge


@dataclass(frozen=True, slots=True)
class WorkflowPolicy:
    """Optional workflow-wide concurrency and timeout limits."""

    max_concurrency: int | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class WorkflowGraph:
    """A validated, deterministic static graph of steps and artifacts.

    Cycles are allowed.  The constructor enforces identity, ownership,
    producer, and availability invariants only; cycle policy belongs to the
    future runtime/planner rather than this structural model.
    """

    workflow_id: str
    steps: tuple[StepNode, ...]
    artifacts: tuple[ArtifactNode, ...]
    edges: tuple[WorkflowEdge, ...] = ()
    policy: WorkflowPolicy = WorkflowPolicy()
    selectors: tuple[SelectNode, ...] = ()

    def __post_init__(self) -> None:
        """Validate the graph from container boundaries to cross-edge invariants."""

        # Validate outer container types before dereferencing their contents.
        # This keeps frozen graph values deeply immutable and turns malformed
        # caller input into WorkflowGraphError rather than incidental TypeError.
        self._require_identity(self.workflow_id, "workflow_id")
        if not isinstance(self.steps, tuple):
            raise WorkflowGraphError("steps must be a tuple")
        if not isinstance(self.artifacts, tuple):
            raise WorkflowGraphError("artifacts must be a tuple")
        if not isinstance(self.edges, tuple):
            raise WorkflowGraphError("edges must be a tuple")
        if not isinstance(self.policy, WorkflowPolicy):
            raise WorkflowGraphError("policy must be a WorkflowPolicy")
        if not isinstance(self.selectors, tuple):
            raise WorkflowGraphError("selectors must be a tuple")
        if not all(isinstance(step, StepNode) for step in self.steps):
            raise WorkflowGraphError("steps must contain only StepNode")
        if not all(isinstance(artifact, ArtifactNode) for artifact in self.artifacts):
            raise WorkflowGraphError("artifacts must contain only ArtifactNode")
        # WorkflowEdge is a closed union: subclasses would break dataclass
        # equality-based deduplication and could override the serialized kind.
        if not all(type(edge) in (ConsumesEdge, ProducesEdge, ForeachEdge) for edge in self.edges):
            raise WorkflowGraphError("edges must contain only workflow edges")
        if not all(type(selector) is SelectNode for selector in self.selectors):
            raise WorkflowGraphError("selectors must contain only SelectNode")

        # Step pass: validate required identities, positive policies, unique
        # step IDs, and resource uniqueness within each owning step.
        step_ids: set[str] = set()
        resource_keys: set[tuple[str, str]] = set()
        for step in self.steps:
            self._require_identity(step.step_id, "step_id")
            self._require_identity(step.name_id, "name_id")
            self._require_identity(step.executor_id, "executor_id")
            if step.instruction_id is not None:
                self._require_identity(step.instruction_id, "instruction_id")
            # A missing timeout is valid; a supplied timeout must be positive.
            self._require_positive(
                step.timeout_seconds,
                "timeout_seconds",
                allow_none=True,
            )
            self._require_positive(step.max_attempts, "max_attempts")
            if type(step.independent) is not bool:
                raise WorkflowGraphError("independent must be a boolean")
            if step.step_id in step_ids:
                raise WorkflowGraphError(f"duplicate step_id: {step.step_id}")
            step_ids.add(step.step_id)
            for requirement in step.resources:
                self._require_identity(requirement.resource_id, "resource_id")
                self._require_positive(requirement.amount, "resource amount")
                resource_key = (step.step_id, requirement.resource_id)
                if resource_key in resource_keys:
                    raise WorkflowGraphError(f"duplicate resource requirement: {resource_key}")
                resource_keys.add(resource_key)

        # Explicit ordering constraints reference steps rather than artifacts.
        # Validate them only after collecting every step ID so forward
        # references are valid.  Cycles remain legal in this declarative model;
        # an execution planner may reject the one-shot cyclic subset.
        for step in self.steps:
            seen_dependencies: set[str] = set()
            for predecessor_id in step.depends_on:
                self._require_identity(predecessor_id, "depends_on step_id")
                if predecessor_id in seen_dependencies:
                    raise WorkflowGraphError(f"duplicate depends_on step: {predecessor_id}")
                seen_dependencies.add(predecessor_id)
                if predecessor_id not in step_ids:
                    raise WorkflowGraphError(f"unknown depends_on step: {predecessor_id}")

        # Artifact pass: validate identities/flags/owners and build the lookup
        # needed by later edge checks.
        artifact_ids: set[str] = set()
        artifacts_by_id: dict[str, ArtifactNode] = {}
        for artifact in self.artifacts:
            self._require_identity(artifact.artifact_id, "artifact_id")
            # ``bool`` is checked exactly so truthy integers such as 1 are not
            # accepted as an ambiguous external representation.
            if type(artifact.is_input) is not bool:
                raise WorkflowGraphError("is_input must be a boolean")
            if type(artifact.is_output) is not bool:
                raise WorkflowGraphError("is_output must be a boolean")
            if artifact.binding_step_id is not None:
                self._require_identity(
                    artifact.binding_step_id,
                    "binding_step_id",
                )
            if artifact.artifact_id in artifact_ids:
                raise WorkflowGraphError(f"duplicate artifact_id: {artifact.artifact_id}")
            artifact_ids.add(artifact.artifact_id)
            artifacts_by_id[artifact.artifact_id] = artifact

        # A shared identity would make edge endpoints ambiguous.
        shared_ids = step_ids & artifact_ids
        if shared_ids:
            raise WorkflowGraphError(f"identity used by both a step and artifact: {sorted(shared_ids)}")

        # A local binding exists only inside its foreach owner.  It therefore
        # cannot be exposed as a workflow boundary artifact.
        for artifact in self.artifacts:
            if artifact.binding_step_id is not None and artifact.binding_step_id not in step_ids:
                raise WorkflowGraphError(f"unknown binding owner step: {artifact.binding_step_id}")
            if artifact.binding_step_id is not None and (artifact.is_input or artifact.is_output):
                raise WorkflowGraphError(f"local binding cannot be a workflow input or output: {artifact.artifact_id}")

        # Edge pass state:
        # - seen_edges rejects exact duplicates;
        # - producers enforces one producer per global artifact;
        # - required_global_artifacts tracks values that must be externally
        #   supplied or produced;
        # - foreach sets enforce one source/binding relation per step/binding.
        seen_edges: set[WorkflowEdge] = set()
        producers: dict[str, str] = {}
        required_global_artifacts: set[str] = {
            artifact.artifact_id
            for artifact in self.artifacts
            if artifact.is_output and artifact.binding_step_id is None
        }
        foreach_steps: set[str] = set()
        foreach_bindings: set[str] = set()
        for edge in self.edges:
            if edge in seen_edges:
                raise WorkflowGraphError(f"duplicate edge: {edge}")
            seen_edges.add(edge)

            if isinstance(edge, ConsumesEdge):
                # consumes: artifact -> step
                self._require_identity(edge.artifact_id, "artifact_id")
                self._require_identity(edge.step_id, "step_id")
                if edge.artifact_id not in artifact_ids:
                    raise WorkflowGraphError(f"unknown consumed artifact: {edge.artifact_id}")
                if edge.step_id not in step_ids:
                    raise WorkflowGraphError(f"unknown consuming step: {edge.step_id}")
                artifact = artifacts_by_id[edge.artifact_id]
                # A foreach binding is visible only to the step that owns it.
                if artifact.binding_step_id is not None and artifact.binding_step_id != edge.step_id:
                    raise WorkflowGraphError(f"local binding consumed by other step: {edge.artifact_id}")
                if artifact.binding_step_id is None:
                    # Global consumed values must later pass availability checks.
                    required_global_artifacts.add(edge.artifact_id)
                continue

            if isinstance(edge, ProducesEdge):
                # produces: step -> artifact
                self._require_identity(edge.step_id, "step_id")
                self._require_identity(edge.artifact_id, "artifact_id")
                if edge.step_id not in step_ids:
                    raise WorkflowGraphError(f"unknown producing step: {edge.step_id}")
                if edge.artifact_id not in artifact_ids:
                    raise WorkflowGraphError(f"unknown produced artifact: {edge.artifact_id}")
                artifact = artifacts_by_id[edge.artifact_id]
                # A foreach binding is created by iteration semantics, not by
                # an ordinary producer edge.
                if artifact.binding_step_id is not None:
                    raise WorkflowGraphError(f"local binding cannot be produced: {edge.artifact_id}")
                if edge.artifact_id in producers:
                    raise WorkflowGraphError(f"artifact has multiple producers: {edge.artifact_id}")
                producers[edge.artifact_id] = edge.step_id
                continue

            # foreach: source artifact -> step, exposing item_binding_id only
            # inside that step.
            self._require_identity(edge.artifact_id, "artifact_id")
            self._require_identity(edge.step_id, "step_id")
            self._require_identity(edge.item_binding_id, "item_binding_id")
            if edge.artifact_id not in artifact_ids:
                raise WorkflowGraphError(f"unknown foreach source artifact: {edge.artifact_id}")
            if edge.step_id not in step_ids:
                raise WorkflowGraphError(f"unknown foreach step: {edge.step_id}")
            if edge.item_binding_id not in artifact_ids:
                raise WorkflowGraphError(f"unknown foreach item binding: {edge.item_binding_id}")
            source = artifacts_by_id[edge.artifact_id]
            # Iteration must read a global collection, never another step's
            # local item binding.
            if source.binding_step_id is not None:
                raise WorkflowGraphError(f"local binding cannot be a foreach source: {edge.artifact_id}")
            binding = artifacts_by_id[edge.item_binding_id]
            if binding.binding_step_id != edge.step_id:
                raise WorkflowGraphError(f"foreach binding owner does not match step: {edge.item_binding_id}")
            if edge.item_binding_id in foreach_bindings:
                raise WorkflowGraphError(f"local binding referenced by multiple foreach edges: {edge.item_binding_id}")
            foreach_bindings.add(edge.item_binding_id)
            if edge.step_id in foreach_steps:
                raise WorkflowGraphError(f"step has multiple foreach sources: {edge.step_id}")
            foreach_steps.add(edge.step_id)
            required_global_artifacts.add(edge.artifact_id)

        # Every local artifact must be materialized by exactly one foreach edge;
        # merely naming a binding owner is insufficient.
        for artifact in self.artifacts:
            if artifact.binding_step_id is not None and artifact.artifact_id not in foreach_bindings:
                raise WorkflowGraphError(
                    f"local binding must be referenced by exactly one foreach edge: {artifact.artifact_id}"
                )

        # Selectors eagerly depend on both candidates and every artifact named
        # by their condition.  Their output is a producer like a Step output.
        for selector in self.selectors:
            self._require_identity(
                selector.output_artifact_id,
                "select output_artifact_id",
            )
            if selector.output_artifact_id not in artifact_ids:
                raise WorkflowGraphError(f"unknown select output artifact: {selector.output_artifact_id}")
            output = artifacts_by_id[selector.output_artifact_id]
            if output.binding_step_id is not None:
                raise WorkflowGraphError(f"select artifact must be global: {selector.output_artifact_id}")
            for input_artifact_id in selector.input_artifact_ids():
                self._require_identity(
                    input_artifact_id,
                    "select input artifact_id",
                )
                if input_artifact_id not in artifact_ids:
                    raise WorkflowGraphError(f"unknown select input artifact: {input_artifact_id}")
                if artifacts_by_id[input_artifact_id].binding_step_id is not None:
                    raise WorkflowGraphError(f"select artifact must be global: {input_artifact_id}")
                required_global_artifacts.add(input_artifact_id)
            if selector.output_artifact_id in producers:
                raise WorkflowGraphError(f"artifact has multiple producers: {selector.output_artifact_id}")
            producers[selector.output_artifact_id] = selector.output_artifact_id

        # A global value needed by a consumer, foreach, or workflow output must
        # enter through the boundary or have exactly one producer.
        for artifact_id in required_global_artifacts:
            artifact = artifacts_by_id[artifact_id]
            if not artifact.is_input and artifact_id not in producers:
                raise WorkflowGraphError(f"global artifact must be an input or producer-backed: {artifact_id}")

        # Workflow policies are optional, but supplied values must be positive.
        self._require_positive(
            self.policy.max_concurrency,
            "max_concurrency",
            allow_none=True,
        )
        self._require_positive(
            self.policy.timeout_seconds,
            "workflow timeout_seconds",
            allow_none=True,
        )

    def to_dict(self) -> WorkflowGraphDict:
        """Return a JSON-ready payload with deterministic collection ordering."""

        # Sort steps and their resources independently so construction order
        # cannot affect serialized output.
        step_payloads: list[StepNodeDict] = []
        for step in sorted(self.steps, key=lambda item: item.step_id):
            resources: list[ResourceRequirementDict] = []
            for requirement in sorted(
                step.resources,
                key=lambda item: item.resource_id,
            ):
                requirement_payload = ResourceRequirementDict(
                    resource_id=requirement.resource_id,
                    amount=requirement.amount,
                )
                resources.append(requirement_payload)

            step_payload = StepNodeDict(
                step_id=step.step_id,
                name_id=step.name_id,
                executor_id=step.executor_id,
                instruction_id=step.instruction_id,
                timeout_seconds=step.timeout_seconds,
                max_attempts=step.max_attempts,
                resources=resources,
            )
            if step.independent:
                step_payload["independent"] = True
            if step.depends_on:
                step_payload["depends_on"] = sorted(step.depends_on)
            step_payloads.append(step_payload)

        # Artifacts have one stable identity key.
        artifact_payloads = [
            ArtifactNodeDict(
                artifact_id=artifact.artifact_id,
                is_input=artifact.is_input,
                is_output=artifact.is_output,
                binding_step_id=artifact.binding_step_id,
            )
            for artifact in sorted(
                self.artifacts,
                key=lambda item: item.artifact_id,
            )
        ]

        # Edges need a total ordering across three dataclass shapes.  The key
        # first orders by kind, then normalizes endpoints into source/target
        # positions, and finally includes the foreach-only binding identity.
        sorted_edges = sorted(
            self.edges,
            key=lambda edge: (
                edge.kind,
                (edge.step_id if isinstance(edge, ProducesEdge) else edge.artifact_id),
                (edge.artifact_id if isinstance(edge, ProducesEdge) else edge.step_id),
                (edge.item_binding_id if isinstance(edge, ForeachEdge) else ""),
            ),
        )
        edge_payloads: list[WorkflowEdgeDict] = []
        for edge in sorted_edges:
            if isinstance(edge, ConsumesEdge):
                # consumes payload keeps artifact before step.
                edge_payloads.append(
                    ConsumesEdgeDict(
                        kind=edge.kind,
                        artifact_id=edge.artifact_id,
                        step_id=edge.step_id,
                    )
                )
            elif isinstance(edge, ProducesEdge):
                # produces payload keeps step before artifact.
                edge_payloads.append(
                    ProducesEdgeDict(
                        kind=edge.kind,
                        step_id=edge.step_id,
                        artifact_id=edge.artifact_id,
                    )
                )
            else:
                # The union leaves only ForeachEdge after the two explicit cases.
                edge_payloads.append(
                    ForeachEdgeDict(
                        kind=edge.kind,
                        artifact_id=edge.artifact_id,
                        step_id=edge.step_id,
                        item_binding_id=edge.item_binding_id,
                    )
                )

        selector_payloads = [
            SelectNodeDict(
                output_artifact_id=selector.output_artifact_id,
                when_true_artifact_id=selector.when_true_artifact_id,
                when_false_artifact_id=selector.when_false_artifact_id,
                condition=SelectNode._condition_to_dict(selector.condition),
            )
            for selector in sorted(
                self.selectors,
                key=lambda item: item.output_artifact_id,
            )
        ]

        payload = WorkflowGraphDict(
            workflow_id=self.workflow_id,
            steps=step_payloads,
            artifacts=artifact_payloads,
            edges=edge_payloads,
            policy=WorkflowPolicyDict(
                max_concurrency=self.policy.max_concurrency,
                timeout_seconds=self.policy.timeout_seconds,
            ),
            selectors=selector_payloads,
        )
        return payload

    @staticmethod
    def _require_identity(value: object, field_name: str) -> None:
        """Require a non-empty string for every graph identity field."""

        if not isinstance(value, str) or not value:
            raise WorkflowGraphError(f"{field_name} must be a non-empty string")

    @staticmethod
    def _require_positive(
        value: object,
        field_name: str,
        *,
        allow_none: bool = False,
    ) -> None:
        """Require a positive integer, optionally accepting an omitted value."""

        if allow_none and value is None:
            return
        # Exact type checking rejects booleans, which are int subclasses.
        if type(value) is not int or value < 1:
            raise WorkflowGraphError(f"{field_name} must be a positive integer")
