"""ToC (桌面版) 专属 OpenAPI 片段 —— 托盘注意力、SPA 一次性偏好、工作区浏览。

背后的 ``AttentionHub`` / ``UIPrefs`` / ``WorkspaceManager`` 都认识桌面概念
(pystray / pywebview / Windows 盘符), ToB 容器里没有这些端点。
本片段不引用任何专属 schema, 只引用公共的 ``#/components/responses/Error``。
"""

from __future__ import annotations

from typing import Any

DESKTOP_PATHS: dict[str, Any] = {
    "/ui/attention": {
        "post": {
            "summary": "Flash tray icon / native window when chat completes in background",
            "operationId": "requestAttention",
            "responses": {
                "200": {"description": "Attention cue dispatched (best-effort)"},
            },
        },
    },
    "/ui/prefs/survey": {
        "get": {
            "summary": "Whether the survey popup was already dismissed on this machine",
            "operationId": "getSurveyPref",
            "responses": {
                "200": {"description": "Survey flag state"},
            },
        },
        "post": {
            "summary": "Record that the survey popup was dismissed",
            "operationId": "setSurveyPref",
            "responses": {
                "200": {"description": "Survey flag persisted"},
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
}
