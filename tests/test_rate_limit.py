"""Unit tests for the Redis-backed rate limiter and cost-budget enforcer.

Uses fakeredis directly (no network, no app/HTTP layer) to pin down exact
counting/expiry/compensation semantics.
"""
from __future__ import annotations

import fakeredis.aioredis as fakeredis_aioredis
import pytest

from app.rate_limit import RateLimiter, estimate_cost_cents


@pytest.fixture
def limiter():
    return RateLimiter(redis_client=fakeredis_aioredis.FakeRedis(decode_responses=True))


async def test_requests_within_limit_are_allowed(limiter):
    for _ in range(3):
        decision = await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
        assert decision.allowed is True


async def test_request_exceeding_limit_is_denied(limiter):
    for _ in range(3):
        await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
    decision = await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
    assert decision.allowed is False
    assert "rate limit exceeded" in decision.reason


async def test_denied_request_does_not_consume_quota(limiter):
    for _ in range(3):
        await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
    # This one is denied...
    await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
    usage = await limiter.current_usage("agent-1", "get_product")
    # ...so usage should still read 3, not 4.
    assert usage["requests_this_minute"] == 3


async def test_rate_limit_is_scoped_per_tool(limiter):
    for _ in range(3):
        await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
    # A different tool for the same agent has its own independent bucket.
    decision = await limiter.check_rate_limit("agent-1", "create_order", limit_per_minute=3)
    assert decision.allowed is True


async def test_rate_limit_is_scoped_per_agent(limiter):
    for _ in range(3):
        await limiter.check_rate_limit("agent-1", "get_product", limit_per_minute=3)
    # A different agent calling the same tool has its own independent bucket.
    decision = await limiter.check_rate_limit("agent-2", "get_product", limit_per_minute=3)
    assert decision.allowed is True


async def test_budget_within_limit_is_allowed(limiter):
    decision = await limiter.check_and_reserve_budget(
        "agent-1", "create_order", cost_cents=2.0, daily_budget_cents=10.0
    )
    assert decision.allowed is True
    assert decision.remaining == pytest.approx(8.0)


async def test_budget_exceeding_limit_is_denied_and_compensated(limiter):
    await limiter.check_and_reserve_budget(
        "agent-1", "create_order", cost_cents=9.0, daily_budget_cents=10.0
    )
    decision = await limiter.check_and_reserve_budget(
        "agent-1", "create_order", cost_cents=5.0, daily_budget_cents=10.0
    )
    assert decision.allowed is False
    assert "budget exceeded" in decision.reason

    # The rejected 5-cent charge must have been rolled back: usage should
    # still read 9.0, not 14.0.
    usage = await limiter.current_usage("agent-1", "create_order")
    assert usage["cost_cents_today"] == pytest.approx(9.0)


async def test_budget_is_scoped_per_agent_and_tool(limiter):
    await limiter.check_and_reserve_budget(
        "agent-1", "create_order", cost_cents=9.0, daily_budget_cents=10.0
    )
    other_tool = await limiter.check_and_reserve_budget(
        "agent-1", "cancel_order", cost_cents=9.0, daily_budget_cents=10.0
    )
    other_agent = await limiter.check_and_reserve_budget(
        "agent-2", "create_order", cost_cents=9.0, daily_budget_cents=10.0
    )
    assert other_tool.allowed is True
    assert other_agent.allowed is True


def test_cost_estimate_scales_with_payload_size():
    small = estimate_cost_cents({"id": "x"})
    large = estimate_cost_cents({"id": "x" * 5000})
    assert large > small


def test_cost_estimate_has_flat_base_cost_plus_small_payload_surcharge():
    # Base cost (1.0) plus a tiny per-KB surcharge on the ~2-byte "{}" payload.
    assert estimate_cost_cents({}) == pytest.approx(1.0, abs=0.01)
