"""Covers the app's default startup path (no pre-populated app.state), as
opposed to the `app` fixture in conftest.py which substitutes in-process
fakes for everything. This exercises the real lifespan defaults: building
the tool registry from the bundled OpenAPI spec file, constructing a real
httpx.AsyncClient (never actually used to make a network call here), and a
real RateLimiter (backed by fakeredis, since AGENTGATE_REDIS_URL=memory://
in the test environment).
"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from app.main import create_app


async def test_health_endpoint_with_real_lifespan_defaults():
    application = create_app()
    async with application.router.lifespan_context(application):
        assert application.state.tool_registry is not None
        assert application.state.http_client is not None
        assert application.state.rate_limiter is not None

        transport = ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["service"] == "agentgate"
