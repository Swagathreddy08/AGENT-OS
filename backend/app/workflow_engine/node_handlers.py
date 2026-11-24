import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models.entities import Integration
from app.workflow_engine.errors import ApprovalRequired, NonRetryableWorkflowError


def _value_at_path(source: Any, path: str) -> Any:
    value = source
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if index < len(value) else None
        else:
            return None
    return value


def _condition(state: dict, config: dict) -> bool:
    value = _value_at_path(state, str(config.get("field", "input.text")))
    expected = config.get("value")
    operator = config.get("operator", "equals")
    try:
        if operator == "equals":
            return value == expected
        if operator == "not_equals":
            return value != expected
        if operator == "contains":
            return expected in value
        if operator == "greater_than":
            return value > expected
        if operator == "less_than":
            return value < expected
        if operator == "is_true":
            return value is True
        if operator == "is_false":
            return value is False
    except (TypeError, ValueError) as exc:
        raise NonRetryableWorkflowError(
            f"Cannot apply condition '{operator}' to the configured value."
        ) from exc
    raise NonRetryableWorkflowError(f"Unsupported condition operator '{operator}'.")


def _render_template(value: Any, state: dict) -> Any:
    if isinstance(value, str):
        def replace(match):
            resolved = _value_at_path(state, match.group(1))
            return "" if resolved is None else str(resolved)

        return re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", replace, value)
    if isinstance(value, list):
        return [_render_template(item, state) for item in value]
    if isinstance(value, dict):
        return {key: _render_template(item, state) for key, item in value.items()}
    return value


def _public_http_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise NonRetryableWorkflowError("HTTP Request URL is invalid.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise NonRetryableWorkflowError(
            "HTTP Request requires an HTTPS URL on port 443 without embedded credentials."
        )
    allowed_hosts = {host.lower() for host in settings.http_allowed_host_list()}
    if parsed.hostname.lower() not in allowed_hosts:
        raise NonRetryableWorkflowError(
            f"HTTP Request host '{parsed.hostname}' is not in HTTP_ALLOWED_HOSTS."
        )
    return url


