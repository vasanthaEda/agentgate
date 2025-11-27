"""Translate a validated tool call into a real HTTP request against the
downstream API, using the routing metadata derived from its OpenAPI spec.
"""
from __future__ import annotations

import httpx

from app.openapi_tools import ToolRoute


class ToolCallError(Exception):
    """Raised when the supplied arguments don't satisfy the tool's route."""


def build_request(
    route: ToolRoute, arguments: dict, base_url: str
) -> tuple[str, str, dict, dict | None]:
    """Return (method, url, query_params, json_body) for httpx to send."""
    remaining = dict(arguments)
    path = route.path

    for param_name in route.path_param_names:
        if param_name not in remaining:
            raise ToolCallError(f"missing required path parameter '{param_name}'")
        path = path.replace(f"{{{param_name}}}", str(remaining.pop(param_name)))

    query_params = {}
    for param_name in route.query_param_names:
        if param_name in remaining:
            query_params[param_name] = remaining.pop(param_name)

    json_body = None
    if route.has_body:
        json_body = remaining.pop("body", None)

    url = base_url.rstrip("/") + path
    return route.method, url, query_params, json_body


async def execute_tool_call(
    client: httpx.AsyncClient,
    route: ToolRoute,
    arguments: dict,
    base_url: str,
    timeout: float = 10.0,
) -> httpx.Response:
    method, url, query_params, json_body = build_request(route, arguments, base_url)
    return await client.request(
        method, url, params=query_params or None, json=json_body, timeout=timeout
    )
