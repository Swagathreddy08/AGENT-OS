import hashlib
import hmac

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.main import app
from app.workers.celery_app import celery_app
from app.workers.tasks.workflow_tasks import requeue_stuck_executions, run_scheduled_workflows


def test_health_and_readiness_endpoints():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code in {200, 503}


def test_periodic_recovery_and_schedule_tasks_are_registered():
    assert requeue_stuck_executions.name.endswith(".requeue_stuck_executions")
    assert run_scheduled_workflows.name.endswith(".run_scheduled_workflows")
    scheduled_tasks = {
        entry["task"]
        for entry in celery_app.conf.beat_schedule.values()
    }
    assert "app.workers.tasks.workflow_tasks.requeue_stuck_executions" in scheduled_tasks
    assert "app.workers.tasks.workflow_tasks.run_scheduled_workflows" in scheduled_tasks
    assert scheduled_tasks.issubset(celery_app.tasks)


def test_security_headers_are_added():
    response = TestClient(app).get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "strict-origin-when-cross-origin" == response.headers["referrer-policy"]


def test_password_and_jwt_round_trips():
    password_hash = hash_password("a-secure-password")
    assert verify_password("a-secure-password", password_hash)
    assert not verify_password("wrong-password", password_hash)

    access = create_access_token("user-123")
    refresh = create_refresh_token("user-123")
    assert decode_token(access, "access") == "user-123"
    assert decode_token(refresh, "refresh") == "user-123"
    assert decode_token(access, "refresh") is None


def test_github_webhook_signature():
    original_secret = settings.github_webhook_secret
    settings.github_webhook_secret = "test-webhook-secret"
    body = b'{"action":"opened","issue":{"number":42,"title":"Bug","body":"Details"}}'
    signature = "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": signature,
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200
        assert response.json()["issue"]["number"] == 42

        invalid = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=invalid"},
        )
        assert invalid.status_code == 401
    finally:
        settings.github_webhook_secret = original_secret


def test_generic_workflow_webhook_requires_signature():
    original_secret = settings.github_webhook_secret
    settings.github_webhook_secret = "test-webhook-secret"
    try:
        response = TestClient(app).post(
            "/api/v1/webhooks/workflows/00000000-0000-0000-0000-000000000001",
            json={"event": "test"},
        )
        assert response.status_code == 401
    finally:
        settings.github_webhook_secret = original_secret
