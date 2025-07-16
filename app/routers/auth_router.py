"""Token exchange: swap a long-lived API key for a short-lived JWT."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import authenticate_api_key, create_access_token
from app.config import settings
from app.db import get_db

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


@router.post("/token", response_model=TokenResponse)
async def issue_token(payload: TokenRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    agent = await authenticate_api_key(payload.api_key, db)
    token = create_access_token(agent.id)
    return TokenResponse(access_token=token, expires_in_minutes=settings.jwt_expire_minutes)
