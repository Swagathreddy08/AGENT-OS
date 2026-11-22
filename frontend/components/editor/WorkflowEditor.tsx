"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import type { Connection, Edge, Node } from "@xyflow/react";

import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api";

interface NodeGroup {
  category: string;
  nodes: string[];
}

interface WorkflowResponse {
  id: string;
  version: number;
}

interface ExecutionResponse {
  id: string;
  status: string;
}

interface ExecutionLog {
  node_id: string;
  status: string;
  output?: Record<string, unknown>;
  error?: string;
  attempt?: number;
}

const nodeTypeLabels: Record<string, string> = {
  "Manual Trigger": "manual_trigger",
  "GitHub Issue Created": "github_issue_created",
  "Webhook Trigger": "webhook_trigger",
  "AI Agent": "ai_agent",
  "LLM Completion": "llm_completion",
  "Text Classifier": "text_classifier",
  Summarizer: "summarizer",
  "If / Else": "if_else",
  Switch: "switch",
  Merge: "merge",
  Loop: "loop",
  "GitHub API": "github_api",
  "HTTP Request": "http_request",
  "Database Query": "database_query",
};

function AgentNode({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="min-w-[150px] rounded-md border border-slate-600 bg-slate-100 px-3 py-2 text-center text-sm font-medium text-slate-900 shadow-lg">
      {String(data.label || "Node")}
    </div>
  );
}

const agentNodeTypes = { agentos: AgentNode };

const initialNodes: Node[] = [
  {
    id: "trigger",
    type: "agentos",
    position: { x: 80, y: 180 },
    data: {
      label: "GitHub Issue Created",
      backendType: "github_issue_created",
      description: "",
      status: "ready",
    },
  },
  {
    id: "classifier",
    type: "agentos",
    position: { x: 320, y: 180 },
    data: {
      label: "AI Issue Classifier",
      backendType: "text_classifier",
      description: "",
      status: "ready",
    },
  },
  {
    id: "approval",
    type: "agentos",
    position: { x: 560, y: 180 },
    data: {
      label: "Human Approval",
      backendType: "approval",
      description: "",
      status: "waiting",
    },
  },
];

const initialEdges: Edge[] = [
  { id: "e1", source: "trigger", target: "classifier" },
  { id: "e2", source: "classifier", target: "approval" },
];

const nodeLibrary: NodeGroup[] = [
  { category: "Trigger", nodes: ["Manual Trigger", "GitHub Issue Created", "Webhook Trigger"] },
  { category: "AI", nodes: ["AI Agent", "LLM Completion", "Text Classifier", "Summarizer"] },
  { category: "Logic", nodes: ["If / Else", "Switch", "Merge", "Loop"] },
  { category: "Integrations", nodes: ["GitHub API", "HTTP Request", "Database Query"] },
];

