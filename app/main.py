"""FastAPI application factory and lifespan wiring for agentgate."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.openapi_tools import build_tool_registry
from app.rate_limit import RateLimiter
from app.routers import agents, audit, auth_router, gateway, policies, tools
from app.tracing import setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Tests (and alternate deployments) may pre-populate these on app.state
    # before startup runs; only fill in defaults for what's still missing.
    if getattr(app.state, "tool_registry", None) is None:
        app.state.tool_registry = build_tool_registry(settings.openapi_spec_path)

    if getattr(app.state, "http_client", None) is None:
        app.state.http_client = httpx.AsyncClient(
            base_url=settings.downstream_base_url,
            timeout=settings.downstream_timeout_seconds,
        )

    if getattr(app.state, "rate_limiter", None) is None:
        app.state.rate_limiter = RateLimiter()

    app.state.tracer = setup_tracing()

    yield

    await app.state.http_client.aclose()
    await app.state.rate_limiter.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="agentgate",
        description=(
            "A policy-and-budget-enforcing gateway between LLM agents and internal REST APIs."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(auth_router.router)
    app.include_router(agents.router)
    app.include_router(policies.router)
    app.include_router(tools.router)
    app.include_router(gateway.router)
    app.include_router(audit.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "service": "agentgate", "environment": settings.environment}

    return app


app = create_app()
