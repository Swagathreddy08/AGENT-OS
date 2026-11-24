from collections import defaultdict
from app.schemas.workflow import WorkflowGraph


TRIGGER_TYPES = {"manual_trigger", "github_issue_created", "github_pr_created", "webhook_trigger", "scheduled_trigger"}
SUPPORTED_NODE_TYPES = TRIGGER_TYPES | {
    "ai_agent",
    "llm_completion",
    "text_classifier",
    "summarizer",
    "if_else",
    "switch",
    "merge",
    "loop",
    "approval",
    "human_approval",
    "github_api",
    "http_request",
    "database_query",
}


def validate_graph(graph: WorkflowGraph) -> list[str]:
    errors: list[str] = []
    ids = [node.id for node in graph.nodes]
    if len(ids) != len(set(ids)):
        errors.append("Workflow contains duplicate node IDs.")
    id_set = set(ids)
    incoming: dict[str, int] = defaultdict(int)

    for node in graph.nodes:
        if node.type not in SUPPORTED_NODE_TYPES:
            errors.append(f"Node {node.id} uses unsupported type '{node.type}'.")

    for edge in graph.edges:
        if edge.source not in id_set:
            errors.append(f"Edge {edge.id} references unknown source {edge.source}.")
        if edge.target not in id_set:
            errors.append(f"Edge {edge.id} references unknown target {edge.target}.")
        if edge.source == edge.target:
            errors.append(f"Edge {edge.id} creates a self-loop.")
        incoming[edge.target] += 1

    triggers = [node for node in graph.nodes if node.type in TRIGGER_TYPES]
    if not triggers:
        errors.append("Workflow must have at least one trigger node.")
    if len(triggers) > 1:
        errors.append("Workflow must have exactly one starting trigger.")
    for trigger in triggers:
        if incoming[trigger.id]:
            errors.append(f"Trigger node {trigger.id} cannot have incoming edges.")

    adjacency = defaultdict(list)
    indegree = {node_id: 0 for node_id in ids}
    for edge in graph.edges:
        if edge.source in id_set and edge.target in id_set:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

    if len(triggers) == 1:
        reachable = {triggers[0].id}
        pending = [triggers[0].id]
        while pending:
            node_id = pending.pop()
            for target in adjacency.get(node_id, []):
                if target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        for node in graph.nodes:
            if node.id not in reachable:
                errors.append(f"Node {node.id} is unreachable.")

    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node_id = ready.pop()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(id_set):
        errors.append("Workflow graph contains a cycle.")
    return errors
