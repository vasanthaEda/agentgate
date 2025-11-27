"""Shared pytest fixtures.

Everything here runs fully offline:
* Postgres -> an in-memory SQLite database (StaticPool keeps one connection
  alive so the in-memory schema survives across the async session's use).
* Redis -> fakeredis, a real (if in-process) reimplementation with correct
  INCR/EXPIRE/TTL semantics, so rate-limit/budget logic is genuinely exercised.
* The downstream "internal API" -> the real sample e-commerce FastAPI app,
  wired in-process via httpx's ASGI transport (no sockets, no Docker).
"""
from __future__ import annotations

import os

# Force a clean, isolated, offline configuration before any app module is
# imported -- app.config.Settings() reads the environment at import time.
os.environ["AGENTGATE_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["AGENTGATE_REDIS_URL"] = "memory://"
os.environ["AGENTGATE_ADMIN_API_KEY"] = "test-admin-key"
os.environ["AGENTGATE_DOWNSTREAM_BASE_URL"] = "http://sample-api.internal"
os.environ["AGENTGATE_DEFAULT_RATE_LIMIT_PER_MINUTE"] = "5"
os.environ["AGENTGATE_DEFAULT_DAILY_COST_BUDGET_CENTS"] = "10"

import fakeredis.aioredis as fakeredis_aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.openapi_tools import build_tool_registry  # noqa: E402
from app.rate_limit import RateLimiter  # noqa: E402
from sample_api.main import app as sample_api_app  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


@pytest_asyncio.fixture(autouse=True)
async def _reset_database():
    """Drop and recreate every table before each test for full isolation."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def app():
    """A fresh FastAPI app per test, wired to in-process fakes."""
    application = create_app()

    # Route "downstream" calls to the real sample API in-process (no network).
    application.state.http_client = httpx.AsyncClient(
        transport=ASGITransport(app=sample_api_app),
        base_url="http://sample-api.internal",
    )
    # Each test gets its own fakeredis instance so rate limit/budget state
    # never leaks between tests.
    application.state.rate_limiter = RateLimiter(
        redis_client=fakeredis_aioredis.FakeRedis(decode_responses=True)
    )
    application.state.tool_registry = build_tool_registry("demo/ecommerce_openapi.json")

    # ASGITransport doesn't invoke the ASGI lifespan protocol on its own, so
    # drive app startup/shutdown explicitly around the test.
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def create_test_agent(
    client: httpx.AsyncClient,
    name: str = "test-agent",
    rate_limit_per_minute: int | None = None,
    daily_cost_budget_cents: int | None = None,
) -> dict:
    resp = await client.post(
        "/v1/agents",
        json={
            "name": name,
            "rate_limit_per_minute": rate_limit_per_minute,
            "daily_cost_budget_cents": daily_cost_budget_cents,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_policy_rule(
    client: httpx.AsyncClient,
    tool_pattern: str,
    effect: str = "allow",
    agent_id: str | None = None,
    priority: int = 0,
) -> dict:
    resp = await client.post(
        "/v1/policies",
        json={
            "agent_id": agent_id,
            "tool_pattern": tool_pattern,
            "effect": effect,
            "priority": priority,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
