"""Shared FastAPI dependencies: admin auth and pulling shared objects off app.state."""
from __future__ import annotations

import httpx
from fastapi import Header, HTTPException, Request, status

from app.config import settings
from app.openapi_tools import ToolRegistry
from app.rate_limit import RateLimiter


def require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")


def get_tool_registry(request: Request) -> ToolRegistry:
    return request.app.state.tool_registry


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter
