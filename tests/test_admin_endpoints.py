"""Coverage for the admin-only agent/policy management endpoints, including
their not-found and require-admin edge cases.
"""
from __future__ import annotations

from tests.conftest import ADMIN_HEADERS, create_policy_rule, create_test_agent


async def test_get_agent_by_id(client):
    agent = await create_test_agent(client, name="get-by-id-agent")
    resp = await client.get(f"/v1/agents/{agent['id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["id"] == agent["id"]


async def test_get_agent_by_id_not_found(client):
    resp = await client.get("/v1/agents/does-not-exist", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_deactivate_agent_not_found(client):
    resp = await client.post("/v1/agents/does-not-exist/deactivate", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_list_agents_requires_admin(client):
    resp = await client.get("/v1/agents")
    assert resp.status_code == 401


async def test_policy_rule_for_unknown_agent_is_rejected(client):
    resp = await client.post(
        "/v1/policies",
        json={"agent_id": "no-such-agent", "tool_pattern": "get_product", "effect": "allow"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 404


async def test_list_policy_rules_global_and_scoped(client):
    agent = await create_test_agent(client, name="policy-list-agent")
    await create_policy_rule(client, "get_product", "allow")  # global
    await create_policy_rule(client, "create_order", "allow", agent_id=agent["id"])

    all_rules = await client.get("/v1/policies", headers=ADMIN_HEADERS)
    assert all_rules.status_code == 200
    assert len(all_rules.json()) == 2

    scoped = await client.get(
        "/v1/policies", params={"agent_id": agent["id"]}, headers=ADMIN_HEADERS
    )
    tool_patterns = {r["tool_pattern"] for r in scoped.json()}
    assert tool_patterns == {"get_product", "create_order"}


async def test_delete_policy_rule(client):
    rule = await create_policy_rule(client, "get_product", "allow")
    delete_resp = await client.delete(f"/v1/policies/{rule['id']}", headers=ADMIN_HEADERS)
    assert delete_resp.status_code == 204

    remaining = await client.get("/v1/policies", headers=ADMIN_HEADERS)
    assert all(r["id"] != rule["id"] for r in remaining.json())


async def test_delete_policy_rule_not_found(client):
    resp = await client.delete("/v1/policies/does-not-exist", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_policies_require_admin(client):
    resp = await client.get("/v1/policies")
    assert resp.status_code == 401