export default function WorkflowEditor({
  onSignOut,
}: {
  onSignOut: () => void;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [message, setMessage] = useState("");
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [executionLogs, setExecutionLogs] = useState<ExecutionLog[]>([]);

  useEffect(() => {
    if (!executionId) {
      return;
    }

    let stopped = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    async function refreshExecution() {
      try {
        const [execution, logs] = await Promise.all([
          api.get<ExecutionResponse>(`/executions/${executionId}`),
          api.get<ExecutionLog[]>(`/executions/${executionId}/logs`),
        ]);
        if (stopped) return;

        setExecutionLogs(logs.data);
        setMessage(
          `Execution ${execution.data.status} · ${execution.data.id.slice(0, 8)}`,
        );

        if (["completed", "failed", "cancelled"].includes(execution.data.status)) {
          if (timer) clearInterval(timer);
        }
      } catch {
        if (!stopped) setMessage("Unable to refresh execution status");
      }
    }

    void refreshExecution();
    timer = setInterval(() => void refreshExecution(), 1500);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [executionId]);

  const handleConnect = useCallback(
    (connection: Connection) => {
      setEdges((currentEdges) => addEdge(connection, currentEdges));
    },
    [setEdges],
  );

  async function saveWorkflow(): Promise<string | null> {
    setMessage("Saving...");

    try {
      const graph = {
        nodes: nodes.map((node) => ({ ...node, type: getBackendNodeType(node.id) })),
        edges,
      };
      const workflow = workflowId
        ? await api.put<WorkflowResponse>(`/workflows/${workflowId}`, {
            name: "GitHub Issue Triage",
            graph,
          })
        : await api.post<WorkflowResponse>("/workflows", {
            name: "GitHub Issue Triage",
            graph,
          });

      setWorkflowId(workflow.data.id);
      setMessage(`Saved · version ${workflow.data.version}`);
      return workflow.data.id;
    } catch {
      setMessage("Save failed; check your connection and permissions");
      return null;
    }
  }

  async function executeWorkflow() {
    const id = await saveWorkflow();
    if (!id) {
      setMessage("Execution could not be started");
      return;
    }

    try {
      const execution = await api.post<ExecutionResponse>(`/workflows/${id}/execute`, {
        input: {},
      });
      setExecutionId(execution.data.id);
      setExecutionLogs([]);
      setMessage(
        `Execution ${execution.data.status} · ${execution.data.id.slice(0, 8)}`,
      );
    } catch {
      setMessage("Execution could not be started");
    }
  }

  function addNode(nodeName: string) {
    const id = `node-${crypto.randomUUID()}`;
    const backendType = nodeTypeLabels[nodeName];
    const newNode: Node = {
      id,
      type: "agentos",
      position: {
        x: 240 + (nodes.length % 4) * 180,
        y: 160 + (nodes.length % 3) * 140,
      },
      data: {
        label: nodeName,
        backendType,
        description: "",
        status: "ready",
      },
    };
    setNodes((currentNodes) => [...currentNodes, newNode]);
    setSelectedNode(newNode);
  }

  function updateSelectedNode(field: "label" | "description", value: string) {
    if (!selectedNode) return;

    const updatedNode: Node = {
      ...selectedNode,
      data: { ...selectedNode.data, [field]: value },
    };
    setSelectedNode(updatedNode);
    setNodes((currentNodes) =>
      currentNodes.map((node) => (node.id === updatedNode.id ? updatedNode : node)),
    );
  }

  return (
    <main className="h-screen grid grid-cols-[230px_minmax(0,1fr)_280px] bg-[#090d18] text-slate-100">
      <aside className="border-r border-slate-800 p-4 overflow-y-auto">
        <div className="mb-6 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600" />
            <strong>AgentOS</strong>
          </div>
          <button
            onClick={onSignOut}
            className="text-xs text-slate-400 hover:text-white"
          >
            Sign out
          </button>
        </div>

        {nodeLibrary.map(({ category, nodes: availableNodes }) => (
          <div key={category} className="mb-5">
            <p className="mb-2 text-xs uppercase tracking-wider text-slate-500">
              {category}
            </p>
            {availableNodes.map((nodeName) => (
              <button
                key={nodeName}
                className="mb-2 w-full rounded-md border border-slate-800 bg-slate-900/50 px-3 py-2 text-left text-sm hover:border-violet-500"
              >
                {nodeName}
              </button>
            ))}
          </div>
        ))}
      </aside>

      <section className="grid grid-rows-[58px_minmax(0,1fr)_150px]">
        <header className="flex items-center justify-between border-b border-slate-800 px-5">
          <div>
            <h1 className="font-semibold">GitHub Issue Triage</h1>
            <p className="text-xs text-slate-500">{message || "Draft · version 1"}</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => void saveWorkflow()}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-sm"
            >
              Save
            </button>
            <button
              onClick={() => void executeWorkflow()}
              className="rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium"
            >
              Execute
            </button>
          </div>
        </header>

        <div>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={handleConnect}
            onNodeClick={(_, node) => setSelectedNode(node)}
            fitView
          >
            <Background color="#25304a" gap={20} />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>

        <div className="border-t border-slate-800 p-4">
          <p className="mb-2 text-xs text-slate-500">EXECUTION LOG</p>
          <div className="text-xs text-slate-400">No active execution</div>
        </div>
      </section>

      <aside className="border-l border-slate-800 p-4">
        <p className="mb-4 text-xs uppercase tracking-wider text-slate-500">
          Configuration
        </p>
        {selectedNode ? (
          <div className="space-y-3">
            <label className="block text-sm">
              Name
              <input
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2"
                defaultValue={String(selectedNode.data.label)}
              />
            </label>
            <label className="block text-sm">
              Description
              <textarea
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2"
                rows={4}
              />
            </label>
          </div>
        ) : (
          <p className="text-sm text-slate-500">Select a node to configure it.</p>
        )}
      </aside>
    </main>
  );
}
