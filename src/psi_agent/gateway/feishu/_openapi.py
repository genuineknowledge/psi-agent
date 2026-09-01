"""ToB (飞书) 专属 OpenAPI 片段 —— 飞书会话到 Session 的路由表, 以及 OAuth 回调中继。

``/feishu/*`` 与三个 ``FeishuRoute*`` schema 只有飞书这条线用得到; ToC 不注册。

``/oauth/*`` 两条从 ``_openapi_core`` 挪来: 取件方(实测)全在 ``agents/feishu/tools/``,
ToC 的登录走手机号 + 验证码不经过 OAuth 跳转 —— 详见 ``_oauth_manager`` 模块头。

**但它们自成 ``OAUTH_PATHS``, 不在 ``FEISHU_PATHS`` 里**: 路由侧
``register_oauth_routes()`` 每种 ``--gateway`` 组合都贴 (回调地址登记在第三方应用
后台, 不随本进程挂了哪面而变), 所以 spec 也必须每种组合都报 —— 挂在 feishu 开关上
就会出现「路由在、spec 里没有」的错报。三份片段变四份, 并集与拆分前一致。
"""

from __future__ import annotations

from typing import Any

FEISHU_PATHS: dict[str, Any] = {
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
}

OAUTH_PATHS: dict[str, Any] = {
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
}

FEISHU_SCHEMAS: dict[str, Any] = {
    "FeishuRouteRequest": {
        "type": "object",
        "description": ("Needs at least one routing key: open_id (DM) or chat_id with a group/topic chat_type."),
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
                    "Optional, defaults to <feishu_workspace_root>/<open_id> (or /chat-<chat_id> for group chats)"
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
}
