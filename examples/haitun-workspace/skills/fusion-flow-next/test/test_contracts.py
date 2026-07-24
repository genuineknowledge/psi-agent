from __future__ import annotations

import pytest
from fusion_flow_next import PlannedStep, check_planned_steps
from fusion_flow_next.checker import check_workflow
from fusion_flow_next.core_ir import Workflow, WorkflowFile


def test_unimplemented_phase_boundaries_fail_explicitly() -> None:
    with pytest.raises(NotImplementedError, match="checker is not implemented"):
        check_workflow(WorkflowFile((), (Workflow("example", ()),)))
    with pytest.raises(NotImplementedError, match="planning check is not implemented"):
        check_planned_steps((PlannedStep("example", "Example step", ()),), ())
