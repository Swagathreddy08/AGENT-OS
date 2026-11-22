from datetime import datetime, timedelta, timezone
import bcrypt
import hashlib
from jose import JWTError, jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    password_digest = hashlib.sha256(password.encode("utf-8")).digest()
    password_hash = bcrypt.hashpw(password_digest, bcrypt.gensalt(rounds=12)).decode("ascii")
    return f"sha256${password_hash}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        if password_hash.startswith("sha256$"):
            password_digest = hashlib.sha256(password.encode("utf-8")).digest()
            bcrypt_hash = password_hash.removeprefix("sha256$")
            return bcrypt.checkpw(password_digest, bcrypt_hash.encode("ascii"))
        # Verify bcrypt hashes created before the sha256 prefix was introduced.
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


def create_access_token(subject: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": subject, "exp": expires, "type": "access"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    return jwt.encode({"sub": subject, "exp": expires, "type": "refresh"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str = "access") -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    subject = payload.get("sub")
    if payload.get("type") != expected_type or not isinstance(subject, str):
        return None
    return subject
