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
