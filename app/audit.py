"""Audit-log persistence: every tool-call attempt is recorded, allowed or not."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def write_audit_log(
    db: AsyncSession,
    *,
    agent_id: str,
    tool_name: str,
    http_method: str,
    http_path: str,
    arguments: dict,
    decision: str,
    decision_reason: str,
    response_status: int | None = None,
    response_body: Any | None = None,
    error: str | None = None,
    latency_ms: float = 0.0,
    cost_cents: float = 0.0,
    trace_id: str | None = None,
    replay_of_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        agent_id=agent_id,
        tool_name=tool_name,
        http_method=http_method,
        http_path=http_path,
        arguments=arguments,
        decision=decision,
        decision_reason=decision_reason,
        response_status=response_status,
        response_body=response_body,
        error=error,
        latency_ms=latency_ms,
        cost_cents=cost_cents,
        trace_id=trace_id,
        replay_of_id=replay_of_id,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_audit_log(db: AsyncSession, log_id: str) -> AuditLog | None:
    result = await db.execute(select(AuditLog).where(AuditLog.id == log_id))
    return result.scalar_one_or_none()


async def list_audit_logs(
    db: AsyncSession, *, agent_id: str | None = None, limit: int = 100
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if agent_id is not None:
        stmt = stmt.where(AuditLog.agent_id == agent_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())
