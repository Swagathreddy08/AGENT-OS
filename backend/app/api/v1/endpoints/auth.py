from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.models.entities import Organization, OrganizationMember, User


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user_id = uuid4()
    user = User(id=user_id, email=str(payload.email).lower(), password_hash=hash_password(payload.password))
    organization = Organization(name=f"{payload.email}'s workspace", slug=f"user-{user.id.hex}")
    db.add_all([user, organization])
    try:
        await db.flush()
        db.add(OrganizationMember(organization_id=organization.id, user_id=user.id, role="owner"))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists") from exc
    subject = str(user.id)
    return TokenResponse(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    subject = str(user.id)
    return TokenResponse(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    token = payload.refresh_token
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token") from exc
    subject = claims.get("sub")
    if claims.get("type") != "refresh" or not isinstance(subject, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token subject") from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is unavailable")
    return TokenResponse(access_token=create_access_token(subject), refresh_token=create_refresh_token(subject))
