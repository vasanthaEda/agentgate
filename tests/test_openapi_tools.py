"""Tests for OpenAPI-spec -> function-calling tool schema ingestion."""
from __future__ import annotations

import pytest

from app.openapi_tools import _resolve_ref, build_tool_registry, load_spec

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "test", "version": "1.0"},
    "paths": {
        "/widgets": {
            "get": {
                "operationId": "list_widgets",
                "summary": "List widgets",
                "parameters": [
                    {
                        "name": "color",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "operationId": "create_widget",
                "summary": "Create a widget",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "created"}},
            },
        },
        "/widgets/{widget_id}": {
            "get": {
                "operationId": "get_widget",
                "summary": "Get one widget",
                "parameters": [
                    {
                        "name": "widget_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


def test_build_tool_registry_from_dict():
    registry = build_tool_registry(MINIMAL_SPEC)
    names = set(registry.schemas.keys())
    assert names == {"list_widgets", "create_widget", "get_widget"}


def test_query_param_is_optional_and_not_required():
    registry = build_tool_registry(MINIMAL_SPEC)
    schema = registry.schemas["list_widgets"]
    assert "color" in schema["parameters"]["properties"]
    assert "color" not in schema["parameters"]["required"]


def test_path_param_is_required_and_routed():
    registry = build_tool_registry(MINIMAL_SPEC)
    schema = registry.schemas["get_widget"]
    assert "widget_id" in schema["parameters"]["required"]
    route = registry.get_route("get_widget")
    assert route.path_param_names == ["widget_id"]
    assert route.method == "GET"
    assert route.path == "/widgets/{widget_id}"


def test_request_body_becomes_body_property():
    registry = build_tool_registry(MINIMAL_SPEC)
    schema = registry.schemas["create_widget"]
    assert "body" in schema["parameters"]["properties"]
    assert "body" in schema["parameters"]["required"]
    route = registry.get_route("create_widget")
    assert route.has_body is True


def test_missing_operation_id_falls_back_to_method_and_path():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {"/foo/bar": {"delete": {"responses": {"200": {"description": "ok"}}}}},
    }
    registry = build_tool_registry(spec)
    assert "delete_foo_bar" in registry.schemas


def test_unknown_tool_route_lookup_returns_none():
    registry = build_tool_registry(MINIMAL_SPEC)
    assert registry.get_route("does_not_exist") is None


def test_load_spec_accepts_dict_passthrough():
    assert load_spec(MINIMAL_SPEC) is MINIMAL_SPEC


def test_load_spec_reads_json_file(tmp_path):
    import json

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(MINIMAL_SPEC))
    loaded = load_spec(spec_path)
    assert loaded["paths"].keys() == MINIMAL_SPEC["paths"].keys()


def test_load_spec_reads_yaml_file(tmp_path):
    import yaml

    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(MINIMAL_SPEC))
    loaded = load_spec(spec_path)
    assert loaded["paths"].keys() == MINIMAL_SPEC["paths"].keys()


def test_resolve_ref_rejects_non_local_refs():
    with pytest.raises(ValueError, match="only local refs"):
        _resolve_ref({}, "https://example.com/schema.json#/Widget")


def test_ecommerce_demo_spec_builds_cleanly():
    registry = build_tool_registry("demo/ecommerce_openapi.json")
    assert "list_products" in registry.schemas
    assert "create_order" in registry.schemas
    assert registry.get_route("create_order").has_body is True
