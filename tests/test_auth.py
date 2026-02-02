"""Auth edge cases: API keys, JWT exchange, deactivation, admin gating."""
from __future__ import annotations

from tests.conftest import ADMIN_HEADERS, create_test_agent


async def test_create_agent_requires_admin_key(client):
    resp = await client.post("/v1/agents", json={"name": "no-admin"})
    assert resp.status_code == 401


async def test_create_agent_returns_api_key_once(client):
    agent = await create_test_agent(client, name="agent-a")
    assert agent["api_key"].startswith("agtk_")
    assert agent["name"] == "agent-a"

    listing = await client.get("/v1/agents", headers=ADMIN_HEADERS)
    assert listing.status_code == 200
    names = [a["name"] for a in listing.json()]
    assert "agent-a" in names
    # the listing endpoint must never leak the raw key
    assert all("api_key" not in a for a in listing.json())


async def test_duplicate_agent_name_rejected(client):
    await create_test_agent(client, name="dup-agent")
    resp = await client.post(
        "/v1/agents", json={"name": "dup-agent"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409


async def test_invoke_without_credentials_is_rejected(client):
    resp = await client.post("/v1/invoke", json={"tool": "list_products", "arguments": {}})
    assert resp.status_code == 401


async def test_invoke_with_bad_api_key_is_rejected(client):
    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"X-API-Key": "agtk_totally-invalid"},
    )
    assert resp.status_code == 401


async def test_invoke_with_valid_api_key_succeeds(client):
    agent = await create_test_agent(client, name="valid-agent")
    from tests.conftest import create_policy_rule

    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == 200
    assert isinstance(body["body"], list)


async def test_jwt_token_exchange_and_use(client):
    agent = await create_test_agent(client, name="jwt-agent")
    from tests.conftest import create_policy_rule

    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    token_resp = await client.post("/v1/auth/token", json={"api_key": agent["api_key"]})
    assert token_resp.status_code == 200, token_resp.text
    token = token_resp.json()["access_token"]

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def test_jwt_exchange_rejects_bad_api_key(client):
    resp = await client.post("/v1/auth/token", json={"api_key": "agtk_nope"})
    assert resp.status_code == 401


async def test_malformed_jwt_is_rejected(client):
    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert resp.status_code == 401


async def test_deactivated_agent_cannot_invoke(client):
    agent = await create_test_agent(client, name="deactivate-me")
    from tests.conftest import create_policy_rule

    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    deactivate_resp = await client.post(
        f"/v1/agents/{agent['id']}/deactivate", headers=ADMIN_HEADERS
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert resp.status_code == 403


async def test_jwt_for_nonexistent_agent_is_rejected(client):
    from app.auth import create_access_token

    token = create_access_token("agent-id-that-was-never-created")
    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


async def test_deactivated_agent_jwt_also_rejected(client):
    agent = await create_test_agent(client, name="deactivate-jwt")
    token_resp = await client.post("/v1/auth/token", json={"api_key": agent["api_key"]})
    token = token_resp.json()["access_token"]

    await client.post(f"/v1/agents/{agent['id']}/deactivate", headers=ADMIN_HEADERS)

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
