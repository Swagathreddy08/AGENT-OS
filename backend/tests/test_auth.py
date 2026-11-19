from fastapi.testclient import TestClient

from app.main import app


def test_workflow_api_requires_authentication():
    response = TestClient(app).get("/api/v1/workflows")
    assert response.status_code == 401
