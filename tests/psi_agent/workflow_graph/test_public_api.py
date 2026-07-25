from __future__ import annotations

import psi_agent.workflow_graph as workflow_graph


def test_workflow_graph_public_api_contains_only_the_graph_model() -> None:
    expected = {
        "ArtifactNode",
        "ArtifactNodeDict",
        "ConsumesEdge",
        "ConsumesEdgeDict",
        "ForeachEdge",
        "ForeachEdgeDict",
        "ProducesEdge",
        "ProducesEdgeDict",
        "ResourceRequirement",
        "ResourceRequirementDict",
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
