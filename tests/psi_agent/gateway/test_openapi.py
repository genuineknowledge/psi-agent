from typing import Any, cast

from psi_agent.gateway._openapi import OPENAPI_SPEC


def test_openapi_router_contract_uses_current_fields_only() -> None:
    spec = cast(dict[str, Any], OPENAPI_SPEC)
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]

    assert {"post", "get"} <= set(paths["/routers"])
    assert "delete" in paths["/routers/{router_id}"]
    properties = schemas["RouterCreateRequest"]["properties"]
    assert properties["mode"]["enum"] == ["routing", "aggregation", "fallback"]
    assert properties["router_ai_id"]["nullable"] is True
    assert properties["router_timeout"]["nullable"] is True
    assert properties["target_timeout"]["nullable"] is True
    assert properties["max_context_chars"]["minimum"] == 1
    assert "default_ai_id" not in properties
    assert "max_context_length" not in properties
    upstream = schemas["RouterUpstreamInfo"]
    assert upstream["required"] == ["backend_type", "backend_id", "description"]
    assert upstream["properties"]["backend_type"]["enum"] == ["ai", "router"]
    assert schemas["RouterCreateRequest"]["oneOf"] == [
        {
            "properties": {
                "mode": {"enum": ["fallback"]},
                "router_ai_id": {"enum": [None]},
            }
        },
        {
            "properties": {
                "mode": {"enum": ["routing", "aggregation"]},
                "router_ai_id": {"type": "string", "minLength": 1},
            }
        },
    ]
    assert "409" in paths["/routers/{router_id}"]["delete"]["responses"]
