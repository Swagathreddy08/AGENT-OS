import pytest

from app.schemas.workflow import WorkflowEdge, WorkflowGraph, WorkflowNode
from app.workflow_engine.executor import WorkflowExecutor


@pytest.mark.asyncio
async def test_executor_runs_nodes_in_dependency_order():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="action", type="http_request"),
        ],
        edges=[WorkflowEdge(id="edge", source="trigger", target="action")],
    )

    async def handler(state, config):
        return {"status": "completed", "previous": state.get("nodes", {})}

    state = await WorkflowExecutor(graph, {"http_request": handler}).execute({"nodes": {}})
    assert "action" in state["nodes"]
    assert state["nodes"]["action"]["status"] == "completed"


@pytest.mark.asyncio
async def test_executor_rejects_unregistered_node_type():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="unknown", type="unknown_action"),
        ],
        edges=[WorkflowEdge(id="edge", source="trigger", target="unknown")],
    )
    with pytest.raises(ValueError, match="No handler is registered"):
        await WorkflowExecutor(graph).execute({"input": {}, "nodes": {}})


@pytest.mark.asyncio
async def test_executor_evaluates_conditional_branches():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="condition", type="if_else"),
            WorkflowNode(id="true_action", type="http_request"),
            WorkflowNode(id="false_action", type="http_request"),
        ],
        edges=[
            WorkflowEdge(id="edge1", source="trigger", target="condition"),
            WorkflowEdge(id="edge2", source="condition", target="true_action", sourceHandle="true"),
            WorkflowEdge(id="edge3", source="condition", target="false_action", sourceHandle="false"),
        ],
    )

    async def condition_handler(state, config):
        return {"condition": True}

    async def true_handler(state, config):
        return {"selected": "true"}

    async def false_handler(state, config):
        return {"selected": "false"}

    state = await WorkflowExecutor(
        graph,
        {
            "if_else": condition_handler,
            "http_request": lambda state, config: true_handler(state, config),
        },
    ).execute({"input": {}, "nodes": {}})
    assert state["nodes"]["condition"]["condition"] is True
    assert "true_action" in state["nodes"]
    assert "false_action" not in state["nodes"]
