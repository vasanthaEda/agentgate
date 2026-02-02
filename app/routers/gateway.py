"""The core gateway logic: authenticate, police, budget, proxy, and audit
a single agent tool call -- all under one OpenTelemetry trace.

``perform_invoke`` is the shared engine used both by the live ``/v1/invoke``
endpoint and by the audit-log ``/v1/audit/{id}/replay`` endpoint, so a replay
goes through exactly the same policy/rate-limit/budget gates as a live call.
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit_log
from app.auth import get_current_agent
from app.config import settings
from app.db import get_db
from app.models import Agent, PolicyRule
from app.openapi_tools import ToolRegistry
from app.policy import evaluate
from app.proxy import ToolCallError, execute_tool_call
from app.rate_limit import RateLimiter, estimate_cost_cents
from app.routers.deps import get_http_client, get_rate_limiter, get_tool_registry
from app.schemas import InvokeRequest, InvokeResponse
from app.tracing import get_tracer

router = APIRouter(prefix="/v1", tags=["gateway"])


async def perform_invoke(
    *,
    agent: Agent,
    tool_name: str,
    arguments: dict,
    db: AsyncSession,
    registry: ToolRegistry,
    http_client: httpx.AsyncClient,
    limiter: RateLimiter,
    replay_of_id: str | None = None,
) -> InvokeResponse:
    tracer = get_tracer()
    start = time.perf_counter()

    with tracer.start_as_current_span("agentgate.invoke") as root_span:
        root_span.set_attribute("agentgate.agent_id", agent.id)
        root_span.set_attribute("agentgate.agent_name", agent.name)
        root_span.set_attribute("agentgate.tool", tool_name)
        if replay_of_id:
            root_span.set_attribute("agentgate.replay_of", replay_of_id)
        trace_id = format(root_span.get_span_context().trace_id, "032x")

        route = registry.get_route(tool_name)
        if route is None:
            entry = await write_audit_log(
                db,
                agent_id=agent.id,
                tool_name=tool_name,
                http_method="",
                http_path="",
                arguments=arguments,
                decision="denied_unknown_tool",
                decision_reason=f"no such tool '{tool_name}'",
                trace_id=trace_id,
                replay_of_id=replay_of_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"message": f"unknown tool '{tool_name}'", "audit_log_id": entry.id},
            )

        # --- policy check --------------------------------------------------
        with tracer.start_as_current_span("agentgate.policy_check") as span:
            result = await db.execute(
                select(PolicyRule).where(
                    (PolicyRule.agent_id == agent.id) | (PolicyRule.agent_id.is_(None))
                )
            )
            rules = list(result.scalars().all())
            decision = evaluate(rules, tool_name)
            span.set_attribute("agentgate.policy_allowed", decision.allowed)

        if not decision.allowed:
            entry = await write_audit_log(
                db,
                agent_id=agent.id,
                tool_name=tool_name,
                http_method=route.method,
                http_path=route.path,
                arguments=arguments,
                decision="denied_policy",
                decision_reason=decision.reason,
                trace_id=trace_id,
                replay_of_id=replay_of_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": decision.reason, "audit_log_id": entry.id},
            )

        # --- rate limit ------------------------------------------------
        rate_limit = agent.rate_limit_per_minute or settings.default_rate_limit_per_minute
        with tracer.start_as_current_span("agentgate.rate_limit_check") as span:
            rl_decision = await limiter.check_rate_limit(agent.id, tool_name, rate_limit)
            span.set_attribute("agentgate.rate_limit_allowed", rl_decision.allowed)

        if not rl_decision.allowed:
            entry = await write_audit_log(
                db,
                agent_id=agent.id,
                tool_name=tool_name,
                http_method=route.method,
                http_path=route.path,
                arguments=arguments,
                decision="denied_rate_limit",
                decision_reason=rl_decision.reason,
                trace_id=trace_id,
                replay_of_id=replay_of_id,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": rl_decision.reason, "audit_log_id": entry.id},
            )

        # --- cost budget -------------------------------------------------
        cost_cents = estimate_cost_cents(arguments)
        daily_budget = agent.daily_cost_budget_cents or settings.default_daily_cost_budget_cents
        with tracer.start_as_current_span("agentgate.budget_check") as span:
            budget_decision = await limiter.check_and_reserve_budget(
                agent.id, tool_name, cost_cents, daily_budget
            )
            span.set_attribute("agentgate.budget_allowed", budget_decision.allowed)

        if not budget_decision.allowed:
            entry = await write_audit_log(
                db,
                agent_id=agent.id,
                tool_name=tool_name,
                http_method=route.method,
                http_path=route.path,
                arguments=arguments,
                decision="denied_budget",
                decision_reason=budget_decision.reason,
                trace_id=trace_id,
                replay_of_id=replay_of_id,
            )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"message": budget_decision.reason, "audit_log_id": entry.id},
            )

        # --- downstream call ----------------------------------------------
        with tracer.start_as_current_span("agentgate.downstream_call") as span:
            span.set_attribute("http.method", route.method)
            span.set_attribute("http.route", route.path)
            try:
                response = await execute_tool_call(
                    http_client,
                    route,
                    arguments,
                    settings.downstream_base_url,
                    timeout=settings.downstream_timeout_seconds,
                )
                latency_ms = (time.perf_counter() - start) * 1000
                try:
                    response_body = response.json()
                except ValueError:
                    response_body = response.text
                entry = await write_audit_log(
                    db,
                    agent_id=agent.id,
                    tool_name=tool_name,
                    http_method=route.method,
                    http_path=route.path,
                    arguments=arguments,
                    decision="allowed",
                    decision_reason=decision.reason,
                    response_status=response.status_code,
                    response_body=response_body,
                    latency_ms=latency_ms,
                    cost_cents=cost_cents,
                    trace_id=trace_id,
                    replay_of_id=replay_of_id,
                )
                return InvokeResponse(
                    audit_log_id=entry.id,
                    tool=tool_name,
                    status=response.status_code,
                    body=response_body,
                    latency_ms=latency_ms,
                    cost_cents=cost_cents,
                    trace_id=trace_id,
                )
            except ToolCallError as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                span.record_exception(exc)
                entry = await write_audit_log(
                    db,
                    agent_id=agent.id,
                    tool_name=tool_name,
                    http_method=route.method,
                    http_path=route.path,
                    arguments=arguments,
                    decision="allowed",
                    decision_reason=decision.reason,
                    error=str(exc),
                    latency_ms=latency_ms,
                    cost_cents=cost_cents,
                    trace_id=trace_id,
                    replay_of_id=replay_of_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": str(exc), "audit_log_id": entry.id},
                ) from exc


@router.post("/invoke", response_model=InvokeResponse)
async def invoke_tool(
    payload: InvokeRequest,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> InvokeResponse:
    return await perform_invoke(
        agent=agent,
        tool_name=payload.tool,
        arguments=payload.arguments,
        db=db,
        registry=registry,
        http_client=http_client,
        limiter=limiter,
    )
