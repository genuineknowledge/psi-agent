from __future__ import annotations

import json

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "psi-agent Gateway", "version": "0.1.0"},
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
                },
            },
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
                                        "description": "JSON array of text chunks",
                                    },
                                    "file": {
                                        "type": "string",
                                        "format": "binary",
                                    },
                                },
                            },
                        },
                    },
                },
                "responses": {
                    "200": {"description": "SSE stream of Chunk objects"},
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
                    "200": {"description": "Array of {role, text} messages"},
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
                    "500": {"$ref": "#/components/responses/Error"},
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
                    }
                ],
                "responses": {
                    "200": {"description": "Directory listing"},
                    "400": {"$ref": "#/components/responses/Error"},
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
                },
            },
            "AiInfo": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "socket": {"type": "string"},
                    "provider": {"type": "string"},
                    "model": {"type": "string"},
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
                        "description": "Optional, defaults to CWD",
                    },
                },
            },
            "SessionInfo": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ai_id": {"type": "string"},
                    "workspace": {"type": "string"},
                    "channel_socket": {"type": "string"},
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
