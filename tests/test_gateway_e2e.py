"""End-to-end tests for the /v1/invoke gateway endpoint against the real
sample e-commerce API (wired in-process, no network) -- covering the full
auth -> policy -> rate-limit -> budget -> proxy -> audit-log pipeline.
"""
from __future__ import annotations

from tests.conftest import ADMIN_HEADERS, create_policy_rule, create_test_agent


async def _headers(agent):
    return {"X-API-Key": agent["api_key"]}


async def test_unknown_tool_is_rejected_and_audited(client):
    agent = await create_test_agent(client, name="unknown-tool-agent")
    resp = await client.post(
        "/v1/invoke",
        json={"tool": "nope_this_tool_does_not_exist", "arguments": {}},
        headers=await _headers(agent),
    )
    assert resp.status_code == 404
    audit_log_id = resp.json()["detail"]["audit_log_id"]

    logs = await client.get("/v1/audit", headers=await _headers(agent))
    assert any(log["id"] == audit_log_id for log in logs.json())
    entry = next(log for log in logs.json() if log["id"] == audit_log_id)
    assert entry["decision"] == "denied_unknown_tool"


async def test_tool_call_denied_by_policy_is_audited_as_denied(client):
    agent = await create_test_agent(client, name="policy-denied-agent")
    # No allow rule at all -> default deny.
    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent),
    )
    assert resp.status_code == 403
    entry_id = resp.json()["detail"]["audit_log_id"]

    log_resp = await client.get(f"/v1/audit/{entry_id}", headers=await _headers(agent))
    assert log_resp.status_code == 200
    assert log_resp.json()["decision"] == "denied_policy"


async def test_full_read_and_write_flow_against_sample_api(client):
    agent = await create_test_agent(client, name="full-flow-agent")
    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])
    await create_policy_rule(client, "create_order", "allow", agent_id=agent["id"])
    await create_policy_rule(client, "get_order", "allow", agent_id=agent["id"])

    products_resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {"category": "electronics"}},
        headers=await _headers(agent),
    )
    assert products_resp.status_code == 200
    products = products_resp.json()["body"]
    assert len(products) >= 1
    sku = products[0]["id"]

    order_resp = await client.post(
        "/v1/invoke",
        json={
            "tool": "create_order",
            "arguments": {
                "body": {
                    "items": [{"product_id": sku, "quantity": 1}],
                    "customer_email": "buyer@example.com",
                }
            },
        },
        headers=await _headers(agent),
    )
    assert order_resp.status_code == 200, order_resp.text
    order_body = order_resp.json()["body"]
    assert order_body["status"] == "confirmed"
    order_id = order_body["id"]

    fetch_resp = await client.post(
        "/v1/invoke",
        json={"tool": "get_order", "arguments": {"order_id": order_id}},
        headers=await _headers(agent),
    )
    assert fetch_resp.status_code == 200
    assert fetch_resp.json()["body"]["id"] == order_id


async def test_downstream_404_is_surfaced_as_allowed_call_with_404_status(client):
    agent = await create_test_agent(client, name="downstream-404-agent")
    await create_policy_rule(client, "get_product", "allow", agent_id=agent["id"])

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "get_product", "arguments": {"product_id": "sku-does-not-exist"}},
        headers=await _headers(agent),
    )
    # The gateway call itself succeeded (it was allowed and executed); the
    # *downstream* status code of 404 is carried through in the body.
    assert resp.status_code == 200
    assert resp.json()["status"] == 404


async def test_missing_required_argument_is_a_client_error_but_still_audited(client):
    agent = await create_test_agent(client, name="missing-arg-agent")
    await create_policy_rule(client, "get_product", "allow", agent_id=agent["id"])

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "get_product", "arguments": {}},
        headers=await _headers(agent),
    )
    assert resp.status_code == 400
    entry_id = resp.json()["detail"]["audit_log_id"]
    log_resp = await client.get(f"/v1/audit/{entry_id}", headers=await _headers(agent))
    assert log_resp.json()["error"] is not None


async def test_rate_limit_enforced_end_to_end(client):
    agent = await create_test_agent(client, name="rate-limited-agent", rate_limit_per_minute=2)
    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    for _ in range(2):
        resp = await client.post(
            "/v1/invoke",
            json={"tool": "list_products", "arguments": {}},
            headers=await _headers(agent),
        )
        assert resp.status_code == 200

    third = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent),
    )
    assert third.status_code == 429


async def test_budget_enforced_end_to_end(client):
    # A single empty-arguments call costs a little over 1 cent (flat base +
    # a tiny per-KB surcharge on the JSON payload); a 2-cent budget fits
    # exactly one call but not a second.
    agent = await create_test_agent(
        client, name="budget-limited-agent", daily_cost_budget_cents=2
    )
    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent),
    )
    assert resp.status_code == 200

    second = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent),
    )
    assert second.status_code == 402


async def test_non_json_downstream_response_is_carried_through_as_text(client):
    agent = await create_test_agent(client, name="plaintext-agent")
    await create_policy_rule(client, "ping", "allow", agent_id=agent["id"])

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "ping", "arguments": {}},
        headers=await _headers(agent),
    )
    assert resp.status_code == 200
    assert resp.json()["body"] == "pong"


async def test_allowed_tools_endpoint_reflects_policy(client):
    agent = await create_test_agent(client, name="visibility-agent")
    await create_policy_rule(client, "list_products", "allow", agent_id=agent["id"])

    all_tools = await client.get("/v1/tools", headers=await _headers(agent))
    allowed_tools = await client.get("/v1/tools/allowed", headers=await _headers(agent))

    all_names = {t["name"] for t in all_tools.json()}
    allowed_names = {t["name"] for t in allowed_tools.json()}

    assert "create_order" in all_names
    assert allowed_names == {"list_products"}


async def test_admin_can_view_all_agents_audit_logs(client):
    agent_a = await create_test_agent(client, name="agent-a-audit")
    agent_b = await create_test_agent(client, name="agent-b-audit")
    await create_policy_rule(client, "list_products", "allow", agent_id=agent_a["id"])
    await create_policy_rule(client, "list_products", "allow", agent_id=agent_b["id"])

    await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent_a),
    )
    await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent_b),
    )

    all_logs = await client.get("/v1/audit/admin/all", headers=ADMIN_HEADERS)
    assert all_logs.status_code == 200
    agent_ids = {log["agent_id"] for log in all_logs.json()}
    assert agent_a["id"] in agent_ids
    assert agent_b["id"] in agent_ids


async def test_agent_cannot_see_another_agents_audit_log(client):
    agent_a = await create_test_agent(client, name="isolated-a")
    agent_b = await create_test_agent(client, name="isolated-b")
    await create_policy_rule(client, "list_products", "allow", agent_id=agent_a["id"])

    resp = await client.post(
        "/v1/invoke",
        json={"tool": "list_products", "arguments": {}},
        headers=await _headers(agent_a),
    )
    log_id = resp.json()["audit_log_id"]

    cross_read = await client.get(f"/v1/audit/{log_id}", headers=await _headers(agent_b))
    assert cross_read.status_code == 404