def create_node_handlers(
    db: AsyncSession,
    execution_id,
    organization_id,
    incoming_nodes: dict[str, list[str]],
) -> dict[str, Callable]:
    async def classifier(state: dict, _config: dict) -> dict:
        source = state.get("input", {})
        if isinstance(source, dict):
            issue = source.get("issue", {})
            text_value = " ".join(
                str(part)
                for part in (
                    source.get("text", ""),
                    issue.get("title", "") if isinstance(issue, dict) else "",
                    issue.get("body", "") if isinstance(issue, dict) else "",
                )
            ).lower()
        else:
            text_value = str(source).lower()
        category = (
            "bug"
            if any(word in text_value for word in ("bug", "error", "crash"))
            else "general"
        )
        return {"category": category, "condition": category == "bug"}

    async def if_else(state: dict, config: dict) -> dict:
        condition = _condition(state, config)
        return {"condition": condition, "route": "true" if condition else "false"}

    async def switch(state: dict, config: dict) -> dict:
        value = _value_at_path(state, str(config.get("field", "input.text")))
        routes = config.get("routes", {})
        if not isinstance(routes, dict):
            raise NonRetryableWorkflowError("Switch node 'routes' must be an object.")
        route = routes.get(str(value), config.get("default_route"))
        if not isinstance(route, str) or not route:
            raise NonRetryableWorkflowError(
                "Switch node needs a matching route or a default_route."
            )
        return {"value": value, "route": route}

    async def merge(state: dict, _config: dict) -> dict:
        node_id = state.get("_active_node_id")
        node_ids = incoming_nodes.get(node_id, [])
        return {
            "inputs": {
                source_id: state.get("nodes", {}).get(source_id, {})
                for source_id in node_ids
            }
        }

    async def loop(state: dict, config: dict) -> dict:
        source = _value_at_path(state, str(config.get("items_from", "input.items")))
        if not isinstance(source, list):
            raise NonRetryableWorkflowError("Loop node items_from must resolve to a list.")
        if len(source) > 1000:
            raise NonRetryableWorkflowError("Loop node is limited to 1000 items per run.")
        template = config.get("template", "{{item}}")
        return {
            "items": [
                _render_template(
                    template,
                    {**state, "item": item, "item_index": index},
                )
                for index, item in enumerate(source)
            ]
        }

    async def approval(state: dict, config: dict) -> dict:
        node_id = state.get("_active_node_id")
        decisions = state.get("approvals", {})
        decision = decisions.get(node_id)
        if decision is None:
            payload = config.get("message") or config.get("description") or "Approval required"
            raise ApprovalRequired(
                {"message": str(payload), "context": state.get("input", {})},
                node_id,
            )
        if decision.get("status") != "approved":
            raise NonRetryableWorkflowError("Approval was rejected.")
        return {"status": "approved", "decision": decision.get("decision", {})}

    async def openai_completion(state: dict, config: dict) -> dict:
        if not settings.openai_api_key:
            raise NonRetryableWorkflowError("OPENAI_API_KEY is not configured.")
        prompt = config.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            prompt = json.dumps(
                {"input": state.get("input", {}), "nodes": state.get("nodes", {})},
                ensure_ascii=False,
            )
        prompt = _render_template(prompt, state)
        model = config.get("model", "gpt-4o-mini")
        if not isinstance(model, str) or len(model) > 100:
            raise NonRetryableWorkflowError("AI node model is invalid.")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": str(config.get("system_prompt", "You are a helpful assistant."))[:4000]},
                {"role": "user", "content": prompt[:20000]},
            ],
            "temperature": config.get("temperature", 0.2),
        }
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=body,
            )
        if response.status_code in range(400, 500):
            if response.status_code == 429:
                raise RuntimeError("AI provider rate limited the request.")
            raise NonRetryableWorkflowError(
                f"AI provider rejected the request ({response.status_code})."
            )
        response.raise_for_status()
        result = response.json()
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("AI provider returned an invalid completion response.") from exc
        return {"text": content, "model": result.get("model", model)}

    async def github_api(state: dict, config: dict) -> dict:
        integration = await db.scalar(
            select(Integration).where(
                Integration.organization_id == organization_id,
                Integration.provider == "github",
            )
        )
        if integration is None:
            raise NonRetryableWorkflowError("Connect a GitHub integration before using this node.")
        try:
            token = Fernet(settings.encryption_key.encode()).decrypt(
                integration.encrypted_credentials
            ).decode()
        except (ValueError, InvalidToken) as exc:
            raise NonRetryableWorkflowError("Stored GitHub credentials cannot be decrypted.") from exc
        repository = integration.config.get("repository")
        if not isinstance(repository, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise NonRetryableWorkflowError(
                "Set the connected GitHub integration repository as 'owner/repo'."
            )
        issue = state.get("input", {}).get("issue", {})
        issue_number = config.get("issue_number")
        if issue_number is None and isinstance(issue, dict):
            issue_number = issue.get("number")
        if not isinstance(issue_number, int) or issue_number < 1:
            raise NonRetryableWorkflowError("GitHub API node needs a valid issue_number.")
        action = config.get("action", "create_issue_comment")
        if action == "create_issue_comment":
            body = _render_template(config.get("body", ""), state)
            if not isinstance(body, str) or not body.strip():
                raise NonRetryableWorkflowError("GitHub comment body cannot be empty.")
            marker = f"<!-- agentos:{execution_id}:{state.get('_active_node_id')} -->"
            method, endpoint, payload = (
                "POST",
                f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments",
                {"body": f"{body[:59000]}\n\n{marker}"},
            )
        elif action == "add_issue_labels":
            labels = config.get("labels")
            if not isinstance(labels, list) or not labels or not all(
                isinstance(label, str) and label for label in labels
            ):
                raise NonRetryableWorkflowError("GitHub labels must be a non-empty string list.")
            method, endpoint, payload = (
                "POST",
                f"https://api.github.com/repos/{repository}/issues/{issue_number}/labels",
                {"labels": labels[:100]},
            )
        else:
            raise NonRetryableWorkflowError(f"Unsupported GitHub action '{action}'.")
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            if action == "create_issue_comment":
                existing_response = await client.get(
                    f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params={"per_page": 100, "page": 1},
                )
                if existing_response.status_code in range(400, 500):
                    if existing_response.status_code == 429:
                        raise RuntimeError("GitHub rate limited the request.")
                    raise NonRetryableWorkflowError(
                        f"GitHub rejected the request ({existing_response.status_code})."
                    )
                existing_response.raise_for_status()
                comments = existing_response.json()
                if isinstance(comments, list) and any(
                    marker in str(comment.get("body", ""))
                    for comment in comments
                    if isinstance(comment, dict)
                ):
                    return {"status": "completed", "deduplicated": True}
            response = await client.request(
                method,
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Idempotency-Key": f"{execution_id}:{state.get('_active_node_id')}",
                },
                json=payload,
            )
        if response.status_code in range(400, 500):
            if response.status_code == 429:
                raise RuntimeError("GitHub rate limited the request.")
            raise NonRetryableWorkflowError(
                f"GitHub rejected the request ({response.status_code})."
            )
        response.raise_for_status()
        return {"status": "completed", "response": response.json()}

    async def http_request(state: dict, config: dict) -> dict:
        url = config.get("url")
        if not isinstance(url, str):
            raise NonRetryableWorkflowError("HTTP Request node needs a URL.")
        url = _public_http_url(_render_template(url, state))
        method = str(config.get("method", "GET")).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise NonRetryableWorkflowError("HTTP Request method is not allowed.")
        headers = config.get("headers", {})
        if (
            not isinstance(headers, dict)
            or len(headers) > 20
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items())
        ):
            raise NonRetryableWorkflowError("HTTP Request headers must be an object with at most 20 entries.")
        blocked_headers = {
            "host",
            "authorization",
            "cookie",
            "proxy-authorization",
            "connection",
            "content-length",
            "transfer-encoding",
            "upgrade",
            "x-api-key",
            "api-key",
            "x-auth-token",
            "x-access-token",
        }
        if any(
            str(key).lower() in blocked_headers
            or "authorization" in str(key).lower()
            or "token" in str(key).lower()
            for key in headers
        ):
            raise NonRetryableWorkflowError("HTTP Request cannot set credential or routing headers.")
        rendered_headers = {
            **_render_template(headers, state),
            "Idempotency-Key": f"{execution_id}:{state.get('_active_node_id')}",
        }
        body = _render_template(config.get("body"), state)
        if len(json.dumps(body, ensure_ascii=False).encode()) > 1_000_000:
            raise NonRetryableWorkflowError("HTTP request body exceeded the 1 MB limit.")
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async with client.stream(
                method,
                url,
                headers=rendered_headers,
                json=body if body is not None else None,
            ) as response:
                if response.status_code in range(400, 500):
                    if response.status_code == 429:
                        raise RuntimeError("HTTP endpoint rate limited the request.")
                    raise NonRetryableWorkflowError(
                        f"HTTP endpoint rejected the request ({response.status_code})."
                    )
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1_000_000:
                        raise NonRetryableWorkflowError(
                            "HTTP response exceeded the 1 MB limit."
                        )
        try:
            response_body = json.loads(content)
        except json.JSONDecodeError:
            response_body = content.decode("utf-8", errors="replace")[:100_000]
        return {"status_code": response.status_code, "body": jsonable_encoder(response_body)}

    async def database_query(_state: dict, config: dict) -> dict:
        integration = await db.scalar(
            select(Integration).where(
                Integration.organization_id == organization_id,
                Integration.provider == "database",
            )
        )
        if integration is None:
            raise NonRetryableWorkflowError(
                "Connect an organization database integration before using this node."
            )
        sql = config.get("query")
        if (
            not isinstance(sql, str)
            or not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE)
            or ";" in sql
            or len(sql) > 10_000
        ):
            raise NonRetryableWorkflowError(
                "Database Query accepts one SELECT statement only; multi-statement SQL is disabled."
            )
        parameters = config.get("parameters", {})
        if not isinstance(parameters, dict):
            raise NonRetryableWorkflowError("Database Query parameters must be an object.")
        try:
            connection_url = Fernet(settings.encryption_key.encode()).decrypt(
                integration.encrypted_credentials
            ).decode()
        except (ValueError, InvalidToken) as exc:
            raise NonRetryableWorkflowError("Stored database credentials cannot be decrypted.") from exc
        parsed_url = urlparse(connection_url)
        if (
            parsed_url.scheme != "postgresql+asyncpg"
            or not parsed_url.hostname
            or parsed_url.hostname.lower() not in settings.database_allowed_host_list()
            or parsed_url.hostname.lower() != integration.config.get("host")
            or parsed_url.port not in (None, 5432)
        ):
            raise NonRetryableWorkflowError(
                "Database integration host is no longer allowed by DATABASE_ALLOWED_HOSTS."
            )
        engine = create_async_engine(connection_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
        try:
            async with engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                    await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                    result = await connection.execute(
                        text(f"SELECT * FROM ({sql}) AS agentos_query LIMIT 100"),
                        parameters,
                    )
                    rows = [dict(row) for row in result.mappings().all()]
                    return {"rows": jsonable_encoder(rows), "row_count": len(rows)}
        except OperationalError as exc:
            raise RuntimeError("Organization database is temporarily unavailable.") from exc
        except DBAPIError as exc:
            if exc.connection_invalidated:
                raise
            raise NonRetryableWorkflowError(
                "Database query failed; inspect the statement, parameters, and database permissions."
            ) from exc
        except SQLAlchemyError as exc:
            raise NonRetryableWorkflowError(
                "Database query failed; inspect the statement, parameters, and database permissions."
            ) from exc
        finally:
            await engine.dispose()

    return {
        "text_classifier": classifier,
        "if_else": if_else,
        "switch": switch,
        "merge": merge,
        "loop": loop,
        "approval": approval,
        "human_approval": approval,
        "ai_agent": openai_completion,
        "llm_completion": openai_completion,
        "summarizer": openai_completion,
        "github_api": github_api,
        "http_request": http_request,
        "database_query": database_query,
    }
