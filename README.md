# AgentOS

AgentOS is a production-oriented visual workflow automation platform. It combines a graph workflow editor, a validated execution engine, persistent human approvals, background workers, and GitHub issue triage.

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

The API applies bearer authentication to workflow, execution, approval, and integration operations. New users receive a private organization. GitHub credentials are encrypted before storage; webhook requests are rejected unless signature verification is configured.

## Main structure

- `frontend/` — Next.js workflow editor
- `backend/` — FastAPI API and workflow engine
- `infrastructure/` — deployment and proxy configuration
- `.github/workflows/` — CI
"# AGENT-OS" 
