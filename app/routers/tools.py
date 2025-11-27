"""Expose the OpenAPI-derived tool catalog to authenticated agents."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_agent
from app.db import get_db
from app.models import Agent, PolicyRule
from app.policy import evaluate
from app.routers.deps import get_tool_registry
from app.schemas import ToolSchema

router = APIRouter(prefix="/v1/tools", tags=["tools"])


@router.get("", response_model=list[ToolSchema])
async def list_all_tools(registry=Depends(get_tool_registry)) -> list[dict]:
    """The full tool catalog derived from the ingested OpenAPI spec, unfiltered."""
    return registry.list_schemas()


@router.get("/allowed", response_model=list[ToolSchema])
async def list_allowed_tools(
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
    registry=Depends(get_tool_registry),
) -> list[dict]:
    """Only the tools this agent's policy rules currently permit.

    This is what you'd actually hand an LLM as its function-calling tool
    list -- it can only ever be offered tools it's allowed to invoke.
    """
    result = await db.execute(
        select(PolicyRule).where(
            (PolicyRule.agent_id == agent.id) | (PolicyRule.agent_id.is_(None))
        )
    )
    rules = list(result.scalars().all())

    allowed = []
    for schema in registry.list_schemas():
        decision = evaluate(rules, schema["name"])
        if decision.allowed:
            allowed.append(schema)
    return allowed
