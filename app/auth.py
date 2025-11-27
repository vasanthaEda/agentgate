"""Per-agent authentication: long-lived API keys and short-lived JWTs minted from them.

Two ways for an agent to authenticate against the gateway:

1. ``X-API-Key: agtk_...`` -- the raw key issued at agent-creation time. The
   gateway only ever stores a SHA-256 hash of it.
2. ``Authorization: Bearer <jwt>`` -- a short-lived JWT obtained by exchanging
   an API key at ``POST /v1/auth/token``. Useful for agent runtimes that want
   to avoid holding the long-lived secret in every outbound call.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import Agent

API_KEY_PREFIX = "agtk_"


def generate_api_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_access_token(agent_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": agent_id, "exp": expire, "type": "agent_access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Return the agent_id encoded in the token, or raise HTTPException(401)."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        ) from exc
    if payload.get("type") != "agent_access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="wrong token type")
    agent_id = payload.get("sub")
    if not agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token missing subject"
        )
    return agent_id


async def authenticate_api_key(raw_key: str, db: AsyncSession) -> Agent:
    key_hash = hash_api_key(raw_key)
    result = await db.execute(select(Agent).where(Agent.api_key_hash == key_hash))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    if not agent.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="agent is deactivated")
    return agent


async def get_current_agent(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Agent:
    """Resolve the calling Agent from either an API key or a bearer JWT."""
    if x_api_key:
        return await authenticate_api_key(x_api_key, db)

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        agent_id = decode_access_token(token)
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown agent")
        if not agent.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="agent is deactivated"
            )
        return agent

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing credentials: supply X-API-Key or Authorization: Bearer <jwt>",
    )
