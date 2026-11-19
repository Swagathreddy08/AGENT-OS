from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models.entities import OrganizationMember, User


bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    subject = decode_token(credentials.credentials)
    if subject is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired access token")
    try:
        user_id = UUID(subject)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token subject")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is unavailable")
    return user


async def get_user_organization_id(user: User, db: AsyncSession) -> UUID:
    membership = await db.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at)
        .limit(1)
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no organization")
    return membership.organization_id
