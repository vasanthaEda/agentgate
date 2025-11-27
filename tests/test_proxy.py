"""Unit tests for translating a tool call into an HTTP request (app.proxy)."""
from __future__ import annotations

import pytest

from app.openapi_tools import ToolRoute
from app.proxy import ToolCallError, build_request


def test_build_request_substitutes_path_param():
    route = ToolRoute(
        name="get_widget",
        method="GET",
        path="/widgets/{widget_id}",
        path_param_names=["widget_id"],
    )
    method, url, params, body = build_request(route, {"widget_id": "abc-1"}, "http://api.internal")
    assert method == "GET"
    assert url == "http://api.internal/widgets/abc-1"
    assert params == {}
    assert body is None


def test_build_request_moves_query_params_out_of_body():
    route = ToolRoute(
        name="list_widgets",
        method="GET",
        path="/widgets",
        query_param_names=["color", "limit"],
    )
    method, url, params, body = build_request(
        route, {"color": "red", "unused": "x"}, "http://api.internal"
    )
    assert params == {"color": "red"}
    assert "unused" not in params  # not declared as a query/path/body field -> dropped
    assert body is None


def test_build_request_extracts_body_for_write_operations():
    route = ToolRoute(name="create_widget", method="POST", path="/widgets", has_body=True)
    method, url, params, body = build_request(
        route, {"body": {"name": "gadget"}}, "http://api.internal"
    )
    assert method == "POST"
    assert body == {"name": "gadget"}


def test_build_request_missing_path_param_raises_tool_call_error():
    route = ToolRoute(
        name="get_widget",
        method="GET",
        path="/widgets/{widget_id}",
        path_param_names=["widget_id"],
    )
    with pytest.raises(ToolCallError):
        build_request(route, {}, "http://api.internal")


def test_build_request_strips_trailing_slash_from_base_url():
    route = ToolRoute(name="list_widgets", method="GET", path="/widgets")
    _, url, _, _ = build_request(route, {}, "http://api.internal/")
    assert url == "http://api.internal/widgets"


async def test_execute_tool_call_hits_sample_api_in_process():
    import httpx
    from httpx import ASGITransport

    from app.proxy import execute_tool_call
    from sample_api.main import app as sample_api_app

    route = ToolRoute(
        name="get_product",
        method="GET",
        path="/products/{product_id}",
        path_param_names=["product_id"],
    )
    async with httpx.AsyncClient(
        transport=ASGITransport(app=sample_api_app), base_url="http://sample-api.internal"
    ) as client:
        response = await execute_tool_call(
            client, route, {"product_id": "sku-001"}, "http://sample-api.internal"
        )
    assert response.status_code == 200
    assert response.json()["id"] == "sku-001"
