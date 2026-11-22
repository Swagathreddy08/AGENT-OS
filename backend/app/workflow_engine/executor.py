from collections import defaultdict, deque
from typing import Any

from app.schemas.workflow import WorkflowGraph

TRIGGER_TYPES = {
    "manual_trigger",
    "github_issue_created",
    "github_pr_created",
    "webhook_trigger",
    "scheduled_trigger",
}


class WorkflowExecutor:
    """Deterministic graph scheduler. Integration and AI nodes are registered handlers."""

    def __init__(self, graph: WorkflowGraph, handlers: dict[str, Any] | None = None):
        self.graph = graph
        self.handlers = handlers or {}

    async def execute(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        state = dict(initial_state)
        node_by_id = {node.id: node for node in self.graph.nodes}
        adjacency = defaultdict(list)
        indegree = {node.id: 0 for node in self.graph.nodes}
        for edge in self.graph.edges:
            adjacency[edge.source].append(edge)
            indegree[edge.target] += 1

        ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        while ready:
            node_id = ready.popleft()
            node = node_by_id[node_id]
            handler = self.handlers.get(node.type)
            if handler is None and node.type in TRIGGER_TYPES:
                output = dict(state.get("input", {}))
            elif handler is None:
                raise ValueError(f"No handler is registered for node type '{node.type}'.")
            else:
                output = await handler(state, node.data)
            state["nodes"] = {**state.get("nodes", {}), node_id: output}

            for edge in adjacency[node_id]:
                # Conditional edges are mutually exclusive. Count an unmatched
                # edge as satisfied so merges on conditional branches can run.
                matches = self._edge_matches(edge, output)
                handle = getattr(edge, "sourceHandle", None)
                if handle not in {"true", "false"} or matches:
                    indegree[edge.target] -= 1
                elif sum(
                    1
                    for incoming_edge in self.graph.edges
                    if incoming_edge.target == edge.target
                ) > 1:
                    indegree[edge.target] -= 1
                if indegree[edge.target] == 0:
                    ready.append(edge.target)
        return state

    @staticmethod
    def _edge_matches(edge: Any, output: dict[str, Any]) -> bool:
        handle = getattr(edge, "sourceHandle", None)
        if handle not in {"true", "false"}:
            return True
        condition = output.get("condition")
        if not isinstance(condition, bool):
            raise ValueError("Conditional edge output must include a boolean 'condition'.")
        return handle == ("true" if condition else "false")
