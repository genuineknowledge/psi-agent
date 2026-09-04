import json
import re
from typing import Any, cast

from psi_agent.gateway._openapi import OPENAPI_SPEC, build_openapi_spec
from psi_agent.gateway._openapi_core import CORE_PATHS, CORE_RESPONSES, CORE_SCHEMAS
from psi_agent.gateway.desktop._openapi import DESKTOP_PATHS
from psi_agent.gateway.feishu._openapi import FEISHU_PATHS, FEISHU_SCHEMAS, OAUTH_PATHS


def test_openapi_router_contract_uses_current_fields_only() -> None:
    spec = OPENAPI_SPEC
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


def test_four_fragments_partition_the_full_spec() -> None:
    """四份片段的 path key 并集 == 完整 spec, 且互不重叠。

    刻意**不做**字节比对 —— 一份 spec 拆成四份之后不可能字节相同。
    ``OAUTH_PATHS`` 是第四份: 与挂了哪些 gateway 正交, 每种组合都报。
    """
    union = set(CORE_PATHS) | set(DESKTOP_PATHS) | set(FEISHU_PATHS) | set(OAUTH_PATHS)
    assert union == set(OPENAPI_SPEC["paths"])
    assert len(CORE_PATHS) + len(DESKTOP_PATHS) + len(FEISHU_PATHS) + len(OAUTH_PATHS) == len(union)
    assert set(CORE_SCHEMAS) | set(FEISHU_SCHEMAS) == set(OPENAPI_SPEC["components"]["schemas"])
    assert set(CORE_RESPONSES) == set(OPENAPI_SPEC["components"]["responses"])


def test_fragments_own_only_their_own_prefixes() -> None:
    """归属按 path 前缀可判: 产品前缀不许出现在公共片段里, 反之亦然。"""
    assert all(k.startswith(("/ui/", "/workspace/", "/auth/")) for k in DESKTOP_PATHS)
    assert all(k.startswith("/feishu/") for k in FEISHU_PATHS)
    assert not any(k.startswith(("/ui/", "/workspace/", "/feishu/", "/oauth/", "/auth/")) for k in CORE_PATHS)
    # /oauth/* 代码归 ToB (取件方全在 agents/feishu/tools 一侧, ToC 登录不走 OAuth 跳转),
    # 但**自成一份片段**: 路由侧每种 --gateway 组合都注册, 挂在 feishu 开关上会错报。
    assert set(OAUTH_PATHS) == {"/oauth/callback", "/oauth/code"}
    assert not set(OAUTH_PATHS) & set(FEISHU_PATHS)


def test_oauth_is_reported_under_every_gateway_combination() -> None:
    """``/oauth/*`` 与挂了哪些 gateway 正交 —— 三种组合的 spec 里都必须有这两条。

    判据对着的是真实故障: 回调地址登记在第三方应用后台, 不随本进程挂了哪些 gateway 而变,
    少报一次就是「路由在、spec 里没有」或反过来「用户点完授权拿 404」。
    """
    for kwargs in (
        {},
        {"desktop": False, "feishu": True},
        {"desktop": True, "feishu": False},
    ):
        spec = build_openapi_spec(**kwargs)  # type: ignore[arg-type]
        assert set(OAUTH_PATHS) <= set(spec["paths"]), kwargs
    # 关掉它才没有 —— 骨架单独建的 app 没贴过那两条路由, spec 里也不该报。
    assert not set(OAUTH_PATHS) & set(build_openapi_spec(oauth=False)["paths"])


def test_each_gateway_gets_only_its_own_endpoints() -> None:
    tob = build_openapi_spec(desktop=False, feishu=True)
    toc = build_openapi_spec(desktop=True, feishu=False)

    assert not set(tob["paths"]) & set(DESKTOP_PATHS)
    assert set(FEISHU_PATHS) <= set(tob["paths"])
    assert not set(toc["paths"]) & set(FEISHU_PATHS)
    assert not [s for s in toc["components"]["schemas"] if s.startswith("Feishu")]
    # 公共那批两边都在, 每个 key 下 schema 与完整 spec 逐一相同。
    for spec in (tob, toc):
        assert set(CORE_PATHS) <= set(spec["paths"])
        assert all(v == OPENAPI_SPEC["paths"][k] for k, v in spec["paths"].items())


def test_every_assembled_spec_has_no_dangling_ref() -> None:
    """按开关裁掉片段后不许剩下解析不到的 $ref。"""
    for spec in (OPENAPI_SPEC, build_openapi_spec(desktop=False, feishu=True), build_openapi_spec(feishu=False)):
        components = cast(dict[str, Any], spec["components"])
        for section, name in set(re.findall(r'"#/components/(schemas|responses)/(\w+)"', json.dumps(spec))):
            assert name in components[section], f"dangling #/components/{section}/{name}"


def test_build_does_not_mutate_the_fragments() -> None:
    """装配走 dict 拷贝: 反复调用不许把片段本身改花。"""
    before = json.dumps(CORE_PATHS), json.dumps(CORE_SCHEMAS), json.dumps(FEISHU_PATHS)
    build_openapi_spec()["paths"]["/injected"] = {}
    build_openapi_spec()["components"]["schemas"]["Injected"] = {}
    assert (json.dumps(CORE_PATHS), json.dumps(CORE_SCHEMAS), json.dumps(FEISHU_PATHS)) == before
    assert "/injected" not in OPENAPI_SPEC["paths"]
