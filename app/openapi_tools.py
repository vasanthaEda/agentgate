"""Turn an OpenAPI 3.x spec into permission-scoped, function-calling tool schemas.

Each ``{path, method}`` operation in the spec becomes one "tool": a name, a
natural-language description, and a JSON-schema ``parameters`` object an LLM
can be handed directly as a function/tool definition. A parallel
``ToolRoute`` records exactly how to replay that tool call against the real
downstream API (which path/query params go where, whether there's a body).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_NON_WORD = re.compile(r"[^0-9a-zA-Z]+")


def load_spec(source: str | Path | dict) -> dict:
    """Load an OpenAPI document from a dict, or a .json/.yaml/.yml file path."""
    if isinstance(source, dict):
        return source
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def _resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a local ``#/a/b/c`` JSON pointer within the spec document."""
    if not ref.startswith("#/"):
        raise ValueError(f"only local refs are supported, got: {ref}")
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _deref(spec: dict, node: Any, _depth: int = 0) -> Any:
    """Recursively resolve $ref pointers inside a schema fragment."""
    if _depth > 20 or not isinstance(node, dict | list):
        return node
    if isinstance(node, list):
        return [_deref(spec, item, _depth + 1) for item in node]
    if "$ref" in node:
        return _deref(spec, _resolve_ref(spec, node["$ref"]), _depth + 1)
    return {k: _deref(spec, v, _depth + 1) for k, v in node.items()}


def _default_tool_name(method: str, path: str) -> str:
    slug = _NON_WORD.sub("_", path.strip("/")).strip("_")
    return f"{method.lower()}_{slug}".lower()


@dataclass
class ToolRoute:
    """Everything needed to replay a tool call against the downstream API."""

    name: str
    method: str
    path: str  # OpenAPI-style path template, e.g. /orders/{order_id}
    path_param_names: list[str] = field(default_factory=list)
    query_param_names: list[str] = field(default_factory=list)
    has_body: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolRegistry:
    """The result of ingesting one OpenAPI spec: tool schemas + routing info."""

    schemas: dict[str, dict] = field(default_factory=dict)  # name -> function-calling schema
    routes: dict[str, ToolRoute] = field(default_factory=dict)  # name -> ToolRoute

    def list_schemas(self) -> list[dict]:
        return list(self.schemas.values())

    def get_route(self, tool_name: str) -> ToolRoute | None:
        return self.routes.get(tool_name)


_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def build_tool_registry(spec_source: str | Path | dict) -> ToolRegistry:
    spec = load_spec(spec_source)
    paths: dict = spec.get("paths", {})
    registry = ToolRegistry()

    for path, path_item in paths.items():
        shared_params = path_item.get("parameters", [])
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if operation is None:
                continue

            operation = _deref(spec, operation)
            op_id = operation.get("operationId")
            tool_name = _sanitize_tool_name(op_id) if op_id else _default_tool_name(method, path)

            all_params = [*shared_params, *operation.get("parameters", [])]
            all_params = _deref(spec, all_params)

            properties: dict[str, Any] = {}
            required: list[str] = []
            path_param_names: list[str] = []
            query_param_names: list[str] = []

            for param in all_params:
                p_name = param["name"]
                p_schema = param.get("schema", {"type": "string"})
                properties[p_name] = {
                    **p_schema,
                    "description": param.get("description", ""),
                }
                if param.get("required") or param.get("in") == "path":
                    required.append(p_name)
                if param.get("in") == "path":
                    path_param_names.append(p_name)
                elif param.get("in") == "query":
                    query_param_names.append(p_name)

            has_body = False
            request_body = operation.get("requestBody")
            if request_body:
                request_body = _deref(spec, request_body)
                json_content = request_body.get("content", {}).get("application/json", {})
                body_schema = json_content.get("schema")
                if body_schema:
                    has_body = True
                    # Merge body schema fields under a "body" property so the
                    # agent-facing schema cleanly separates routing params
                    # from the payload -- this also keeps things simple when
                    # a body property happens to collide with a query param.
                    properties["body"] = body_schema
                    if request_body.get("required"):
                        required.append("body")

            description = operation.get("summary") or operation.get("description") or (
                f"{method.upper()} {path}"
            )

            registry.schemas[tool_name] = {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
                "method": method.upper(),
                "path": path,
            }
            registry.routes[tool_name] = ToolRoute(
                name=tool_name,
                method=method.upper(),
                path=path,
                path_param_names=path_param_names,
                query_param_names=query_param_names,
                has_body=has_body,
                tags=operation.get("tags", []),
            )

    return registry


def _sanitize_tool_name(name: str) -> str:
    return _NON_WORD.sub("_", name).strip("_").lower()
