"""OpenAPI spec 装配 —— 公共骨架 + 各产品线按需往上贴。

原先本文件是一个 915 行的整体 dict, 桌面端和飞书端的端点混在同一份里: 谁想只发布
自己那批端点都做不到, 只能整份发出去。现在按 path key 分成四份, 各自独立演化:

- ``_openapi_core.py``      两条线都注册的端点 (``/ais`` ``/sessions`` ``/titles`` …)
- ``desktop/_openapi.py``   ToC 专属 (``/ui/*`` ``/workspace/*``)
- ``feishu/_openapi.py``    ToB 专属 (``/feishu/*``) + 与产品线正交的 ``OAUTH_PATHS``

A5: 后两份随各自产品线搬进 ``desktop/`` / ``feishu/`` 子包 —— 一条产品线的 spec 片段
和它的 manager、路由注册住在一起, 加端点时只动一个目录。装配仍留在骨架层: 它要同时
认识三份才能拼, 放进任一产品包都会让那个包被另一条线反向依赖。

``build_openapi_spec()`` 按传入开关组装; ``OPENAPI_SPEC`` 是「全都要」的那份, 与拆分前
的 path key 集合和每个 key 下的 schema 完全一致 —— 现有 ``GET /openapi.json`` 行为不变。
路由注册按消费者分开之后 (A4), 各产品线换成传对应开关即可。
"""

from __future__ import annotations

import json
from typing import Any

from psi_agent.gateway._openapi_core import CORE_PATHS, CORE_RESPONSES, CORE_SCHEMAS
from psi_agent.gateway.desktop._openapi import DESKTOP_PATHS
from psi_agent.gateway.feishu._openapi import FEISHU_PATHS, FEISHU_SCHEMAS, OAUTH_PATHS


def build_openapi_spec(*, desktop: bool = True, feishu: bool = True, oauth: bool = True) -> dict[str, Any]:
    """组装 spec。``desktop`` / ``feishu`` 决定是否贴上对应产品线的片段。

    ``oauth`` 独立于产品线: ``/oauth/*`` 由 ``register_oauth_routes()`` 每种组合都注册,
    所以默认为真, 与 ``feishu`` 无关 (见 ``feishu/_openapi.OAUTH_PATHS`` 模块头)。
    """
    paths: dict[str, Any] = dict(CORE_PATHS)
    schemas: dict[str, Any] = dict(CORE_SCHEMAS)
    if desktop:
        paths.update(DESKTOP_PATHS)
    if feishu:
        paths.update(FEISHU_PATHS)
        schemas.update(FEISHU_SCHEMAS)
    if oauth:
        paths.update(OAUTH_PATHS)
    return {
        "openapi": "3.0.3",
        "info": {"title": "psi-agent Gateway", "version": "1.0.0"},
        "servers": [{"url": "/"}],
        "paths": paths,
        "components": {"schemas": schemas, "responses": dict(CORE_RESPONSES)},
    }


OPENAPI_SPEC = build_openapi_spec()


def render_openapi(*, desktop: bool = True, feishu: bool = True, oauth: bool = True) -> str:
    """渲染 spec。三个开关都为真时直接用预建的 ``OPENAPI_SPEC``, 省一次装配。

    ``GET /openapi.json`` 把本进程真的注册了的产品线传进来 (见 ``server._handle_openapi``),
    这样飞书容器的 spec 里不再有 ``/workspace/*``, 桌面端的里不再有 ``/feishu/*``。
    """
    if desktop and feishu and oauth:
        return json.dumps(OPENAPI_SPEC)
    return json.dumps(build_openapi_spec(desktop=desktop, feishu=feishu, oauth=oauth))
