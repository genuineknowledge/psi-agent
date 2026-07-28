from __future__ import annotations

import pytest
from fusion_flow.core_ir import (
    Assertion,
    CompoundTerm,
    Concept,
    ConnectiveFormula,
    Constant,
    IfTerm,
    ListTerm,
    Operator,
    Workflow,
    WorkflowFile,
)


def test_workflow_core_ir_contract() -> None:
    step = Concept("Step")
    item = Constant("item", (step,))
    operator = Operator("identity", (step,), step)
    assertion = Assertion(CompoundTerm(operator, (item,)), ListTerm((item,)))
    condition = ConnectiveFormula(assertion, "NOT")
    conditional = IfTerm(condition, item, item)
    workflow = Workflow("example", (Assertion(conditional, item),))
    workflow_file = WorkflowFile((item,), (workflow,))

    assert operator.arity == 1
    assert not hasattr(assertion, "relation_symbol")
    assert workflow_file.workflows[0].assertions[0].lhs is conditional


def test_connective_formula_rejects_invalid_arity() -> None:
    value = Constant("value")
    assertion = Assertion(value, value)

    with pytest.raises(ValueError, match="NOT cannot have a right formula"):
        ConnectiveFormula(assertion, "NOT", assertion)
    with pytest.raises(ValueError, match="AND requires a right formula"):
        ConnectiveFormula(assertion, "AND")
