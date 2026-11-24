# AgentOS

AgentOS is a workflow automation platform in active development. It combines a graph editor, a validated execution engine, PostgreSQL-backed execution records, and Celery workers.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:3000
- API: http://localhost:8000
- API docs: http://localhost:8000/docs

Set strong `JWT_SECRET`, `GITHUB_WEBHOOK_SECRET`, and `ENCRYPTION_KEY` values in `.env` before exposing the services. Generate a Fernet-compatible encryption key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

For production, set `ENVIRONMENT=production`, disable API documentation with
`DOCS_ENABLED=false`, use unique database and JWT secrets, and put the
application behind an HTTPS reverse proxy. PostgreSQL and Redis are kept on
the Compose-internal network; Nginx is the only service published by Compose
and listens on port 80. Configure TLS and restrict access at the external
proxy or load balancer.

The API applies bearer authentication to workflow, execution, approval, and
integration operations. New users receive a private organization. GitHub
credentials are encrypted before storage; webhook requests are rejected
unless signature verification is configured. The API readiness endpoint
checks both PostgreSQL and Redis. Celery Beat periodically requeues executions
that remained queued for more than a minute, covering API/worker interruptions
around task publication; execution claiming is guarded by a database status
transition so duplicate deliveries do not start the same execution twice.

## Workflow nodes and operational limits

The worker supports manual, GitHub issue/PR, signed generic webhook, and
interval-scheduled triggers; rule-based text classification; conditions,
switches, merges and bounded list mapping; AI completions; GitHub comments and
labels; allowlisted HTTPS requests; read-only PostgreSQL queries; and human
approval pauses/resume. Configure node JSON in the editor. AI nodes require
`OPENAI_API_KEY`. HTTP destinations must be exact hostnames in
`HTTP_ALLOWED_HOSTS`. Database integrations require PostgreSQL hosts in
`DATABASE_ALLOWED_HOSTS`, and query transactions are forced read-only with a
10-second statement timeout and a 100-row result limit. Use a least-privilege
database role even with these controls.

Examples for the node configuration JSON editor:

- If / Else: `{"field":"input.issue.title","operator":"contains","value":"bug"}`
- Switch (route values must match outgoing edge handles):
  `{"field":"nodes.classifier.category","routes":{"bug":"bug","general":"general"}}`
- Scheduled Trigger: `{"interval_seconds":3600}`
- HTTP Request:
  `{"method":"POST","url":"https://api.example.com/events","body":{"title":"{{input.issue.title}}"}}`
- GitHub API:
  `{"action":"create_issue_comment","body":"Classified as {{nodes.classifier.category}}"}`
- Database Query:
  `{"query":"SELECT id, status FROM jobs WHERE status = :status","parameters":{"status":"queued"}}`

For switch routes and conditional branches, connect outgoing edges using the
matching source handles. Never put API keys or passwords in workflow node
configuration; GitHub and database credentials belong in the encrypted
integration forms.

GitHub webhooks at `/api/v1/webhooks/github` dispatch issue-open/reopen and
pull-request-open/reopen events to matching connected-repository workflows.
Generic webhook workflows use `/api/v1/webhooks/workflows/{workflow_id}` and
the `X-AgentOS-Signature-256` HMAC header, signed with
`GITHUB_WEBHOOK_SECRET`. Scheduled trigger configuration uses UTC intervals
and requires `interval_seconds` between 60 and 86400.

Execution checkpoints and node logs are stored in PostgreSQL. Celery retries
transient errors up to three times; Beat requeues lost queued work and stale
running work from its last node checkpoint. Delivery is at-least-once, so
external HTTP endpoints should honor the provided `Idempotency-Key`. GitHub
comments include a workflow execution marker to avoid duplicate comments on
retry.

Before unattended production use, configure external alerting for failed
executions and worker/Beat health, back up PostgreSQL and the encryption key
together, restrict secrets and deployment access, and test restore and
failure-recovery procedures. AI nodes produce completions only; they do not
have autonomous tool execution.

## Main structure

- `frontend/` — Next.js workflow editor
- `backend/` — FastAPI API and workflow engine
- `infrastructure/` — deployment and proxy configuration
- `.github/workflows/` — CI
"# AGENT-OS" 
