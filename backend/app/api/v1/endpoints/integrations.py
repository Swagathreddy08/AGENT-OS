import logging
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, get_user_organization_id
from app.models.entities import Integration, User
from app.schemas.integration import DatabaseConnectRequest, GitHubConnectRequest

router = APIRouter(prefix="/integrations", tags=["integrations"])
logger = logging.getLogger(__name__)


@router.post("/github/connect")
async def connect_github(
    payload: GitHubConnectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.encryption_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Credential encryption is not configured")
    try:
        cipher = Fernet(settings.encryption_key.encode())
    except ValueError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Credential encryption key is invalid") from exc

    organization_id = await get_user_organization_id(user, db)
    integration = await db.scalar(
        select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.provider == "github",
        )
    )
    encrypted_credentials = cipher.encrypt(payload.access_token.get_secret_value().encode())
    if integration is None:
        integration = Integration(
            id=uuid4(),
            organization_id=organization_id,
            provider="github",
            encrypted_credentials=encrypted_credentials,
            config={},
        )
        db.add(integration)
    else:
        integration.encrypted_credentials = encrypted_credentials
    integration.config = {"repository": payload.repository, "account": payload.account}
    await db.commit()
    await db.refresh(integration)
    return {"id": str(integration.id), "provider": integration.provider, "config": integration.config}


@router.post("/database/connect")
async def connect_database(
    payload: DatabaseConnectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.encryption_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Credential encryption is not configured")
    try:
        cipher = Fernet(settings.encryption_key.encode())
    except ValueError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Credential encryption key is invalid") from exc

    raw_url = payload.connection_url.get_secret_value()
    parsed = urlparse(raw_url)
    if (
        parsed.scheme != "postgresql+asyncpg"
        or not parsed.hostname
        or parsed.hostname.lower() not in settings.database_allowed_host_list()
        or parsed.port not in (None, 5432)
        or parsed.fragment
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Database URL must use PostgreSQL and a host in DATABASE_ALLOWED_HOSTS.",
        )
    query = {
        key: value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key == "application_name"
    }
    query["ssl"] = "require"
    connection_url = urlunparse(parsed._replace(query=urlencode(query)))

    engine = create_async_engine(connection_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.exception("Unable to validate organization database integration")
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Database connection failed; verify the URL and read-only account.",
        ) from exc
    finally:
        await engine.dispose()

    organization_id = await get_user_organization_id(user, db)
    integration = await db.scalar(
        select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.provider == "database",
        )
    )
    encrypted_credentials = cipher.encrypt(connection_url.encode())
    if integration is None:
        integration = Integration(
            id=uuid4(),
            organization_id=organization_id,
            provider="database",
            encrypted_credentials=encrypted_credentials,
            config={"host": parsed.hostname.lower()},
        )
        db.add(integration)
    else:
        integration.encrypted_credentials = encrypted_credentials
        integration.config = {"host": parsed.hostname.lower()}
    await db.commit()
    await db.refresh(integration)
    return {"id": str(integration.id), "provider": integration.provider, "config": integration.config}


@router.get("")
async def list_integrations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    organization_id = await get_user_organization_id(user, db)
    result = await db.scalars(select(Integration).where(Integration.organization_id == organization_id))
    return [
        {"id": str(item.id), "provider": item.provider, "config": item.config}
        for item in result.all()
    ]


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    organization_id = await get_user_organization_id(user, db)
    integration = await db.scalar(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.organization_id == organization_id,
        )
    )
    if integration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    await db.delete(integration)
    await db.commit()
