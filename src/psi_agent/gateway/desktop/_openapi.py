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
    "/ui/prefs/language": {
        "get": {
            "summary": "Effective app language for the SPA",
            "operationId": "getLanguagePref",
            "responses": {
                "200": {"description": "Effective language code"},
            },
        },
        "post": {
            "summary": "Persist the in-app language switch",
            "operationId": "setLanguagePref",
            "responses": {
                "200": {"description": "Language code persisted"},
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
    "/workspace/file": {
        "get": {
            "summary": "Read a file as base64",
            "operationId": "readWorkspaceFile",
            "parameters": [
                {"name": "path", "in": "query", "required": True, "schema": {"type": "string"}},
                {"name": "root", "in": "query", "schema": {"type": "string"}},
            ],
            "responses": {
                "200": {"description": "Base64 file content"},
                "400": {"$ref": "#/components/responses/Error"},
                "404": {"$ref": "#/components/responses/Error"},
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
    "/auth/status": {
        "get": {
            "summary": "Get authentication status and self-check info",
            "operationId": "authStatus",
            "responses": {
                "200": {"description": "Authentication status without sensitive token"},
            },
        },
    },
    "/auth/send-code": {
        "post": {
            "summary": "Request verification code from cloud auth service",
            "operationId": "authSendCode",
            "responses": {
                "200": {"description": "Verification code sent"},
                "400": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/verify": {
        "post": {
            "summary": "Verify phone/email code for login/registration",
            "operationId": "authVerify",
            "responses": {
                "200": {"description": "Code verified"},
                "400": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/complete": {
        "post": {
            "summary": "Complete two-step user registration",
            "operationId": "authComplete",
            "responses": {
                "200": {"description": "Registration complete"},
                "400": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/bind": {
        "post": {
            "summary": "Bind additional identity (phone/email)",
            "operationId": "authBind",
            "responses": {
                "200": {"description": "Identity bound"},
                "400": {"$ref": "#/components/responses/Error"},
                "409": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/identities/{provider}": {
        "delete": {
            "summary": "Unbind an identity provider",
            "operationId": "authUnbind",
            "parameters": [
                {"name": "provider", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {
                "200": {"description": "Identity unbound"},
                "400": {"$ref": "#/components/responses/Error"},
                "409": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/me": {
        "get": {
            "summary": "Get current logged-in user profile",
            "operationId": "authMe",
            "responses": {
                "200": {"description": "User profile"},
                "401": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/logout": {
        "post": {
            "summary": "Log out user session and clear local credentials",
            "operationId": "authLogout",
            "responses": {
                "200": {"description": "Logged out"},
            },
        },
    },
    "/auth/devices": {
        "get": {
            "summary": "List logged-in user devices",
            "operationId": "authDevices",
            "responses": {
                "200": {"description": "Device list"},
                "401": {"$ref": "#/components/responses/Error"},
            },
        },
    },
    "/auth/devices/{device_id}": {
        "delete": {
            "summary": "Revoke/kick a logged-in device",
            "operationId": "authRevokeDevice",
            "parameters": [
                {"name": "device_id", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {
                "200": {"description": "Device revoked"},
                "401": {"$ref": "#/components/responses/Error"},
            },
        },
    },
}
