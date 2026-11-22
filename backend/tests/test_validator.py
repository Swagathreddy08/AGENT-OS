from app.schemas.workflow import WorkflowEdge, WorkflowGraph, WorkflowNode
from app.workflow_engine.validator import validate_graph


def test_accepts_valid_dag():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="classify", type="text_classifier"),
        ],
        edges=[WorkflowEdge(id="edge", source="trigger", target="classify")],
    )
    assert validate_graph(graph) == []


def test_rejects_cycle():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="branch", type="if_else"),
        ],
        edges=[
            WorkflowEdge(id="edge1", source="trigger", target="branch"),
            WorkflowEdge(id="edge2", source="branch", target="trigger"),
        ],
    )
    errors = validate_graph(graph)
    assert any("cycle" in error for error in errors)
    assert any("incoming edges" in error for error in errors)


def test_rejects_unreachable_node():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="used", type="text_classifier"),
            WorkflowNode(id="unreachable", type="text_classifier"),
        ],
        edges=[WorkflowEdge(id="edge", source="trigger", target="used")],
    )
    errors = validate_graph(graph)
    assert any("unreachable" in error for error in errors)
