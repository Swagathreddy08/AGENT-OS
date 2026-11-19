import hashlib
import hmac
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings


router = APIRouter(prefix="/webhooks/github", tags=["github"])


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
    event = request.headers.get("X-GitHub-Event", "ping")
    if event == "ping":
        return {"ok": True, "event": "ping"}
    if event == "issues" and payload.get("action") in {"opened", "edited", "reopened"}:
        issue = payload["issue"]
        return {
            "ok": True,
            "event": "issues",
            "issue": {"number": issue["number"], "title": issue["title"], "body": issue.get("body") or ""},
        }
    return {"ok": True, "event": event}
