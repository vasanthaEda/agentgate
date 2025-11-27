"""ORM models: agents (callers), policy rules, and the audit log."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Agent(Base):
    """A registered LLM agent identity: it authenticates with an API key or JWT."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Per-agent overrides of the global defaults; None means "use the default".
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_cost_budget_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    policy_rules: Mapped[list[PolicyRule]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="agent")


class PolicyRule(Base):
    """An allow/deny rule matched against a tool name (glob-style pattern).

    A rule with agent_id=None is a *global* rule applied to every agent unless
    a more specific (higher priority, or agent-scoped) rule overrides it.
    """

    __tablename__ = "policy_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tool_pattern: Mapped[str] = mapped_column(String(200))
    effect: Mapped[str] = mapped_column(String(10))  # "allow" | "deny"
    priority: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent: Mapped[Agent | None] = relationship(back_populates="policy_rules")


class AuditLog(Base):
    """Immutable record of every tool-call attempt (allowed, denied, or errored)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(200), index=True)
    http_method: Mapped[str] = mapped_column(String(10))
    http_path: Mapped[str] = mapped_column(String(500))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)

    # "allowed" | "denied_policy" | "denied_rate_limit" | "denied_budget" | "denied_unknown_tool"
    decision: Mapped[str] = mapped_column(String(20))
    decision_reason: Mapped[str] = mapped_column(String(500))

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cents: Mapped[float] = mapped_column(Float, default=0.0)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    replay_of_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audit_logs.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    agent: Mapped[Agent] = relationship(back_populates="audit_logs")
