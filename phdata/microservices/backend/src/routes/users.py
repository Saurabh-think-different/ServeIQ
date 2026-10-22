"""Account registration, bearer login, and authenticated user directory."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from src.models import User
from src.services.store import get_db

router = APIRouter(prefix="/users", tags=["users"])
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if value.count("@") != 1 or not all(value.split("@")) or any(c.isspace() for c in value):
            raise ValueError("Invalid email address")
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    created_at: datetime


class UserSummary(BaseModel):
    id: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.get("", response_model=list[UserSummary])
def list_users(db: DB, user: CurrentUser):
    return db.execute(select(User.id, User.email).order_by(User.email)).mappings().all()


@router.post("/register", response_model=UserResponse, status_code=201)
def register(body: Credentials, db: DB):
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Account already exists") from None
    return user


@router.post("/login", response_model=TokenResponse)
def login(body: Credentials, db: DB):
    user = db.scalar(select(User).where(User.email == body.email))
    valid = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not valid:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser):
    return user
