from __future__ import annotations

import fusion_flow.workflow_graph as workflow_graph


def test_workflow_graph_public_api_contains_only_the_graph_model() -> None:
    expected = {
        "ArtifactOperand",
        "ArtifactOperandDict",
        "ArtifactNode",
        "ArtifactNodeDict",
        "ComparisonCondition",
        "ComparisonConditionDict",
        "ComparisonOperator",
        "ConditionOperand",
        "ConditionOperandDict",
        "ConsumesEdge",
        "ConsumesEdgeDict",
        "ForeachEdge",
        "ForeachEdgeDict",
        "LiteralOperand",
        "LiteralOperandDict",
        "LogicalCondition",
        "LogicalConditionDict",
        "LogicalOperator",
        "ProducesEdge",
        "ProducesEdgeDict",
        "ResourceRequirement",
        "ResourceRequirementDict",
        "SelectCondition",
        "SelectConditionDict",
        "SelectNode",
        "SelectNodeDict",
        "StepNode",
        "StepNodeDict",
        "WorkflowEdge",
        "WorkflowEdgeDict",
        "WorkflowGraph",
        "WorkflowGraphDict",
        "WorkflowGraphError",
        "WorkflowPolicy",
        "WorkflowPolicyDict",
    }

    assert set(workflow_graph.__all__) == expected
    assert all(getattr(workflow_graph, name) is not None for name in expected)
