"""Query the audit trail and replay a past tool call.

Replay re-runs the exact same tool + arguments through ``perform_invoke``,
so it is re-evaluated against the *current* policy rules, rate limits, and
budget -- exactly what you want when investigating "did this agent action
actually happen, and what would happen if we ran it again right now."
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import get_audit_log, list_audit_logs
from app.auth import get_current_agent
from app.db import get_db
from app.models import Agent
from app.openapi_tools import ToolRegistry
from app.rate_limit import RateLimiter
from app.routers.deps import get_http_client, get_rate_limiter, get_tool_registry, require_admin
from app.routers.gateway import perform_invoke
from app.schemas import AuditLogOut, InvokeResponse, ReplayRequest

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
async def list_my_audit_logs(
    limit: int = 100,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> list:
    return await list_audit_logs(db, agent_id=agent.id, limit=limit)


@router.get("/admin/all", response_model=list[AuditLogOut], dependencies=[Depends(require_admin)])
async def list_all_audit_logs(limit: int = 100, db: AsyncSession = Depends(get_db)) -> list:
    """Cross-agent view of the full audit trail (admin only)."""
    return await list_audit_logs(db, agent_id=None, limit=limit)


@router.get("/{log_id}", response_model=AuditLogOut)
async def get_one_audit_log(
    log_id: str,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    entry = await get_audit_log(db, log_id)
    if entry is None or entry.agent_id != agent.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audit log not found")
    return entry


@router.post("/{log_id}/replay", response_model=InvokeResponse)
async def replay_audit_log(
    log_id: str,
    payload: ReplayRequest = ReplayRequest(),
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> InvokeResponse:
    entry = await get_audit_log(db, log_id)
    if entry is None or entry.agent_id != agent.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audit log not found")

    arguments = (
        payload.override_arguments if payload.override_arguments is not None else entry.arguments
    )

    return await perform_invoke(
        agent=agent,
        tool_name=entry.tool_name,
        arguments=arguments,
        db=db,
        registry=registry,
        http_client=http_client,
        limiter=limiter,
        replay_of_id=entry.id,
    )
