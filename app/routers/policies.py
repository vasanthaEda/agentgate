"""Admin endpoints for managing the allow/deny policy rule set."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Agent, PolicyRule
from app.routers.deps import require_admin
from app.schemas import PolicyRuleCreate, PolicyRuleOut

router = APIRouter(prefix="/v1/policies", tags=["policies"], dependencies=[Depends(require_admin)])


@router.post("", response_model=PolicyRuleOut, status_code=status.HTTP_201_CREATED)
async def create_policy_rule(
    payload: PolicyRuleCreate, db: AsyncSession = Depends(get_db)
) -> PolicyRule:
    if payload.agent_id is not None:
        result = await db.execute(select(Agent).where(Agent.id == payload.agent_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="agent not found")

    rule = PolicyRule(
        agent_id=payload.agent_id,
        tool_pattern=payload.tool_pattern,
        effect=payload.effect,
        priority=payload.priority,
        description=payload.description,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.get("", response_model=list[PolicyRuleOut])
async def list_policy_rules(
    agent_id: str | None = None, db: AsyncSession = Depends(get_db)
) -> list[PolicyRule]:
    stmt = select(PolicyRule).order_by(PolicyRule.priority.desc())
    if agent_id is not None:
        stmt = stmt.where((PolicyRule.agent_id == agent_id) | (PolicyRule.agent_id.is_(None)))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_policy_rule(rule_id: str, db: AsyncSession = Depends(get_db)) -> None:
    result = await db.execute(select(PolicyRule).where(PolicyRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="policy rule not found")
    await db.delete(rule)
    await db.commit()
