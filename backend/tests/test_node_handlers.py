import pytest

from app.core.config import settings
from app.workflow_engine.errors import ApprovalRequired, NonRetryableWorkflowError
from app.workflow_engine.node_handlers import create_node_handlers, _public_http_url


@pytest.mark.asyncio
async def test_logic_and_loop_handlers_use_configured_state_paths():
    handlers = create_node_handlers(None, "execution", "organization", {})
    state = {"input": {"priority": 5, "kind": "bug", "items": [1, 2]}}

    condition = await handlers["if_else"](
        state,
        {"field": "input.priority", "operator": "greater_than", "value": 3},
    )
    switch = await handlers["switch"](
        state,
        {"field": "input.kind", "routes": {"bug": "triage"}},
    )
    loop = await handlers["loop"](
        state,
        {"items_from": "input.items", "template": "{{item_index}}={{item}}"},
    )

    assert condition == {"condition": True, "route": "true"}
    assert switch == {"value": "bug", "route": "triage"}
    assert loop == {"items": ["0=1", "1=2"]}


@pytest.mark.asyncio
async def test_approval_handler_suspends_at_the_current_node():
    handlers = create_node_handlers(None, "execution", "organization", {})

    with pytest.raises(ApprovalRequired) as exc_info:
        await handlers["approval"](
            {"_active_node_id": "approval", "input": {"ticket": 7}},
            {"message": "Review this ticket"},
        )

    assert exc_info.value.node_id == "approval"
    assert exc_info.value.payload["message"] == "Review this ticket"

    resumed = await handlers["approval"](
        {
            "_active_node_id": "approval",
            "approvals": {
                "approval": {
                    "status": "approved",
                    "decision": {"approved_by": "owner"},
                }
            },
        },
        {},
    )
    assert resumed == {
        "status": "approved",
        "decision": {"approved_by": "owner"},
    }


def test_http_requests_require_explicit_https_host_allowlisting(monkeypatch):
    monkeypatch.setattr(settings, "http_allowed_hosts", "api.example.com")
    assert _public_http_url("https://api.example.com/v1") == "https://api.example.com/v1"
    with pytest.raises(NonRetryableWorkflowError, match="HTTPS"):
        _public_http_url("http://api.example.com/v1")
    with pytest.raises(NonRetryableWorkflowError, match="not in HTTP_ALLOWED_HOSTS"):
        _public_http_url("https://unlisted.example.com/v1")
