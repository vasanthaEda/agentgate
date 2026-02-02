"""Redis-backed rate limiting and cost-budget enforcement, scoped per agent+tool.

Two independent guards are enforced before any downstream call is made:

* **Rate limit** -- a fixed one-minute window counter, keyed by
  ``(agent, tool, minute-bucket)``. Protects against runaway request loops.
* **Cost budget** -- a rolling daily spend counter, keyed by
  ``(agent, tool, date)``. Protects against a cheap-looking tool being called
  so often it becomes expensive, or an agent burning through its allotment.

Both use a "reserve, then compensate on rejection" pattern so a denied call
never counts against the caller's quota. This isn't perfectly atomic under
extreme concurrency (a Lua script would be used for that in production, and
the hook point is ``RateLimiter._redis`` if you want to add one) but is
correct for the realistic case of one agent making sequential tool calls,
which is how LLM agent loops actually behave.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import settings

RATE_WINDOW_SECONDS = 60
BUDGET_WINDOW_SECONDS = 24 * 60 * 60

DEFAULT_TOOL_BASE_COST_CENTS = 1.0
DEFAULT_TOOL_PER_KB_COST_CENTS = 0.1


def estimate_cost_cents(arguments: dict) -> float:
    """A simple, deterministic stand-in for "LLM token cost" of a tool call:
    a flat per-call cost plus a small per-KB surcharge on the argument payload,
    mirroring how token-metered costs scale with request size.
    """
    payload_size = len(json.dumps(arguments, default=str).encode("utf-8"))
    kb = payload_size / 1024
    return round(DEFAULT_TOOL_BASE_COST_CENTS + kb * DEFAULT_TOOL_PER_KB_COST_CENTS, 4)


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    reason: str
    limit: int | float
    remaining: float


def _minute_bucket(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.strftime("%Y%m%d%H%M")


def _day_bucket(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.strftime("%Y%m%d")


def get_redis_client():
    """Build the redis-compatible async client from settings.

    ``memory://`` (the default) uses fakeredis -- a real, correct Redis
    reimplementation with zero network I/O, which is what keeps this project
    testable offline while still exercising real INCR/EXPIRE/TTL semantics.
    """
    if settings.redis_url.startswith("memory://"):
        import fakeredis.aioredis as fakeredis_aioredis

        return fakeredis_aioredis.FakeRedis(decode_responses=True)

    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(settings.redis_url, decode_responses=True)


class RateLimiter:
    """Per agent+tool rate limiting and cost-budget enforcement backed by Redis."""

    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis_client()

    async def check_rate_limit(
        self, agent_id: str, tool_name: str, limit_per_minute: int
    ) -> LimitDecision:
        key = f"agentgate:rl:{agent_id}:{tool_name}:{_minute_bucket()}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, RATE_WINDOW_SECONDS)

        if count > limit_per_minute:
            await self._redis.decr(key)  # don't let a denied call consume quota
            return LimitDecision(
                allowed=False,
                reason=(
                    f"rate limit exceeded for tool '{tool_name}': "
                    f"{limit_per_minute}/min"
                ),
                limit=limit_per_minute,
                remaining=0,
            )
        return LimitDecision(
            allowed=True,
            reason="within rate limit",
            limit=limit_per_minute,
            remaining=max(0, limit_per_minute - count),
        )

    async def check_and_reserve_budget(
        self, agent_id: str, tool_name: str, cost_cents: float, daily_budget_cents: float
    ) -> LimitDecision:
        key = f"agentgate:budget:{agent_id}:{tool_name}:{_day_bucket()}"
        new_total = await self._redis.incrbyfloat(key, cost_cents)
        ttl = await self._redis.ttl(key)
        if ttl is None or ttl < 0:
            await self._redis.expire(key, BUDGET_WINDOW_SECONDS)

        if new_total > daily_budget_cents:
            await self._redis.incrbyfloat(key, -cost_cents)  # compensate
            return LimitDecision(
                allowed=False,
                reason=(
                    f"daily cost budget exceeded for tool '{tool_name}': "
                    f"${daily_budget_cents / 100:.2f}/day"
                ),
                limit=daily_budget_cents,
                remaining=max(0.0, daily_budget_cents - (new_total - cost_cents)),
            )
        return LimitDecision(
            allowed=True,
            reason="within cost budget",
            limit=daily_budget_cents,
            remaining=max(0.0, daily_budget_cents - new_total),
        )

    async def current_usage(self, agent_id: str, tool_name: str) -> dict:
        rl_key = f"agentgate:rl:{agent_id}:{tool_name}:{_minute_bucket()}"
        budget_key = f"agentgate:budget:{agent_id}:{tool_name}:{_day_bucket()}"
        rl_count = await self._redis.get(rl_key)
        budget_spent = await self._redis.get(budget_key)
        return {
            "requests_this_minute": int(rl_count) if rl_count else 0,
            "cost_cents_today": float(budget_spent) if budget_spent else 0.0,
        }

    async def close(self):
        try:
            await self._redis.aclose()
        except AttributeError:
            pass
