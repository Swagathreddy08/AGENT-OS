from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, get_user_organization_id
from app.models.entities import Integration, User
from app.schemas.integration import GitHubConnectRequest

router = APIRouter(prefix="/integrations", tags=["integrations"])


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
