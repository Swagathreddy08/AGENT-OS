"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
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
  error?: string | null;
}

interface ExecutionLog {
  node_id: string;
  status: string;
  output?: Record<string, unknown>;
  error?: string;
  attempt?: number;
}

interface ApprovalResponse {
  id: string;
  execution_id: string;
  node_id: string;
  status: string;
  payload: Record<string, unknown>;
}

const nodeTypeLabels: Record<string, string> = {
  "Manual Trigger": "manual_trigger",
  "GitHub Issue Created": "github_issue_created",
  "GitHub PR Created": "github_pr_created",
  "Webhook Trigger": "webhook_trigger",
  "Scheduled Trigger": "scheduled_trigger",
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
  const backendType = String(data.backendType || "");
  const rawConfig = data.config;
  const config =
    rawConfig !== null && typeof rawConfig === "object"
      ? (rawConfig as Record<string, unknown>)
      : {};
  const switchConfig = config.routes;
  const switchRoutes =
    switchConfig !== null && typeof switchConfig === "object"
      ? Object.values(switchConfig).filter(
          (route): route is string => typeof route === "string",
        )
      : [];
  const sourceHandles =
    backendType === "if_else" || backendType === "text_classifier"
      ? ["true", "false"]
      : backendType === "switch"
        ? Array.from(
            new Set([
              ...switchRoutes,
              ...(typeof config.default_route === "string"
                ? [config.default_route]
                : []),
            ]),
          )
        : [];
  return (
    <div className="min-w-[150px] rounded-md border border-slate-600 bg-slate-100 px-3 py-2 text-center text-sm font-medium text-slate-900 shadow-lg">
      <Handle type="target" position={Position.Left} />
      {String(data.label || "Node")}
      {sourceHandles.length === 0 ? (
        <Handle type="source" position={Position.Right} />
      ) : (
        sourceHandles.map((id, index) => (
          <Handle
            key={id}
            id={id}
            type="source"
            position={Position.Right}
            style={{
              top: `${((index + 1) / (sourceHandles.length + 1)) * 100}%`,
            }}
          />
        ))
      )}
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
  {
    category: "Trigger",
    nodes: [
      "Manual Trigger",
      "GitHub Issue Created",
      "GitHub PR Created",
      "Webhook Trigger",
      "Scheduled Trigger",
    ],
  },
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
  const [nodeConfigText, setNodeConfigText] = useState("{}");
  const [nodeConfigTextId, setNodeConfigTextId] = useState<string | null>(null);
  const [githubToken, setGithubToken] = useState("");
  const [githubRepository, setGithubRepository] = useState("");
  const [githubAccount, setGithubAccount] = useState("");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [approvals, setApprovals] = useState<ApprovalResponse[]>([]);

  useEffect(() => {
    setNodeConfigTextId(selectedNode?.id || null);
    setNodeConfigText(
      JSON.stringify(selectedNode?.data.config || {}, null, 2),
    );
  }, [selectedNode?.id]);

  useEffect(() => {
    let stopped = false;
    async function refreshApprovals() {
      try {
        const response = await api.get<ApprovalResponse[]>("/approvals");
        if (!stopped) {
          setApprovals(response.data.filter((approval) => approval.status === "pending"));
        }
      } catch {
        if (!stopped) setMessage("Unable to refresh pending approvals");
      }
    }

    void refreshApprovals();
    const timer = setInterval(() => void refreshApprovals(), 5000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

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
          execution.data.status === "failed" && execution.data.error
            ? `Execution failed · ${execution.data.error}`
            : `Execution ${execution.data.status} · ${execution.data.id.slice(0, 8)}`,
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
      const selectedConfig =
        selectedNode?.id && nodeConfigTextId === selectedNode.id
          ? (JSON.parse(nodeConfigText) as unknown)
          : selectedNode?.data.config || {};
      if (
        selectedConfig === null ||
        typeof selectedConfig !== "object" ||
        Array.isArray(selectedConfig)
      ) {
        setMessage("Node configuration must be a JSON object");
        return null;
      }
      const graph = {
        nodes: nodes.map((node) => ({
          id: node.id,
          type: String(node.data.backendType || "manual_trigger"),
          position: node.position,
          data: {
            label: String(node.data.label || ""),
            description: String(node.data.description || ""),
            config:
              selectedNode?.id === node.id
                ? selectedConfig
                : node.data.config || {},
          },
        })),
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
    } catch (error) {
      if (error instanceof SyntaxError) {
        setMessage("Node configuration must be valid JSON");
        return null;
      }
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
        config: {},
        status: "ready",
      },
    };
    setNodes((currentNodes) => [...currentNodes, newNode]);
    setSelectedNode(newNode);
  }

  function updateSelectedNode(
    field: "label" | "description" | "config",
    value: string | Record<string, unknown>,
  ) {
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

  async function connectGithub() {
    try {
      await api.post("/integrations/github/connect", {
        access_token: githubToken,
        repository: githubRepository,
        account: githubAccount || undefined,
      });
      setGithubToken("");
      setMessage("GitHub integration connected");
    } catch {
      setMessage("GitHub connection failed; check the token and repository");
    }
  }

  async function connectDatabase() {
    try {
      await api.post("/integrations/database/connect", {
        connection_url: databaseUrl,
      });
      setDatabaseUrl("");
      setMessage("Read-only database integration connected");
    } catch {
      setMessage("Database connection failed; check host allowlisting and credentials");
    }
  }

  async function decideApproval(approvalId: string, action: "approve" | "reject") {
    try {
      await api.post(`/approvals/${approvalId}/${action}`, { decision: {} });
      const refreshed = await api.get<ApprovalResponse[]>("/approvals");
      setApprovals(
        refreshed.data.filter((approval) => approval.status === "pending"),
      );
      setMessage(action === "approve" ? "Approval approved" : "Approval rejected");
    } catch {
      setMessage(`Unable to ${action} approval`);
    }
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
                type="button"
                onClick={() => addNode(nodeName)}
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
            nodeTypes={agentNodeTypes}
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
          {executionLogs.length === 0 ? (
            <div className="text-xs text-slate-400">
              {executionId ? "Waiting for worker..." : "No active execution"}
            </div>
          ) : (
            <div className="max-h-28 space-y-1 overflow-y-auto text-xs">
              {executionLogs.map((log) => (
                <div key={`${log.node_id}-${log.attempt || 1}`} className="flex justify-between gap-2">
                  <span className="truncate text-slate-300">{log.node_id}</span>
                  <span
                    title={log.error}
                    className={`max-w-[70%] truncate ${
                      log.status === "failed" ? "text-red-400" : "text-slate-400"
                    }`}
                  >
                    {log.error ? `${log.status}: ${log.error}` : log.status}
                  </span>
                </div>
              ))}
            </div>
          )}
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
                value={String(selectedNode.data.label || "")}
                onChange={(event) => updateSelectedNode("label", event.target.value)}
              />
            </label>
            <label className="block text-sm">
              Description
              <textarea
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2"
                rows={4}
                value={String(selectedNode.data.description || "")}
                onChange={(event) => updateSelectedNode("description", event.target.value)}
              />
            </label>
            <label className="block text-sm">
              Node configuration (JSON)
              <textarea
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs"
                rows={8}
                value={nodeConfigText}
                onChange={(event) => setNodeConfigText(event.target.value)}
                onBlur={() => {
                  try {
                    const config = JSON.parse(nodeConfigText) as unknown;
                    if (
                      config !== null &&
                      typeof config === "object" &&
                      !Array.isArray(config)
                    ) {
                      updateSelectedNode(
                        "config",
                        config as Record<string, unknown>,
                      );
                    }
                  } catch {
                    setMessage("Node configuration must be valid JSON");
                  }
                }}
              />
            </label>
          </div>
        ) : (
          <p className="text-sm text-slate-500">Select a node to configure it.</p>
        )}
        {workflowId && (
          <div className="mt-6 border-t border-slate-800 pt-4 text-xs">
            <p className="mb-1 font-medium">Generic signed webhook URL</p>
            <p className="break-all text-slate-400">
              {typeof window !== "undefined"
                ? `${window.location.origin}/api/v1/webhooks/workflows/${workflowId}`
                : ""}
            </p>
          </div>
        )}
        <div className="mt-6 border-t border-slate-800 pt-4">
          <p className="mb-2 text-xs uppercase tracking-wider text-slate-500">
            Integrations
          </p>
          <label className="mb-2 block text-xs">
            GitHub repository (owner/repo)
            <input
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1"
              value={githubRepository}
              onChange={(event) => setGithubRepository(event.target.value)}
            />
          </label>
          <label className="mb-2 block text-xs">
            GitHub account (optional)
            <input
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1"
              value={githubAccount}
              onChange={(event) => setGithubAccount(event.target.value)}
            />
          </label>
          <label className="mb-2 block text-xs">
            GitHub access token
            <input
              type="password"
              autoComplete="new-password"
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1"
              value={githubToken}
              onChange={(event) => setGithubToken(event.target.value)}
            />
          </label>
          <button
            type="button"
            onClick={() => void connectGithub()}
            disabled={!githubToken || !githubRepository}
            className="mb-4 rounded-md bg-slate-800 px-3 py-1.5 text-xs disabled:opacity-50"
          >
            Connect GitHub
          </button>
          <label className="mb-2 block text-xs">
            PostgreSQL URL (host must be allowlisted)
            <input
              type="password"
              autoComplete="new-password"
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1"
              value={databaseUrl}
              onChange={(event) => setDatabaseUrl(event.target.value)}
            />
          </label>
          <button
            type="button"
            onClick={() => void connectDatabase()}
            disabled={!databaseUrl}
            className="rounded-md bg-slate-800 px-3 py-1.5 text-xs disabled:opacity-50"
          >
            Connect read-only database
          </button>
        </div>
        <div className="mt-6 border-t border-slate-800 pt-4">
          <p className="mb-2 text-xs uppercase tracking-wider text-slate-500">
            Pending approvals
          </p>
          {approvals.length === 0 ? (
            <p className="text-xs text-slate-500">No pending approvals</p>
          ) : (
            approvals.map((approval) => (
              <div key={approval.id} className="mb-3 rounded border border-slate-800 p-2">
                <p className="mb-2 text-xs">
                  {String(approval.payload.message || approval.node_id)}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void decideApproval(approval.id, "approve")}
                    className="rounded bg-emerald-800 px-2 py-1 text-xs"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => void decideApproval(approval.id, "reject")}
                    className="rounded bg-red-900 px-2 py-1 text-xs"
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </aside>
    </main>
  );
}
