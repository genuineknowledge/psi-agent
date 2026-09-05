"""公共 OpenAPI 片段 —— 两条产品线都注册的端点。

按 path key 从原 ``_openapi.py`` 原样切出, 未改动任何 schema。
产品专属片段见 ``desktop/_openapi.py`` (ToC) / ``feishu/_openapi.py`` (ToB)。

``/oauth/*`` 曾经归这里, 理由是「``OAuthRelay`` 只认识 ``state -> code`` 信箱」。
后来(实测)取件方全在 ToB 一侧, 已随 ``OAuthRelay`` 一起挪到 ``feishu/_openapi.py``
—— 判据不只看这段代码认识什么概念, 还要看它到底有没有第二个消费者。
"""

from __future__ import annotations

from typing import Any

CORE_PATHS: dict[str, Any] = {
    "/ais": {
        "post": {
            "summary": "Create an AI backend",
            "operationId": "createAi",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AiCreateRequest"}}},
            },
            "responses": {
                "201": {
                    "description": "AI created",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AiInfo"}}},
                },
                "400": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
        "get": {
            "summary": "List all AI backends",
            "operationId": "listAis",
            "responses": {
                "200": {
                    "description": "List of AIs",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/AiInfo"},
                            }
                        }
                    },
                },
            },
        },
    },
    "/ais/{ai_id}": {
        "delete": {
            "summary": "Delete an AI backend",
            "operationId": "deleteAi",
            "parameters": [
                {
                    "name": "ai_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {
                    "description": "AI deleted",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeleteResponse"}}},
                },
                "404": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/routers": {
        "post": {
            "summary": "Create and start a Router backend",
            "operationId": "createRouter",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RouterCreateRequest"}}},
            },
            "responses": {
                "201": {
                    "description": "Router created",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RouterInfo"}}},
                },
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
        "get": {
            "summary": "List all Router backends",
            "operationId": "listRouters",
            "responses": {
                "200": {
                    "description": "List of Routers",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/RouterInfo"},
                            }
                        }
                    },
                }
            },
        },
    },
    "/routers/{router_id}": {
        "delete": {
            "summary": "Stop and delete a Router backend",
            "operationId": "deleteRouter",
            "parameters": [
                {
                    "name": "router_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {
                    "description": "Router deleted",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeleteResponse"}}},
                },
                "404": {"$ref": "#/components/responses/Error"},
                "409": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        }
    },
    "/sessions": {
        "post": {
            "summary": "Create a Session",
            "operationId": "createSession",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SessionCreateRequest"}}},
            },
            "responses": {
                "201": {
                    "description": "Session created",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SessionInfo"}}},
                },
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
        "get": {
            "summary": "List all Sessions",
            "operationId": "listSessions",
            "responses": {
                "200": {
                    "description": "List of Sessions",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/SessionInfo"},
                            }
                        }
                    },
                },
            },
        },
    },
    "/sessions/{session_id}": {
        "delete": {
            "summary": "Delete a Session",
            "operationId": "deleteSession",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {
                    "description": "Session deleted",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeleteResponse"}}},
                },
                "404": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/sessions/{session_id}/chat": {
        "post": {
            "summary": "Chat with a Session (SSE stream)",
            "operationId": "chat",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "requestBody": {
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "chunks": {
                                    "type": "string",
                                    "description": "JSON array of text and blob chunks",
                                },
                                "file": {
                                    "type": "string",
                                    "format": "binary",
                                },
                            },
                        },
                    },
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "chunks": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string"},
                                            "text": {"type": "string"},
                                            "name": {"type": "string"},
                                            "data": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {"description": "SSE stream of Chunk objects"},
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/sessions/{session_id}/history": {
        "get": {
            "summary": "Get session conversation history",
            "operationId": "getHistory",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {
                    "description": (
                        "Array of {role, text, kind?, sends?, reasoning?, tools?} messages; "
                        "assistant may include JSONL ``reasoning`` (thinking) and "
                        "``tools`` (structured tool_calls projection) for SPA process UI"
                    )
                },
                "404": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/sessions/{session_id}/todos": {
        "get": {
            "summary": "Get session todo list (AppData todos/ with legacy dual-read)",
            "operationId": "getTodos",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {"description": ("Object with todos[] ({id, content, status}) and summary counts")},
                "404": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/sessions/{session_id}/todo-segments": {
        "get": {
            "summary": "List todo sub-task segments (AppData *.segments.json, newest first)",
            "operationId": "listTodoSegments",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {
                "200": {"description": ("Array of {id, label, created_at, updated_at, closed_at, source, summary}")},
                "404": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/sessions/{session_id}/todo-segments/{segment_id}": {
        "get": {
            "summary": "Get one todo segment including todos[]",
            "operationId": "getTodoSegment",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "segment_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "responses": {
                "200": {"description": ("Object with id, label, todos[], summary, closed_at, …")},
                "404": {"$ref": "#/components/responses/Error"},
            },
        },
        "post": {
            "summary": "Set todo segment label (P1 summary override)",
            "operationId": "setTodoSegmentLabel",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "segment_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["label"],
                            "properties": {"label": {"type": "string"}},
                        }
                    }
                },
            },
            "responses": {
                "200": {"description": "Updated segment including todos[]"},
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/titles": {
        "get": {
            "summary": "List all session titles",
            "operationId": "listTitles",
            "responses": {
                "200": {"description": "Map of session IDs to titles"},
            },
        },
        "post": {
            "summary": "Set a session title",
            "operationId": "setTitle",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["id", "title"],
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {"description": "Title set"},
                "400": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/titles/generate": {
        "post": {
            "summary": "AI-generated session title",
            "operationId": "generateTitle",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["id", "user_text", "assistant_text"],
                            "properties": {
                                "id": {"type": "string"},
                                "user_text": {"type": "string"},
                                "assistant_text": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {"description": "Generated title"},
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/summaries": {
        "get": {
            "summary": "List all session task summaries",
            "operationId": "listSummaries",
            "responses": {
                "200": {"description": "Map of session IDs to task summaries"},
            },
        },
        "post": {
            "summary": "Set a session task summary",
            "operationId": "setSummary",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["id", "summary"],
                            "properties": {
                                "id": {"type": "string"},
                                "summary": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {"description": "Summary set"},
                "400": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/summaries/generate": {
        "post": {
            "summary": "AI-generated task summary (1-2 sentences, not a title)",
            "operationId": "generateSummary",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["id", "user_text", "assistant_text"],
                            "properties": {
                                "id": {"type": "string"},
                                "user_text": {"type": "string"},
                                "assistant_text": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "responses": {
                "200": {"description": "Generated summary"},
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
                "500": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/defaults": {
        "get": {
            "summary": "Default agent, workspace, and AppData root paths",
            "operationId": "getDefaults",
            "responses": {
                "200": {
                    "description": "Path defaults for SPA / tooling (AppData announce-only until relocate PRs)",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/GatewayDefaults"},
                        }
                    },
                },
            },
        },
    },
}

CORE_SCHEMAS: dict[str, Any] = {
    "AiCreateRequest": {
        "type": "object",
        "required": ["provider", "model", "api_key", "base_url"],
        "properties": {
            "id": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "api_key": {"type": "string"},
            "base_url": {"type": "string"},
            "max_context_tokens": {
                "type": "integer",
                "default": -1,
                "description": (
                    "Prompt token threshold that triggers history compaction. "
                    "-1 = resolve from PSI_MAX_CONTEXT_TOKENS env var, else 200000. "
                    "0 = disable compaction. Keep it well below the model's real "
                    "context window so compaction runs before the upstream rejects "
                    "the request."
                ),
            },
        },
    },
    "AiInfo": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "socket": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "max_context_tokens": {"type": "integer"},
        },
    },
    "RouterUpstreamInfo": {
        "type": "object",
        "required": ["backend_type", "backend_id", "description"],
        "properties": {
            "backend_type": {"type": "string", "enum": ["ai", "router"]},
            "backend_id": {"type": "string"},
            "description": {"type": "string"},
        },
    },
    "RouterCreateRequest": {
        "type": "object",
        "required": ["name", "mode", "router_ai_id", "upstreams"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "mode": {"type": "string", "enum": ["routing", "aggregation", "fallback"]},
            "router_ai_id": {"type": "string", "nullable": True},
            "upstreams": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/components/schemas/RouterUpstreamInfo"},
            },
            "router_timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "nullable": True,
            },
            "target_timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "nullable": True,
            },
            "max_context_chars": {
                "type": "integer",
                "minimum": 1,
                "default": 12_000,
            },
        },
        "oneOf": [
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
        ],
    },
    "RouterInfo": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "socket": {"type": "string"},
            "mode": {"type": "string", "enum": ["routing", "aggregation", "fallback"]},
            "router_ai_id": {"type": "string", "nullable": True},
            "upstreams": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/RouterUpstreamInfo"},
            },
            "router_timeout": {"type": "number", "nullable": True},
            "target_timeout": {"type": "number", "nullable": True},
            "max_context_chars": {"type": "integer", "minimum": 1},
        },
    },
    "SessionCreateRequest": {
        "type": "object",
        "required": ["ai_id"],
        "properties": {
            "id": {"type": "string"},
            "ai_id": {"type": "string"},
            "workspace": {
                "type": "string",
                "description": (
                    "User workspace. Empty → Gateway default ({Desktop}/haitun交付); mkdir on Session create"
                ),
            },
            "agent": {
                "type": "string",
                "description": (
                    "Agent package path. Empty → Gateway default "
                    "(agents/feishu when present), else Session uses workspace"
                ),
            },
        },
    },
    "SessionInfo": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "ai_id": {"type": "string"},
            "workspace": {"type": "string"},
            "agent": {"type": "string"},
            "channel_socket": {"type": "string"},
            "active_schedules": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names of the schedules under {workspace}/schedules this session "
                    "actually fires; ['*'] means all of them. Activation is a "
                    "(session x schedule) property, so sessions sharing a workspace can "
                    "each fire a different subset"
                ),
            },
            "deactive_schedules": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names excluded from active_schedules (blacklist, wins over the "
                    "whitelist). A wildcard whitelist plus this blacklist is how a session "
                    "claims 'everything except these', including TASK.md files created later"
                ),
            },
            "scheduler": {
                "type": "boolean",
                "description": (
                    "Derived: true only for the per-workspace scheduler session that fires "
                    "all of {workspace}/schedules (active_schedules == ['*']). Such sessions "
                    "are hidden from GET /sessions, so this is always false in list responses"
                ),
            },
        },
    },
    "GatewayDefaults": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Default agent package path"},
            "workspace": {"type": "string", "description": "Default user workspace"},
            "appdata": {
                "type": "string",
                "description": (
                    "AppData memory root (platformdirs / --appdata / PSI_APPDATA). "
                    "Todos live under {appdata}/todos/; history under {appdata}/histories/; "
                    "Gateway state under {appdata}/state/ (legacy paths dual-read)."
                ),
            },
        },
    },
    "DeleteResponse": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string"},
        },
    },
    "Error": {
        "type": "object",
        "properties": {"error": {"type": "string"}},
    },
}

CORE_RESPONSES: dict[str, Any] = {
    "Error": {
        "description": "Error response",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    },
}
