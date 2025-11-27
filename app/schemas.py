"""Pydantic request/response schemas for the gateway's public API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rate_limit_per_minute: int | None = None
    daily_cost_budget_cents: int | None = None


class AgentCreated(BaseModel):
    id: str
    name: str
    api_key: str  # returned exactly once, at creation time
    rate_limit_per_minute: int | None
    daily_cost_budget_cents: int | None


class AgentOut(BaseModel):
    id: str
    name: str
    is_active: bool
    rate_limit_per_minute: int | None
    daily_cost_budget_cents: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Policy rules
# ---------------------------------------------------------------------------
class PolicyRuleCreate(BaseModel):
    agent_id: str | None = None
    tool_pattern: str = Field(min_length=1, max_length=200)
    effect: Literal["allow", "deny"]
    priority: int = 0
    description: str | None = None


class PolicyRuleOut(BaseModel):
    id: str
    agent_id: str | None
    tool_pattern: str
    effect: str
    priority: int
    description: str | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Tool schemas (OpenAPI-derived, function-calling format)
# ---------------------------------------------------------------------------
class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    # internal routing metadata, harmless to expose to the agent
    method: str
    path: str


# ---------------------------------------------------------------------------
# Gateway invocation
# ---------------------------------------------------------------------------
class InvokeRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    audit_log_id: str
    tool: str
    status: int
    body: Any
    latency_ms: float
    cost_cents: float
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
class AuditLogOut(BaseModel):
    id: str
    agent_id: str
    tool_name: str
    http_method: str
    http_path: str
    arguments: dict[str, Any]
    decision: str
    decision_reason: str
    response_status: int | None
    response_body: Any | None
    error: str | None
    latency_ms: float
    cost_cents: float
    trace_id: str | None
    replay_of_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReplayRequest(BaseModel):
    override_arguments: dict[str, Any] | None = None
