import hashlib
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.models.entities import Integration, Workflow
from app.api.v1.endpoints.workflows import queue_workflow_execution


router = APIRouter(prefix="/webhooks/github", tags=["github"])
workflow_webhook_router = APIRouter(prefix="/webhooks/workflows", tags=["webhooks"])


@router.post("")
async def github_webhook(request: Request, x_hub_signature_256: str | None = Header(default=None)) -> dict:
    body = await request.body()
    if not settings.github_webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub webhook secret is not configured")
    expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid GitHub webhook signature")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook payload must be a JSON object")
    event = request.headers.get("X-GitHub-Event", "ping")
    if event == "ping":
        return {"ok": True, "event": "ping"}

    issue_input = None
    trigger_type = None
    if event == "issues" and payload.get("action") in {"opened", "reopened"}:
        issue = payload.get("issue")
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid issue event payload")
        issue_input = {
            "number": issue["number"],
            "title": issue.get("title", ""),
            "body": issue.get("body") or "",
            "url": issue.get("html_url"),
        }
        trigger_type = "github_issue_created"
    elif event == "pull_request" and payload.get("action") in {"opened", "reopened"}:
        pull_request = payload.get("pull_request")
        if not isinstance(pull_request, dict) or not isinstance(pull_request.get("number"), int):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid pull request event payload")
        issue_input = {
            "number": pull_request["number"],
            "title": pull_request.get("title", ""),
            "body": pull_request.get("body") or "",
            "url": pull_request.get("html_url"),
        }
        trigger_type = "github_pr_created"

    executions = []
    repository = payload.get("repository", {})
    repository_name = repository.get("full_name") if isinstance(repository, dict) else None
    if trigger_type and issue_input is not None and isinstance(repository_name, str):
        async with SessionLocal() as db:
            workflows = (
                await db.scalars(
                    select(Workflow)
                    .join(
                        Integration,
                        Integration.organization_id == Workflow.organization_id,
                    )
                    .where(
                        Integration.provider == "github",
                        Integration.config["repository"].astext == repository_name,
                        Workflow.graph["nodes"].contains([{"type": trigger_type}]),
                    )
                )
            ).all()
            delivery = request.headers.get("X-GitHub-Delivery") or hashlib.sha256(body).hexdigest()
            for workflow in workflows:
                execution = await queue_workflow_execution(
                    workflow,
                    {"issue": issue_input, "event": event, "repository": repository_name},
                    db,
                    f"github:{delivery}",
                )
                executions.append(execution["id"])
    result = {"ok": True, "event": event, "executions": executions}
    if issue_input is not None:
        result["issue"] = issue_input
    return result


@workflow_webhook_router.post("/{workflow_id}")
async def workflow_webhook(
    workflow_id: UUID,
    request: Request,
    x_agentos_signature_256: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.github_webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook secret is not configured")
    body = await request.body()
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not x_agentos_signature_256 or not hmac.compare_digest(
        expected,
        x_agentos_signature_256,
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
    try:
        input_data = await request.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON payload") from exc
    if not isinstance(input_data, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook payload must be a JSON object")

    workflow = await db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    nodes = workflow.graph.get("nodes", [])
    if not any(node.get("type") == "webhook_trigger" for node in nodes):
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow has no webhook trigger")
    delivery = request.headers.get("X-AgentOS-Delivery") or hashlib.sha256(body).hexdigest()
    execution = await queue_workflow_execution(
        workflow,
        input_data,
        db,
        f"webhook:{delivery}",
    )
    return {"ok": True, "execution": execution}
