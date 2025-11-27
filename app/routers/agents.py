"""Admin endpoints for registering and managing agent identities."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import generate_api_key, hash_api_key
from app.db import get_db
from app.models import Agent
from app.routers.deps import require_admin
from app.schemas import AgentCreate, AgentCreated, AgentOut

router = APIRouter(prefix="/v1/agents", tags=["agents"], dependencies=[Depends(require_admin)])


@router.post("", response_model=AgentCreated, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentCreate, db: AsyncSession = Depends(get_db)) -> AgentCreated:
    existing = await db.execute(select(Agent).where(Agent.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"agent '{payload.name}' already exists")

    raw_key = generate_api_key()
    agent = Agent(
        name=payload.name,
        api_key_hash=hash_api_key(raw_key),
        rate_limit_per_minute=payload.rate_limit_per_minute,
        daily_cost_budget_cents=payload.daily_cost_budget_cents,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return AgentCreated(
        id=agent.id,
        name=agent.name,
        api_key=raw_key,
        rate_limit_per_minute=agent.rate_limit_per_minute,
        daily_cost_budget_cents=agent.daily_cost_budget_cents,
    )


@router.get("", response_model=list[AgentOut])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[Agent]:
    result = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> Agent:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.post("/{agent_id}/deactivate", response_model=AgentOut)
async def deactivate_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> Agent:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    agent.is_active = False
    await db.commit()
    await db.refresh(agent)
    return agent
