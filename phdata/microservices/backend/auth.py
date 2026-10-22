"""Argon2 passwords and signed, expiring bearer credentials."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy.orm import Session

from config import settings
from src.models import User
from src.services.store import get_db

_passwords = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = _passwords.hash("dummy-credential-for-timing-equalization")
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _passwords.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _passwords.verify(password, password_hash)
    except (ValueError, TypeError, UnknownHashError):
        return False


def _secret() -> str:
    if len(settings.jwt_secret.strip()) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return settings.jwt_secret


def create_access_token(subject: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": subject, "iat": now, "exp": now + timedelta(minutes=settings.token_expire_minutes)},
        _secret(),
        algorithm=settings.jwt_algorithm,
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(
            credentials.credentials,
            _secret(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "iat", "exp"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise unauthorized
    except jwt.InvalidTokenError:
        raise unauthorized from None
    user = db.get(User, subject)
    if user is None:
        raise unauthorized
    return user
