"""Tests for the audit log's replay endpoint."""
from __future__ import annotations

from tests.conftest import create_policy_rule, create_test_agent


async def _headers(agent):
    return {"X-API-Key": agent["api_key"]}


async def test_replay_reexecutes_the_same_tool_call(client):
    agent = await create_test_agent(client, name="replay-agent", rate_limit_per_minute=100)
    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    original = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {"category": "electronics"}},
        headers=await _headers(agent),
    )
    assert original.status_code == 200
    log_id = original.json()["audit_log_id"]

    replay = await client.post(
        f"/v1/audit/{log_id}/replay", json={}, headers=await _headers(agent)
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["tool"] == "list_products"
    assert replay.json()["body"] == original.json()["body"]

    logs = await client.get("/v1/audit", headers=await _headers(agent))
    replay_entries = [log for log in logs.json() if log["id"] == replay.json()["audit_log_id"]]
    assert len(replay_entries) == 1
    assert replay_entries[0]["replay_of_id"] == log_id


async def test_replay_honors_current_policy_even_if_it_changed(client):
    agent = await create_test_agent(client, name="policy-changed-agent")
    rule = await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    original = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent),
    )
    assert original.status_code == 200
    log_id = original.json()["audit_log_id"]

    # Revoke access after the fact.
    delete_resp = await client.delete(
        f"/v1/policies/{rule['id']}", headers={"X-Admin-Key": "test-admin-key"}
    )
    assert delete_resp.status_code == 204

    replay = await client.post(
        f"/v1/audit/{log_id}/replay", json={}, headers=await _headers(agent)
    )
    assert replay.status_code == 403


async def test_replay_can_override_arguments(client):
    agent = await create_test_agent(client, name="override-agent", rate_limit_per_minute=100)
    await create_policy_rule(client, "get_product", "allow", agent_id=agent["id"])

    original = await client.post(
        "/v1/invoke",
        json={"tool": "get_product", "arguments": {"product_id": "sku-001"}},
        headers=await _headers(agent),
    )
    assert original.status_code == 200
    log_id = original.json()["audit_log_id"]

    replay = await client.post(
        f"/v1/audit/{log_id}/replay",
        json={"override_arguments": {"product_id": "sku-002"}},
        headers=await _headers(agent),
    )
    assert replay.status_code == 200
    assert replay.json()["body"]["id"] == "sku-002"


async def test_replaying_someone_elses_log_is_not_found(client):
    agent_a = await create_test_agent(client, name="owner-agent")
    agent_b = await create_test_agent(client, name="intruder-agent")
    await create_policy_rule(client, "list_products", "allow", agent_id=agent_a["id"])

    original = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent_a),
    )
    log_id = original.json()["audit_log_id"]

    replay = await client.post(
        f"/v1/audit/{log_id}/replay", json={}, headers=await _headers(agent_b)
    )
    assert replay.status_code == 404


async def test_replay_of_unknown_log_id_is_not_found(client):
    agent = await create_test_agent(client, name="unknown-log-agent")
    replay = await client.post(
        "/v1/audit/does-not-exist/replay", json={}, headers=await _headers(agent)
    )
    assert replay.status_code == 404
