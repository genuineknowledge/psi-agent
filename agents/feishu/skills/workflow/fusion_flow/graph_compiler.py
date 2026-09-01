"""Lower checked FusionFlow Core IR into the psi-agent workflow graph model.

The shared :class:`CoreIRCompiler` owns traversal.  This module only implements
the target-specific hooks: it classifies graph assertions, collects their
facts, and finally builds the immutable Step-Artifact graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .compiler import CoreIRCompiler, _CompiledDeclarations
from .core_ir import Assertion, CompoundTerm, ConnectiveFormula, Constant, IfTerm, ListTerm, Workflow
from .workflow_graph.model import (
    ArtifactNode,
    ArtifactOperand,
    ComparisonCondition,
    ComparisonOperator,
    ConsumesEdge,
    ForeachEdge,
    LiteralOperand,
    LogicalCondition,
    ProducesEdge,
    ResourceRequirement,
    SelectCondition,
    SelectNode,
    StepNode,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowGraphError,
    WorkflowPolicy,
)


class WorkflowGraphCompilationError(ValueError):
    """A checked Core IR workflow cannot be represented by the graph target."""


@dataclass(frozen=True, slots=True)
class WorkflowGraphCompilation:
    """One graph plus the assertions deliberately left for another backend."""

    graph: WorkflowGraph
    residual_assertions: tuple[Assertion, ...]


@dataclass(frozen=True, slots=True)
class _CompiledCall:
    """Transient result of the ``CompoundTerm`` compiler hook.

    ``_build_workflow`` consumes this operator name and its recursively
    compiled arguments to dispatch graph lowering.  This is not a public graph
    node and never appears in ``WorkflowGraph`` or its serialized payload.
    """

    operator_name: str
    arguments: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _CompiledList:
    """Transient result of the ``ListTerm`` compiler hook.

    The wrapper preserves the Core IR list boundary while its items are being
    compiled; a bare tuple would be indistinguishable from a call's argument
    tuple.  It is consumed during lowering and is not a graph Artifact or any
    other public ``WorkflowGraph`` value.
    """

    items: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _GraphFact:
    """One graph-vocabulary equality normalized for workflow assembly.

    The Core IR equality may put its graph call on either side.  This private
    work item records the functional shape ``operator(arguments...) = value``
    consumed by ``_build_workflow``; it is not part of ``WorkflowGraph``.
    """

    operator_name: str
    arguments: tuple[object, ...]
    value: object


@dataclass(slots=True)
class _StepDraft:
    """Mutable StepNode payload until required fields are fully known.

    ``WorkflowGraph`` values are frozen and validated eagerly, so step facts
    accumulate here until every order-independent assertion has been seen.
    Complete sub-values such as ``ResourceRequirement`` are stored directly.
    """

    name_id: str | None = None
    executor_id: str | None = None
    instruction_id: str | None = None
    timeout_seconds: int | None = None
    # None means the assertion was absent; an explicit value of 1 must still
    # make a second max_attempts assertion a duplicate.
    max_attempts: int | None = None
    independent: bool | None = None
    resources: dict[str, int] = field(default_factory=dict)
    depends_on: set[str] = field(default_factory=set)


class WorkflowGraphCompiler(CoreIRCompiler):
    """Compile graph operators while preserving unrelated assertions.

    Graph operators fall into four groups:

    * workflow boundaries: ``input_workflow`` and ``output_workflow``;
    * step metadata: ``step_name``, ``step_instruction``, and ``step_executor``;
    * dataflow: ``consumes``, ``produces``, and ``foreach_item``;
    * policies: timeouts, retries, resources, and concurrency.

    The compiler only overrides protected hooks.  Public traversal and
    unsupported-node handling remain owned by :class:`CoreIRCompiler`.
    """

    SUPPORTED_OPERATORS = frozenset(
        {
            # Workflow boundary operators.
            "input_workflow",
            "output_workflow",
            # Required and optional step metadata.
            "step_name",
            "step_instruction",
            "step_executor",
            # Step-to-artifact dataflow operators.
            "consumes",
            "produces",
            "foreach_item",
            # Step and workflow policies.
            "step_timeout",
            "max_attempts",
            "resource_requirement",
            "max_concurrency",
            "workflow_timeout",
            # Catalog-backed scheduling metadata.
            "independent",
            "depends_on",
        }
    )
    # Executor concepts are mutually exclusive in the graph target.
    _EXECUTOR_CONCEPTS = frozenset({"Human", "Agent", "Program"})

    def _compile_constant(self, constant: Constant) -> object:
        """Preserve both the constant symbol and its executor concept tags."""

        return constant

    def _compile_compound_term(self, term: CompoundTerm) -> object:
        """Compile an operator application without interpreting the operator yet.

        Interpretation belongs to ``_build_workflow``, where the workflow name
        and the other assertions are available for cross-assertion validation.
        """

        return _CompiledCall(
            operator_name=term.operator.name,
            arguments=tuple(self._compile_term(argument) for argument in term.arguments),
        )

    def _compile_list_term(self, term: ListTerm) -> object:
        """Compile every list item while retaining the Core IR list boundary."""

        return _CompiledList(items=tuple(self._compile_term(item) for item in term.items))

    def _compile_if_term(self, term: IfTerm) -> object:
        """Reject conditional graph values and expose a graph-specific error.

        The base hook fails closed.  Wrapping its error keeps callers from
        depending on the generic compiler's exception type.
        """

        try:
            return super()._compile_if_term(term)
        except ValueError as error:
            raise WorkflowGraphCompilationError(str(error)) from error

    def _compile_assertion(self, assertion: Assertion) -> object:
        """Compile one equality into a graph fact or untouched residual IR.

        Equality is symmetric, so position does not select the graph call.
        Zero recognized calls means this backend does not own the assertion;
        exactly one defines a graph fact; more than one would try to encode
        multiple graph facts in a single equality and is rejected.
        """

        # A top-level IfTerm has one executable graph representation: it must
        # select between two Artifact constants into a named Constant. Other
        # term shapes remain on the existing graph-fact or residual path.
        for output_term, value_term in (
            (assertion.lhs, assertion.rhs),
            (assertion.rhs, assertion.lhs),
        ):
            if not isinstance(value_term, IfTerm):
                continue
            if isinstance(output_term, Constant):
                return self._compile_select(output_term, value_term)

        # Pair each possible graph call with the value on the other side.
        # Nested calls inside an unknown outer operator remain residual because
        # only top-level terms can declare a graph fact.
        graph_fact_candidates = tuple(
            (term, value)
            for term, value in (
                (assertion.lhs, assertion.rhs),
                (assertion.rhs, assertion.lhs),
            )
            if isinstance(term, CompoundTerm) and term.operator.name in self.SUPPORTED_OPERATORS
        )

        # Returning the original object makes residual IR explicit: it was not
        # compiled into any graph-specific representation.
        if not graph_fact_candidates:
            return assertion

        if len(graph_fact_candidates) > 1:
            raise WorkflowGraphCompilationError("one equality cannot declare multiple graph facts")

        # FIXME(#20): Before built-in operators grow or the workflow scheduler
        # is added, give each assertion an operator-specific compile attr/handler
        # and carry its full Assertion/Formula context through graph lowering and
        # scheduling.  The compiler and scheduler must consume the same operator
        # metadata instead of maintaining separate dispatch tables.
        #
        # HACK: Every graph operator currently forms a declaration-shaped fact:
        # one recognized call plus one term treated as its value.  That makes
        # both this positional split and the flat _GraphFact(call, value) record
        # sufficient, but _GraphFact is not a general model for future graph
        # facts.  A pre/post-condition operator may instead carry an Assertion
        # or another expression as an argument; when that vocabulary arrives,
        # preserve its term structure and give it purpose-specific lowering
        # rather than forcing it through this call/value shape.
        call_term, value_term = graph_fact_candidates[0]

        # Recognized operators are compiled recursively and fail closed on an
        # unsupported child such as IfTerm.
        call = self._compile_term(call_term)
        if not isinstance(call, _CompiledCall):
            raise TypeError("compound term hook returned an invalid graph call")
        return _GraphFact(
            operator_name=call.operator_name,
            arguments=call.arguments,
            value=self._compile_term(value_term),
        )

    def _build_workflow(
        self,
        workflow: Workflow,
        *,
        assertions: tuple[object, ...],
    ) -> object:
        """Collect graph facts, validate cross-op invariants, and build one graph.

        Frozen public graph values are created as soon as one fact fully
        determines them: artifacts, edges, and resource requirements never need
        tuple/ID shadow state.  Only incomplete step fields stay mutable until
        the final order-independent validation pass.
        """

        step_drafts: dict[str, _StepDraft] = {}
        artifacts: dict[str, ArtifactNode] = {}
        edges: set[WorkflowEdge] = set()
        selectors: list[SelectNode] = []

        # Workflow-wide policies are optional but singular.
        policy = WorkflowPolicy()

        # Assertions outside the graph vocabulary remain available to callers.
        residual: list[Assertion] = []

        for compiled in assertions:
            if isinstance(compiled, SelectNode):
                selectors.append(compiled)
                for artifact_id in (
                    compiled.output_artifact_id,
                    *compiled.input_artifact_ids(),
                ):
                    artifacts.setdefault(artifact_id, ArtifactNode(artifact_id=artifact_id))
                continue

            # Residual assertions are the untouched Core IR objects returned by
            # _compile_assertion when this backend owns no graph fact.
            if isinstance(compiled, Assertion):
                residual.append(compiled)
                continue

            # Every other value must be one normalized graph fact produced by
            # _compile_assertion; anything else breaks the compiler hook contract.
            if not isinstance(compiled, _GraphFact):
                raise TypeError("workflow graph compiler received an invalid graph fact")

            operator_name = compiled.operator_name
            arguments = compiled.arguments
            fact_value = compiled.value

            # Keep every built-in operator explicit: similar names do not imply
            # a shared IR contract, so lowering must not infer behavior from a
            # prefix, suffix, or grouped fallback.
            match operator_name:
                case "input_workflow":
                    # input_workflow(workflow) == [artifact, ...]
                    self._require_arity(arguments, 1, operator_name)
                    artifact_ids = self._list_symbols(fact_value, operator_name)
                    owner_id = self._symbol(arguments[0], "input_workflow owner")
                    self._require_owner(owner_id, workflow.name, operator_name)
                    for artifact_id in artifact_ids:
                        artifact = artifacts.setdefault(artifact_id, ArtifactNode(artifact_id=artifact_id))
                        if artifact.is_input:
                            raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {artifact_id!r}")
                        artifacts[artifact_id] = replace(artifact, is_input=True)

                case "output_workflow":
                    # output_workflow(workflow) == [artifact, ...]
                    self._require_arity(arguments, 1, operator_name)
                    artifact_ids = self._list_symbols(fact_value, operator_name)
                    owner_id = self._symbol(arguments[0], "output_workflow owner")
                    self._require_owner(owner_id, workflow.name, operator_name)
                    for artifact_id in artifact_ids:
                        artifact = artifacts.setdefault(artifact_id, ArtifactNode(artifact_id=artifact_id))
                        if artifact.is_output:
                            raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {artifact_id!r}")
                        artifacts[artifact_id] = replace(artifact, is_output=True)

                case "consumes":
                    # consumes(step) == [artifact, ...]
                    self._require_arity(arguments, 1, operator_name)
                    artifact_ids = self._list_symbols(fact_value, operator_name)
                    step_id = self._symbol(arguments[0], "consumes step")
                    step_drafts.setdefault(step_id, _StepDraft())
                    for artifact_id in artifact_ids:
                        artifacts.setdefault(artifact_id, ArtifactNode(artifact_id=artifact_id))
                        self._add_unique(
                            edges,
                            ConsumesEdge(artifact_id=artifact_id, step_id=step_id),
                            operator_name,
                        )

                case "produces":
                    # produces(step) == [artifact, ...]
                    self._require_arity(arguments, 1, operator_name)
                    artifact_ids = self._list_symbols(fact_value, operator_name)
                    step_id = self._symbol(arguments[0], "produces step")
                    step_drafts.setdefault(step_id, _StepDraft())
                    for artifact_id in artifact_ids:
                        artifacts.setdefault(artifact_id, ArtifactNode(artifact_id=artifact_id))
                        self._add_unique(
                            edges,
                            ProducesEdge(step_id=step_id, artifact_id=artifact_id),
                            operator_name,
                        )

                case "max_concurrency":
                    # max_concurrency(workflow) = count
                    self._require_arity(arguments, 1, operator_name)
                    owner_id = self._symbol(arguments[0], "max_concurrency owner")
                    self._require_owner(owner_id, workflow.name, operator_name)
                    value = self._positive_integer(fact_value, operator_name)
                    # None distinguishes "not supplied" from a duplicate value.
                    if policy.max_concurrency is not None:
                        raise WorkflowGraphCompilationError("duplicate max_concurrency")
                    policy = replace(policy, max_concurrency=value)

                case "workflow_timeout":
                    # workflow_timeout(workflow) = seconds
                    self._require_arity(arguments, 1, operator_name)
                    owner_id = self._symbol(arguments[0], "workflow_timeout owner")
                    self._require_owner(owner_id, workflow.name, operator_name)
                    value = self._positive_integer(fact_value, operator_name)
                    if policy.timeout_seconds is not None:
                        raise WorkflowGraphCompilationError("duplicate workflow_timeout")
                    policy = replace(policy, timeout_seconds=value)

                case "step_name":
                    # step_name(step) = display_name
                    self._require_arity(arguments, 1, operator_name)
                    step_id = self._symbol(arguments[0], "step_name step")
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    name_id = self._symbol(fact_value, "step_name value")
                    if step_draft.name_id is not None:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {step_id!r}")
                    step_draft.name_id = name_id

                case "step_instruction":
                    # step_instruction(step) = instruction_identity
                    self._require_arity(arguments, 1, operator_name)
                    step_id = self._symbol(arguments[0], "step_instruction step")
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    instruction_id = self._symbol(fact_value, "step_instruction value")
                    if step_draft.instruction_id is not None:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {step_id!r}")
                    step_draft.instruction_id = instruction_id

                case "step_executor":
                    # step_executor(step) = executor_identity
                    # Concept tags, when present, also select exactly one executor kind.
                    self._require_arity(arguments, 1, operator_name)
                    step_id = self._symbol(arguments[0], "step_executor step")
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    executor = self._constant(fact_value, "step_executor value")
                    self._validate_executor_concepts(executor)
                    if step_draft.executor_id is not None:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {step_id!r}")
                    step_draft.executor_id = executor.symbol

                case "foreach_item":
                    # foreach_item(step, collection_artifact) = item_binding
                    # The binding is a local artifact owned by exactly this step.
                    self._require_arity(arguments, 2, operator_name)
                    step_id = self._concept_symbol(
                        arguments[0],
                        "Step",
                        "foreach_item step",
                    )
                    step_drafts.setdefault(step_id, _StepDraft())
                    source_id = self._concept_symbol(
                        arguments[1],
                        "Artifact",
                        "foreach source",
                    )
                    binding_id = self._concept_symbol(
                        fact_value,
                        "Artifact",
                        "foreach item binding",
                    )
                    # ponytail: keep the edge collection as the source of truth;
                    # add a foreach index only if large workflows make this scan hot.
                    if any(isinstance(edge, ForeachEdge) and edge.step_id == step_id for edge in edges):
                        raise WorkflowGraphCompilationError(f"duplicate foreach_item for step {step_id!r}")
                    binding_artifact = artifacts.setdefault(binding_id, ArtifactNode(artifact_id=binding_id))
                    if binding_artifact.binding_step_id is not None:
                        raise WorkflowGraphCompilationError(f"duplicate foreach item binding {binding_id!r}")
                    artifacts.setdefault(source_id, ArtifactNode(artifact_id=source_id))
                    artifacts[binding_id] = replace(binding_artifact, binding_step_id=step_id)
                    edges.add(
                        ForeachEdge(
                            artifact_id=source_id,
                            step_id=step_id,
                            item_binding_id=binding_id,
                        )
                    )

                case "step_timeout":
                    # step_timeout(step) = seconds
                    self._require_arity(arguments, 1, operator_name)
                    step_id = self._symbol(arguments[0], "step_timeout step")
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    timeout_seconds = self._positive_integer(fact_value, operator_name)
                    if step_draft.timeout_seconds is not None:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {step_id!r}")
                    step_draft.timeout_seconds = timeout_seconds

                case "max_attempts":
                    # max_attempts(step) = count
                    self._require_arity(arguments, 1, operator_name)
                    step_id = self._symbol(arguments[0], "max_attempts step")
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    max_attempts = self._positive_integer(fact_value, operator_name)
                    if step_draft.max_attempts is not None:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {step_id!r}")
                    step_draft.max_attempts = max_attempts

                case "independent":
                    # independent(step) == True is preserved as a non-binding hint.
                    self._require_arity(arguments, 1, operator_name)
                    self._require_true(fact_value, operator_name)
                    step_id = self._concept_symbol(
                        arguments[0],
                        "Step",
                        "independent step",
                    )
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    if step_draft.independent is not None:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {step_id!r}")
                    step_draft.independent = True

                case "resource_requirement":
                    # resource_requirement(step, resource) = positive_amount
                    self._require_arity(arguments, 2, operator_name)
                    step_id = self._concept_symbol(
                        arguments[0],
                        "Step",
                        "resource_requirement step",
                    )
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    resource_id = self._concept_symbol(
                        arguments[1],
                        "Resource",
                        "resource identity",
                    )
                    amount = self._positive_integer(fact_value, operator_name)
                    if resource_id in step_draft.resources:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {(step_id, resource_id)!r}")
                    step_draft.resources[resource_id] = amount

                case "depends_on":
                    # depends_on(step, predecessor) == True declares an explicit
                    # control dependency without inventing an Artifact edge.
                    self._require_arity(arguments, 2, operator_name)
                    self._require_true(fact_value, operator_name)
                    step_id = self._concept_symbol(
                        arguments[0],
                        "Step",
                        "depends_on step",
                    )
                    predecessor_id = self._concept_symbol(
                        arguments[1],
                        "Step",
                        "depends_on predecessor",
                    )
                    step_draft = step_drafts.setdefault(step_id, _StepDraft())
                    if predecessor_id in step_draft.depends_on:
                        raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {(step_id, predecessor_id)!r}")
                    step_draft.depends_on.add(predecessor_id)

                case _:
                    # _compile_assertion recognizes names through
                    # SUPPORTED_OPERATORS.  Fail closed if that vocabulary grows
                    # without a matching, operator-specific lowering case.
                    raise WorkflowGraphCompilationError(f"unsupported graph operator: {operator_name}")

        try:
            # A StepNode becomes valid only after its required name and executor
            # facts are known.  Construct it once here instead of maintaining a
            # second set of completed step IDs.
            steps: list[StepNode] = []
            for step_id, step_draft in sorted(step_drafts.items()):
                if step_draft.depends_on and (step_draft.name_id is None or step_draft.executor_id is None):
                    raise WorkflowGraphCompilationError(f"depends_on target {step_id!r} is not a fully declared step")
                for predecessor_id in sorted(step_draft.depends_on):
                    predecessor = step_drafts.get(predecessor_id)
                    if predecessor is None or predecessor.name_id is None or predecessor.executor_id is None:
                        raise WorkflowGraphCompilationError(
                            f"depends_on predecessor {predecessor_id!r} is not a fully declared step"
                        )
                if step_draft.name_id is None:
                    raise WorkflowGraphCompilationError(f"step {step_id!r} has no step_name")
                if step_draft.executor_id is None:
                    raise WorkflowGraphCompilationError(f"step {step_id!r} has no step_executor")
                resources = [
                    ResourceRequirement(
                        resource_id=resource_id,
                        amount=amount,
                    )
                    for resource_id, amount in sorted(step_draft.resources.items())
                ]
                steps.append(
                    StepNode(
                        step_id=step_id,
                        name_id=step_draft.name_id,
                        executor_id=step_draft.executor_id,
                        instruction_id=step_draft.instruction_id,
                        timeout_seconds=step_draft.timeout_seconds,
                        # The graph model defaults retries to one when the DSL
                        # omits max_attempts.
                        max_attempts=step_draft.max_attempts if step_draft.max_attempts is not None else 1,
                        resources=tuple(resources),
                        independent=step_draft.independent is True,
                        depends_on=tuple(sorted(step_draft.depends_on)),
                    )
                )

            # All other collections already contain target graph values.  Sort
            # only to make equality and serialization independent of IR order.
            graph = WorkflowGraph(
                workflow_id=workflow.name,
                steps=tuple(steps),
                artifacts=tuple(artifacts[artifact_id] for artifact_id in sorted(artifacts)),
                # Kind order is consumes, foreach, produces.  Within each kind,
                # preserve the backend's previous deterministic endpoint order.
                edges=tuple(
                    sorted(
                        edges,
                        key=lambda edge: (
                            edge.kind,
                            edge.artifact_id if isinstance(edge, ConsumesEdge) else edge.step_id,
                            edge.step_id if isinstance(edge, ConsumesEdge) else edge.artifact_id,
                            edge.item_binding_id if isinstance(edge, ForeachEdge) else "",
                        ),
                    )
                ),
                policy=policy,
                selectors=tuple(
                    sorted(
                        selectors,
                        key=lambda selector: selector.output_artifact_id,
                    )
                ),
            )
        except WorkflowGraphError as error:
            # Present target-model invariant failures through the compiler's
            # public error type while preserving the original cause.
            raise WorkflowGraphCompilationError(str(error)) from error

        return WorkflowGraphCompilation(
            graph=graph,
            residual_assertions=tuple(residual),
        )

    def _build_program(
        self,
        declarations: _CompiledDeclarations,
        *,
        workflows: tuple[object, ...],
    ) -> object:
        """Return one compilation result per workflow in source order.

        Global constants have already served as term values; the graph target
        has no declaration table of its own.
        """

        del declarations
        return workflows

    @classmethod
    def _compile_select(cls, output: object, conditional: IfTerm) -> SelectNode:
        """Lower one named Artifact equality into an eager graph selector."""

        if not isinstance(output, Constant) or not cls._has_concept(output, "Artifact"):
            raise WorkflowGraphCompilationError("selected if output must be an Artifact constant")

        when_true = conditional.when_true
        when_false = conditional.when_false
        if isinstance(when_true, IfTerm) or isinstance(when_false, IfTerm):
            raise WorkflowGraphCompilationError("nested if branches are unsupported")
        if not isinstance(when_true, Constant) or not cls._has_concept(when_true, "Artifact"):
            raise WorkflowGraphCompilationError("if branches must be Artifact constants")
        if not isinstance(when_false, Constant) or not cls._has_concept(when_false, "Artifact"):
            raise WorkflowGraphCompilationError("if branches must be Artifact constants")

        try:
            return SelectNode(
                output_artifact_id=output.symbol,
                when_true_artifact_id=when_true.symbol,
                when_false_artifact_id=when_false.symbol,
                condition=cls._select_condition(conditional.condition),
            )
        except WorkflowGraphError as error:
            raise WorkflowGraphCompilationError(str(error)) from error

    @classmethod
    def _select_condition(cls, formula: object) -> SelectCondition:
        """Lower the closed FusionFlow condition subset into graph-owned values."""

        if isinstance(formula, ConnectiveFormula):
            left = cls._select_condition(formula.formula_left)
            match formula.connective:
                case "NOT":
                    return LogicalCondition(operator="not", conditions=(left,))
                case "AND":
                    if formula.formula_right is None:
                        raise WorkflowGraphCompilationError("AND condition requires a right formula")
                    return LogicalCondition(
                        operator="and",
                        conditions=(left, cls._select_condition(formula.formula_right)),
                    )
                case "OR":
                    if formula.formula_right is None:
                        raise WorkflowGraphCompilationError("OR condition requires a right formula")
                    return LogicalCondition(
                        operator="or",
                        conditions=(left, cls._select_condition(formula.formula_right)),
                    )
                case _:
                    raise WorkflowGraphCompilationError(f"unsupported logical connective: {formula.connective}")

        if not isinstance(formula, Assertion):
            raise WorkflowGraphCompilationError("if condition must be an equality or logical formula")

        ordered = tuple(
            (term, asserted)
            for term, asserted in (
                (formula.lhs, formula.rhs),
                (formula.rhs, formula.lhs),
            )
            if isinstance(term, CompoundTerm)
            and term.operator.name
            in {
                "comparison_lt_op",
                "comparison_lte_op",
                "comparison_gt_op",
                "comparison_gte_op",
            }
        )
        if ordered:
            if len(ordered) != 1:
                raise WorkflowGraphCompilationError("one condition cannot contain multiple ordered comparisons")
            comparison, asserted = ordered[0]
            if cls._boolean_literal(asserted) is not True:
                raise WorkflowGraphCompilationError("ordered comparison must be asserted against True")
            if len(comparison.arguments) != 2:
                raise WorkflowGraphCompilationError("ordered comparison expects two operands")
            operator: ComparisonOperator
            match comparison.operator.name:
                case "comparison_lt_op":
                    operator = "lt"
                case "comparison_lte_op":
                    operator = "lte"
                case "comparison_gt_op":
                    operator = "gt"
                case "comparison_gte_op":
                    operator = "gte"
                case _:
                    raise AssertionError("ordered comparison was filtered above")
            return ComparisonCondition(
                operator=operator,
                left=cls._select_operand(comparison.arguments[0]),
                right=cls._select_operand(comparison.arguments[1]),
            )

        if not isinstance(formula.lhs, Constant) or not isinstance(formula.rhs, Constant):
            raise WorkflowGraphCompilationError("condition operands must be constants")
        return ComparisonCondition(
            operator="eq",
            left=cls._select_operand(formula.lhs),
            right=cls._select_operand(formula.rhs),
        )

    @classmethod
    def _select_operand(cls, term: object) -> ArtifactOperand | LiteralOperand:
        """Lower one condition operand without evaluating arbitrary terms."""

        if not isinstance(term, Constant):
            raise WorkflowGraphCompilationError("condition operands must be constants")
        if cls._has_concept(term, "Artifact"):
            return ArtifactOperand(artifact_id=term.symbol)
        if cls._has_concept(term, "Bool"):
            boolean = cls._boolean_literal(term)
            if boolean is None:
                raise WorkflowGraphCompilationError("Bool condition operand must be True or False")
            return LiteralOperand(value=boolean)
        if cls._has_concept(term, "ComplexNumber"):
            try:
                value: int | float = int(term.symbol)
            except ValueError:
                try:
                    value = float(term.symbol)
                except ValueError as error:
                    raise WorkflowGraphCompilationError("ComplexNumber condition operand must be numeric") from error
            return LiteralOperand(value=value)
        return LiteralOperand(value=term.symbol)

    @classmethod
    def _boolean_literal(cls, term: object) -> bool | None:
        """Return a typed Bool literal, or None for every other constant."""

        if not isinstance(term, Constant) or not cls._has_concept(term, "Bool"):
            return None
        match term.symbol.casefold():
            case "true":
                return True
            case "false":
                return False
            case _:
                return None

    @staticmethod
    def _has_concept(constant: Constant, concept_name: str) -> bool:
        """Whether a constant was declared with one named concept."""

        return any(concept.name == concept_name for concept in constant.belong_concepts)

    @staticmethod
    def _require_arity(arguments: tuple[object, ...], expected: int, operator_name: str) -> None:
        """Require the exact arity defined by one graph operator."""

        if len(arguments) != expected:
            raise WorkflowGraphCompilationError(f"{operator_name} expects {expected} arguments, got {len(arguments)}")

    @staticmethod
    def _constant(value: object, context: str) -> Constant:
        """Narrow a compiled value to a non-empty Core IR constant."""

        if not isinstance(value, Constant) or not value.symbol:
            raise WorkflowGraphCompilationError(f"{context} must be a non-empty constant")
        return value

    @classmethod
    def _symbol(cls, value: object, context: str) -> str:
        """Extract the identity/literal text carried by a compiled constant."""

        return cls._constant(value, context).symbol

    @classmethod
    def _concept_symbol(
        cls,
        value: object,
        concept_name: str,
        context: str,
    ) -> str:
        """Extract an identity and reject a conflicting explicit concept tag.

        Untyped constants remain accepted for hand-built Core IR. The official
        parser/catalog path supplies concept tags, which must include the
        operator position's required concept.
        """

        constant = cls._constant(value, context)
        if constant.belong_concepts and concept_name not in {concept.name for concept in constant.belong_concepts}:
            raise WorkflowGraphCompilationError(f"{context} must belong to {concept_name}")
        return constant.symbol

    @classmethod
    def _require_true(cls, value: object, operator_name: str) -> None:
        """Require the positive form of a catalog Bool relation."""

        constant = cls._constant(value, f"{operator_name} RHS")
        concept_names = {concept.name for concept in constant.belong_concepts}
        if constant.symbol != "True" or (concept_names and "Bool" not in concept_names):
            raise WorkflowGraphCompilationError(f"{operator_name} RHS must be the Boolean constant True")

    @classmethod
    def _list_symbols(cls, value: object, operator_name: str) -> tuple[str, ...]:
        """Extract a duplicate-free ordered symbol list from a compiled ListTerm."""

        if not isinstance(value, _CompiledList):
            raise WorkflowGraphCompilationError(f"{operator_name} RHS must be a List term")
        symbols: list[str] = []
        seen: set[str] = set()
        for item in value.items:
            symbol = cls._symbol(item, f"{operator_name} list item")
            # Reject duplicates here because a set conversion would silently
            # erase an invalid repeated edge/boundary declaration.
            if symbol in seen:
                raise WorkflowGraphCompilationError(f"duplicate {operator_name} list item: {symbol!r}")
            seen.add(symbol)
            symbols.append(symbol)
        return tuple(symbols)

    @staticmethod
    def _require_owner(owner_id: str, workflow_id: str, operator_name: str) -> None:
        """Ensure a workflow-scoped assertion cannot mutate another workflow."""

        if owner_id != workflow_id:
            raise WorkflowGraphCompilationError(
                f"{operator_name} owner {owner_id!r} does not match workflow {workflow_id!r}"
            )

    @classmethod
    def _positive_integer(cls, value: object, operator_name: str) -> int:
        """Parse a positive ASCII-decimal policy value without accepting signs."""

        symbol = cls._symbol(value, f"{operator_name} RHS")
        # ``str.isdecimal`` accepts non-ASCII digits; the DSL contract does not.
        if not symbol.isascii() or not symbol.isdecimal():
            raise WorkflowGraphCompilationError(f"{operator_name} RHS must be a positive integer constant")
        try:
            # Python may reject extremely long decimal strings under its
            # integer-conversion safety limit; normalize that to our API error.
            number = int(symbol)
        except ValueError as error:
            raise WorkflowGraphCompilationError(f"{operator_name} RHS must be a positive integer constant") from error
        if number < 1:
            raise WorkflowGraphCompilationError(f"{operator_name} RHS must be a positive integer constant")
        return number

    @classmethod
    def _validate_executor_concepts(cls, executor: Constant) -> None:
        """Require exactly one of the graph's three executor kinds."""

        matches = {concept.name for concept in executor.belong_concepts} & cls._EXECUTOR_CONCEPTS
        if len(matches) != 1:
            raise WorkflowGraphCompilationError("step_executor must belong to exactly one of Human, Agent, or Program")

    @staticmethod
    def _add_unique[T](values: set[T], value: T, operator_name: str) -> None:
        """Insert one set-backed fact while treating repetition as invalid IR."""

        if value in values:
            raise WorkflowGraphCompilationError(f"duplicate {operator_name}: {value!r}")
        values.add(value)
