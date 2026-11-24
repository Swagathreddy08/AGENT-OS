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


@pytest.mark.asyncio
async def test_executor_stops_when_cancelled():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="action", type="http_request"),
        ],
        edges=[WorkflowEdge(id="edge", source="trigger", target="action")],
    )
    initial_state = {"input": {}, "nodes": {}}

    async def should_continue():
        return False

    state = await WorkflowExecutor(graph, should_continue=should_continue).execute(initial_state)
    assert state["cancelled"] is True
    assert "action" not in state["nodes"]
    assert initial_state["nodes"] == {}


@pytest.mark.asyncio
async def test_executor_resumes_from_checkpoint_without_rerunning_completed_nodes():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="first", type="http_request"),
            WorkflowNode(id="second", type="http_request"),
        ],
        edges=[
            WorkflowEdge(id="edge1", source="trigger", target="first"),
            WorkflowEdge(id="edge2", source="first", target="second"),
        ],
    )
    calls = []

    async def handler(state, config):
        calls.append(config["name"])
        return {"name": config["name"]}

    state = await WorkflowExecutor(
        graph,
        {"http_request": handler},
    ).execute(
        {
            "input": {},
            "nodes": {"first": {"name": "first"}},
        }
    )

    assert calls == ["second"]
    assert state["nodes"]["second"] == {"name": "second"}


@pytest.mark.asyncio
async def test_executor_routes_switch_handles_by_route_name():
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", type="manual_trigger"),
            WorkflowNode(id="switch", type="switch"),
            WorkflowNode(id="bug", type="http_request"),
            WorkflowNode(id="other", type="http_request"),
        ],
        edges=[
            WorkflowEdge(id="edge1", source="trigger", target="switch"),
            WorkflowEdge(id="edge2", source="switch", target="bug", sourceHandle="bug"),
            WorkflowEdge(id="edge3", source="switch", target="other", sourceHandle="other"),
        ],
    )

    async def switch_handler(state, config):
        return {"route": "bug"}

    state = await WorkflowExecutor(
        graph,
        {"switch": switch_handler, "http_request": lambda state, config: async_output()},
    ).execute({"input": {}, "nodes": {}})
    assert "bug" in state["nodes"]
    assert "other" not in state["nodes"]


async def async_output():
    return {"ok": True}
