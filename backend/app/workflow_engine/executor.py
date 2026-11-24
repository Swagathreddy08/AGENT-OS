import inspect
from collections import defaultdict, deque
from typing import Any

from app.schemas.workflow import WorkflowGraph
from app.workflow_engine.errors import ApprovalRequired, NonRetryableWorkflowError

TRIGGER_TYPES = {
    "manual_trigger",
    "github_issue_created",
    "github_pr_created",
    "webhook_trigger",
    "scheduled_trigger",
}


class WorkflowExecutor:
    """Runs a workflow graph in a deterministic, dependency-based order."""

    def __init__(
        self,
        graph: WorkflowGraph,
        handlers: dict[str, Any] | None = None,
        should_continue: Any | None = None,
        on_node_start: Any | None = None,
        on_node_complete: Any | None = None,
        on_node_error: Any | None = None,
    ):
        self.graph = graph
        self.handlers = handlers or {}
        self.should_continue = should_continue
        self.on_node_start = on_node_start
        self.on_node_complete = on_node_complete
        self.on_node_error = on_node_error

    async def execute(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        state = dict(initial_state)
        node_state = dict(state.get("nodes", {}))
        state["nodes"] = node_state

        node_by_id = {node.id: node for node in self.graph.nodes}
        adjacency = defaultdict(list)
        indegree = {node.id: 0 for node in self.graph.nodes}
        incoming_count = defaultdict(int)

        for edge in self.graph.edges:
            adjacency[edge.source].append(edge)
            indegree[edge.target] += 1
            incoming_count[edge.target] += 1

        ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)

        while ready:
            node_id = ready.popleft()
            if await self._has_been_cancelled():
                state["cancelled"] = True
                return state

            state["_active_node_id"] = node_id
            try:
                if node_id in node_state:
                    output = node_state[node_id]
                else:
                    if self.on_node_start is not None:
                        result = self.on_node_start(node_id, state)
                        if inspect.isawaitable(result):
                            await result
                    try:
                        output = await self._run_node(node_by_id[node_id], state)
                    except ApprovalRequired:
                        raise
                    except Exception as exc:
                        if self.on_node_error is not None:
                            result = self.on_node_error(node_id, exc, state)
                            if inspect.isawaitable(result):
                                await result
                        raise
                    node_state[node_id] = output
                    if self.on_node_complete is not None:
                        result = self.on_node_complete(node_id, output, state)
                        if inspect.isawaitable(result):
                            await result
            finally:
                state.pop("_active_node_id", None)

            for edge in adjacency[node_id]:
                matches = self._edge_matches(edge, output)
                handle = getattr(edge, "sourceHandle", None)

                if handle is None or matches:
                    indegree[edge.target] -= 1
                elif incoming_count[edge.target] > 1:
                    indegree[edge.target] -= 1

                if indegree[edge.target] == 0:
                    ready.append(edge.target)

        return state

    async def _has_been_cancelled(self) -> bool:
        if self.should_continue is None:
            return False

        result = self.should_continue()
        if inspect.isawaitable(result):
            result = await result
        return not bool(result)

    async def _run_node(self, node: Any, state: dict[str, Any]) -> dict[str, Any]:
        handler = self.handlers.get(node.type)

        if handler is None:
            if node.type in TRIGGER_TYPES:
                return dict(state.get("input", {}))
            raise NonRetryableWorkflowError(
                f"No handler is registered for node type '{node.type}'."
            )

        config = dict(node.data)
        node_config = config.pop("config", {})
        if isinstance(node_config, dict):
            config.update(node_config)
        return await handler(state, config)

    @staticmethod
    def _edge_matches(edge: Any, output: dict[str, Any]) -> bool:
        handle = getattr(edge, "sourceHandle", None)
        if handle not in {"true", "false"}:
            route = output.get("route")
            return handle is None or (
                isinstance(route, str) and route == handle
            )
        condition = output.get("condition")
        if not isinstance(condition, bool):
            raise NonRetryableWorkflowError(
                "Conditional edge output must include a boolean 'condition'."
            )
        return handle == ("true" if condition else "false")
