from __future__ import annotations

import json

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "psi-agent Gateway", "version": "1.0.0"},
    "servers": [{"url": "/"}],
    "paths": {
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
        "/feishu/route": {
            "post": {
                "summary": "Route a Feishu chat to its Session (per-chat for groups, per-user for DMs)",
                "operationId": "feishuRoute",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FeishuRouteRequest"}}},
                },
                "responses": {
                    "201": {
                        "description": "Routed",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FeishuRoute"}}},
                    },
                    "400": {"$ref": "#/components/responses/Error"},
                    "404": {"$ref": "#/components/responses/Error"},
                    "500": {"$ref": "#/components/responses/Error"},
                },
            },
        },
        "/feishu/routes": {
            "get": {
                "summary": "List all Feishu chat -> Session routes",
                "operationId": "listFeishuRoutes",
                "responses": {
                    "200": {
                        "description": "List of routes",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/FeishuRouteEntry"},
                                }
                            }
                        },
                    },
                },
            },
        },
        "/docs-addon/session": {
            "post": {
                "summary": "Route a docs add-on conversation to its Session (per document, per user)",
                "operationId": "docsAddonSession",
                "parameters": [
                    {
                        "name": "X-Psi-Addon-Token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Pre-shared token matching --docs-addon-token; 404 when unset, 401 when wrong",
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/DocsAddonSessionRequest"}}
                    },
                },
                "responses": {
                    "201": {
                        "description": "Routed (channel_socket is deliberately withheld from browser clients)",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DocsAddonRoute"}}},
                    },
                    "400": {"$ref": "#/components/responses/Error"},
                    "401": {"$ref": "#/components/responses/Error"},
                    "404": {"$ref": "#/components/responses/Error"},
                    "500": {"$ref": "#/components/responses/Error"},
                },
            },
        },
        "/docs-addon/chat": {
            "post": {
                "summary": "One docs add-on chat turn, streamed as SSE (session derived, never client-supplied)",
                "operationId": "docsAddonChat",
                "parameters": [
                    {
                        "name": "X-Psi-Addon-Token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DocsAddonChatRequest"}}},
                },
                "responses": {
                    "200": {"description": "SSE stream of Chunk objects"},
                    "400": {"$ref": "#/components/responses/Error"},
                    "401": {"$ref": "#/components/responses/Error"},
                    "404": {"$ref": "#/components/responses/Error"},
                },
            },
        },
        "/docs-addon/routes": {
            "get": {
                "summary": "List all docs add-on -> Session routes",
                "operationId": "listDocsAddonRoutes",
                "parameters": [
                    {
                        "name": "X-Psi-Addon-Token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "List of routes",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/DocsAddonRoute"},
                                }
                            }
                        },
                    },
                    "401": {"$ref": "#/components/responses/Error"},
                    "404": {"$ref": "#/components/responses/Error"},
                },
            },
        },
        "/oauth/callback": {
            "get": {
                "summary": "OAuth redirect landing point (relays the code, no manual copy)",
                "operationId": "oauthCallback",
                "parameters": [
                    {"name": "state", "in": "query", "required": True, "schema": {"type": "string"}},
                    {"name": "code", "in": "query", "schema": {"type": "string"}},
                    {"name": "error", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {
                    "200": {"description": "HTML success page; the code is held for the initiator"},
                    "400": {"description": "HTML failure page (missing state, or provider error)"},
                },
            },
        },
        "/oauth/code": {
            "get": {
                "summary": "Take the relayed authorization code once, by state",
                "operationId": "oauthTakeCode",
                "parameters": [
                    {"name": "state", "in": "query", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {
                    "200": {"description": "{state, code} — or {state, error}; consumed on read"},
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
                    "200": {
                        "description": ("Array of {id, label, created_at, updated_at, closed_at, source, summary}")
                    },
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
        "/ui/attention": {
            "post": {
                "summary": "Flash tray icon / native window when chat completes in background",
                "operationId": "requestAttention",
                "responses": {
                    "200": {"description": "Attention cue dispatched (best-effort)"},
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
        "/workspace/places": {
            "get": {
                "summary": "List quick-access paths and drives for path picker",
                "operationId": "listWorkspaceRoots",
                "responses": {
                    "200": {"description": "Roots and drives"},
                },
            },
        },
        "/workspace/browse": {
            "get": {
                "summary": "Browse directories for workspace selection",
                "operationId": "browseWorkspace",
                "parameters": [
                    {
                        "name": "path",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "kind",
                        "in": "query",
                        "schema": {"type": "string", "enum": ["directory", "file", "all"], "default": "directory"},
                    },
                    {
                        "name": "q",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {"description": "Directory listing"},
                    "400": {"$ref": "#/components/responses/Error"},
                },
            },
        },
        "/workspace/cwd": {
            "get": {
                "summary": "Get the server's current working directory",
                "operationId": "getCwd",
                "responses": {
                    "200": {"description": 'CWD string (e.g. {"cwd": "/home/user"})'},
                },
            },
        },
        "/workspace/reveal": {
            "post": {
                "summary": "Reveal a path in the OS file manager",
                "operationId": "revealWorkspacePath",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["path"],
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "Absolute or resolvable filesystem path to select/open",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "File manager launched ({path, ok})"},
                    "400": {"$ref": "#/components/responses/Error"},
                    "404": {"$ref": "#/components/responses/Error"},
                },
            },
        },
    },
    "components": {
        "schemas": {
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
                            "-1 = resolve from PSI_MAX_CONTEXT_TOKENS env var, else 100000. "
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
                            "(examples/haitun-workspace when present), else Session uses workspace"
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
            "FeishuRouteRequest": {
                "type": "object",
                "description": (
                    "Needs at least one routing key: open_id (DM) or chat_id with a group/topic chat_type."
                ),
                "properties": {
                    "open_id": {
                        "type": "string",
                        "description": "Sender's open_id. Required unless routing a group chat by chat_id.",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "Feishu chat id. With chat_type group/topic, the whole chat shares one Session.",
                    },
                    "chat_type": {
                        "type": "string",
                        "description": "p2p | group | topic. group/topic routes by chat_id, anything else by open_id.",
                    },
                    "ai_id": {
                        "type": "string",
                        "description": "Optional, overrides Gateway --feishu-ai-id",
                    },
                    "workspace": {
                        "type": "string",
                        "description": (
                            "Optional, defaults to <feishu_workspace_root>/<open_id> "
                            "(or /chat-<chat_id> for group chats)"
                        ),
                    },
                },
            },
            "FeishuRoute": {
                "type": "object",
                "properties": {
                    "open_id": {"type": "string"},
                    "chat_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "channel_socket": {"type": "string"},
                },
            },
            "FeishuRouteEntry": {
                "type": "object",
                "description": "One route. Group entries carry chat_id with an empty open_id; DMs the reverse.",
                "properties": {
                    "open_id": {"type": "string"},
                    "chat_id": {"type": "string"},
                    "session_id": {"type": "string"},
                },
            },
            "DocsAddonSessionRequest": {
                "type": "object",
                "description": (
                    "Both keys are required. user_id comes from the add-on's Service.User.getUserId() and is "
                    "client-asserted: it scopes the conversation but is NOT authentication — the pre-shared "
                    "X-Psi-Addon-Token is."
                ),
                "properties": {
                    "doc_token": {
                        "type": "string",
                        "description": "The document the add-on block lives in.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Feishu user id, used only to isolate conversations within a document.",
                    },
                    "ai_id": {
                        "type": "string",
                        "description": (
                            "Optional, overrides Gateway --docs-addon-ai-id (which falls back to --feishu-ai-id)"
                        ),
                    },
                    "workspace": {
                        "type": "string",
                        "description": (
                            "Optional, defaults to "
                            "<docs_addon_workspace_root>/docsaddon-<doc_token hash>/<user_id hash> "
                            "(hashed to match how session_id is derived; map back via GET /docs-addon/routes)"
                        ),
                    },
                },
            },
            "DocsAddonChatRequest": {
                "type": "object",
                "description": (
                    "No session_id field by design: the Session is re-derived from (doc_token, user_id), so a "
                    "token holder cannot address someone else's conversation."
                ),
                "properties": {
                    "doc_token": {"type": "string"},
                    "user_id": {"type": "string"},
                    "chunks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "text": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "DocsAddonRoute": {
                "type": "object",
                "properties": {
                    "doc_token": {"type": "string"},
                    "user_id": {"type": "string"},
                    "session_id": {"type": "string"},
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
        },
        "responses": {
            "Error": {
                "description": "Error response",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
            },
        },
    },
}


def render_openapi() -> str:
    return json.dumps(OPENAPI_SPEC)
