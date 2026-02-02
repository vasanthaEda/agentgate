#!/usr/bin/env python3
"""A demo "LLM agent" driving a shopping task entirely through agentgate.

Run this against a live agentgate + sample e-commerce API (e.g. the
docker-compose stack, or `uvicorn app.main:app` + `uvicorn sample_api.main:app`
locally) to see the whole pipeline in action: agent registration, policy
scoping, a normal tool-call sequence, a policy denial, a rate-limit denial,
and an audit-log replay.

    docker compose up -d
    python demo/demo_agent.py

This script is intentionally synchronous and narrated with print()s -- it's
meant to be read, not just run.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

GATEWAY_URL = os.environ.get("AGENTGATE_URL", "http://localhost:8000")
ADMIN_KEY = os.environ.get("AGENTGATE_ADMIN_API_KEY", "dev-admin-key-change-me")


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _admin_headers() -> dict:
    return {"X-Admin-Key": ADMIN_KEY}


def register_agent(client: httpx.Client, name: str) -> dict:
    resp = client.post(
        "/v1/agents",
        json={"name": name, "rate_limit_per_minute": 3, "daily_cost_budget_cents": 50},
        headers=_admin_headers(),
    )
    resp.raise_for_status()
    agent = resp.json()
    print(f"Registered agent '{name}' (id={agent['id']})")
    print(f"  API key: {agent['api_key']}")
    return agent


def grant_policy(client: httpx.Client, agent_id: str, pattern: str, effect: str) -> None:
    resp = client.post(
        "/v1/policies",
        json={"agent_id": agent_id, "tool_pattern": pattern, "effect": effect, "priority": 0},
        headers=_admin_headers(),
    )
    resp.raise_for_status()
    print(f"  policy: {effect:5s} -> {pattern}")


def invoke(client: httpx.Client, api_key: str, tool: str, arguments: dict) -> httpx.Response:
    return client.post(
        "/v1/invoke",
        json={"tool": tool, "arguments": arguments},
        headers={"X-API-Key": api_key},
    )


def main() -> int:
    with httpx.Client(base_url=GATEWAY_URL, timeout=10.0) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Cannot reach agentgate at {GATEWAY_URL}: {exc}")
            print("Start it first, e.g.: docker compose up -d")
            return 1

        _section("1. Register the agent and scope its permissions")
        agent = register_agent(client, name=f"demo-shopping-agent-{int(time.time())}")
        for pattern, effect in [
            ("list_products", "allow"),
            ("get_product", "allow"),
            ("create_order", "allow"),
            ("get_order", "allow"),
            ("cancel_order", "deny"),  # deliberately withheld from this agent
        ]:
            grant_policy(client, agent["id"], pattern, effect)

        api_key = agent["api_key"]

        _section("2. Fetch the tool schemas this agent is actually allowed to use")
        tools_resp = client.get("/v1/tools/allowed", headers={"X-API-Key": api_key})
        tools_resp.raise_for_status()
        tool_schemas = tools_resp.json()
        for schema in tool_schemas:
            print(f"  - {schema['name']}: {schema['description']}")
        print(f"  ({len(tool_schemas)} tools -- this is what you'd hand an LLM as its")
        print("   function-calling tool list; cancel_order is invisible to it.)")

        _section("3. Agent turn: browse products")
        resp = invoke(client, api_key, "list_products", {"category": "electronics"})
        resp.raise_for_status()
        result = resp.json()
        products = result["body"]
        print(f"  list_products -> {len(products)} product(s), trace_id={result['trace_id']}")
        sku = products[0]["id"]
        print(f"  picking {products[0]['name']} ({sku})")

        _section("4. Agent turn: place an order")
        order_body = {
            "items": [{"product_id": sku, "quantity": 1}],
            "customer_email": "agent-demo@example.com",
        }
        resp = invoke(client, api_key, "create_order", {"body": order_body})
        resp.raise_for_status()
        order = resp.json()["body"]
        print(f"  create_order -> order {order['id']} confirmed, total={order['total_cents']}c")
        order_id = order["id"]
        create_order_audit_log_id = resp.json()["audit_log_id"]

        _section("5. Agent turn: confirm the order (read-back)")
        resp = invoke(client, api_key, "get_order", {"order_id": order_id})
        resp.raise_for_status()
        print(f"  get_order -> status={resp.json()['body']['status']}")

        _section("6. Agent tries something it was never granted: cancel_order")
        resp = invoke(client, api_key, "cancel_order", {"order_id": order_id})
        print(f"  cancel_order -> HTTP {resp.status_code} ({resp.json()['detail']['message']})")
        assert resp.status_code == 403, "policy engine should have denied this"

        _section("7. Agent runs away in a loop -- rate limiting kicks in")
        for i in range(5):
            resp = invoke(client, api_key, "list_products", {})
            print(f"  call #{i + 1}: HTTP {resp.status_code}")
            if resp.status_code == 429:
                print(f"  -> rate limit tripped: {resp.json()['detail']['message']}")
                break

        _section("8. Replay the order-creation call from the audit log")
        replay_resp = client.post(
            f"/v1/audit/{create_order_audit_log_id}/replay",
            json={},
            headers={"X-API-Key": api_key},
        )
        print(f"  replay -> HTTP {replay_resp.status_code}")
        if replay_resp.status_code == 200:
            replay_body = replay_resp.json()
            print(f"  new audit_log_id={replay_body['audit_log_id']}")
            print(f"  new order id: {replay_body['body']['id']} (a real second order was placed)")
        else:
            print(f"  (denied: {replay_resp.json()['detail']})")

        _section("9. Full audit trail for this agent")
        audit_resp = client.get("/v1/audit", headers={"X-API-Key": api_key}, params={"limit": 20})
        audit_resp.raise_for_status()
        for entry in reversed(audit_resp.json()):
            reason = entry["decision_reason"][:50]
            when, tool, decision = entry["created_at"], entry["tool_name"], entry["decision"]
            print(f"  [{when}] {tool:15s} {decision:20s} {reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
