"""Tests for the generic ``feishu_api`` tool and the ``feishu_chart`` dispatcher.

Both replace a pile of narrow tools with one parameterised entry point, so what needs
covering is the *translation*: that plain JSON arguments become the request the
dedicated tools would have built, and that a caller who gets the shape wrong is told
the accepted shape instead of being handed a Feishu 400.

Assertions land on the outgoing ``BaseRequest`` (method / uri / paths / queries /
body / token types), never on intent — a tool that builds the wrong request while
reporting success is exactly the failure these guard against.
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from lark_channel.core.enum import AccessTokenType, HttpMethod

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_api: Any = importlib.import_module("_feishu_api_impl")
_tool: Any = importlib.import_module("feishu_api")
_chart: Any = importlib.import_module("feishu_chart")


class _CapturedInvoke:
    """Stand-in for ``_feishu_impl._invoke`` that records the request it was given."""

    def __init__(self, data: dict[str, Any] | None = None, ok: bool = True, code: int = 0) -> None:
        self._data = data if data is not None else {}
        self._ok = ok
        self._code = code
        self.requests: list[Any] = []
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        self.kwargs.append(kwargs)
        if self._ok:
            return {"ok": True, "data": self._data}
        return {"ok": False, "code": self._code, "message": "denied"}

    @property
    def request(self) -> Any:
        assert len(self.requests) == 1, f"expected exactly one call, got {len(self.requests)}"
        return self.requests[0]


def _qdict(req: Any) -> dict[str, str]:
    """SDK stores queries as list[tuple[str, str]]."""
    return dict(req.queries)


@pytest.mark.asyncio
async def test_get_builds_request_with_paths_and_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"user": {"name": "罗霖"}})
    monkeypatch.setattr(_impl, "_invoke", cap)

    res = await _api.call_api_impl(
        method="get",
        uri="/open-apis/contact/v3/users/:user_id",
        paths_json='{"user_id":"ou_abc"}',
        query_json='{"user_id_type":"open_id","page_size":50}',
        user_key="ou_sender",
    )

    req = cap.request
    assert req.http_method is HttpMethod.GET
    assert req.uri == "/open-apis/contact/v3/users/:user_id"
    assert req.paths == {"user_id": "ou_abc"}
    assert _qdict(req) == {"user_id_type": "open_id", "page_size": "50"}
    assert req.token_types == {AccessTokenType.TENANT, AccessTokenType.USER}
    assert cap.kwargs[0] == {"user_key": "ou_sender", "prefer": "tenant", "identity": ""}
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_post_sends_body_and_lowercase_method_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"task": {"guid": "t1"}})
    monkeypatch.setattr(_impl, "_invoke", cap)

    await _api.call_api_impl(
        method="post",
        uri="/open-apis/task/v2/tasks",
        body_json='{"summary":"写周报","members":[{"id":"ou_a","role":"assignee"}]}',
    )

    req = cap.request
    assert req.http_method is HttpMethod.POST
    assert req.body == {"summary": "写周报", "members": [{"id": "ou_a", "role": "assignee"}]}


@pytest.mark.asyncio
async def test_prefer_user_selects_the_user_send_path(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)

    await _api.call_api_impl(
        method="POST",
        uri="/open-apis/suite/docs-api/search/object",
        body_json='{"search_key":"SOP"}',
        prefer="user",
        identity="user",
        user_key="ou_sender",
    )

    # ``prefer`` is what routes to the UAT send; the request advertises both candidates so
    # the same request stays sendable as tenant. Each send path sets only its own token, and
    # the SDK's verify() picks the type whose token is actually present — it does not try the
    # bot token first.
    assert cap.request.token_types == {AccessTokenType.TENANT, AccessTokenType.USER}
    assert cap.kwargs[0]["prefer"] == "user"
    assert cap.kwargs[0]["identity"] == "user"


@pytest.mark.asyncio
async def test_bool_and_list_query_values(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)

    # Deliberately an endpoint no rule governs: this test is about stringification, and a
    # rule would add its own defaults and paging to the comparison. It used to point at
    # ``/task/v2/tasks``, which was ungoverned only until that domain moved into a table.
    await _api.call_api_impl(
        method="GET",
        uri="/open-apis/optical_char_recognition/v1/image/basic_recognize",
        query_json='{"completed":false,"ids":["a","b"],"skip":null}',
    )

    # Python's False would serialise as "False", which Feishu rejects; a list repeats
    # the key rather than becoming "['a', 'b']"; null drops out entirely.
    assert cap.request.queries == [("completed", "false"), ("ids", "a"), ("ids", "b")]


@pytest.mark.asyncio
async def test_upload_endpoints_are_refused_with_the_tool_that_works() -> None:
    for uri, expected in [
        ("/open-apis/im/v1/images", "feishu_message_send_image"),
        ("/open-apis/im/v1/files", "feishu_message_send_file"),
        ("/open-apis/drive/v1/medias/upload_all", "feishu_drive_upload"),
    ]:
        res = await _api.call_api_impl(method="POST", uri=uri, body_json='{"image_type":"message"}')
        assert res["ok"] is False
        assert res["code"] == "use_dedicated_tool"
        assert expected in res["tool"]


@pytest.mark.asyncio
async def test_unfilled_placeholder_is_caught_before_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)

    res = await _api.call_api_impl(method="GET", uri="/open-apis/contact/v3/users/:user_id")

    assert res["ok"] is False
    assert res["code"] == "missing_path_params"
    assert "user_id" in res["message"]
    assert cap.requests == [], "must not reach the API with an unsubstituted :placeholder"


@pytest.mark.asyncio
async def test_malformed_arguments_are_rejected() -> None:
    bad_method = await _api.call_api_impl(method="FETCH", uri="/open-apis/x")
    assert bad_method["ok"] is False and "method must be one of" in bad_method["message"]

    full_url = await _api.call_api_impl(method="GET", uri="https://open.feishu.cn/open-apis/x")
    assert full_url["ok"] is False and "must be a path" in full_url["message"]

    no_prefix = await _api.call_api_impl(method="GET", uri="/contact/v3/users")
    assert no_prefix["ok"] is False and "/open-apis/" in no_prefix["message"]

    bad_body = await _api.call_api_impl(method="POST", uri="/open-apis/a/b", body_json="{oops")
    assert bad_body["ok"] is False and "not valid JSON" in bad_body["message"]

    array_body = await _api.call_api_impl(method="POST", uri="/open-apis/a/b", body_json="[1,2]")
    assert array_body["ok"] is False and "must be a JSON object" in array_body["message"]


@pytest.mark.asyncio
async def test_leading_slash_is_added(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)

    await _api.call_api_impl(method="GET", uri="open-apis/im/v1/chats")

    assert cap.request.uri == "/open-apis/im/v1/chats"


@pytest.mark.asyncio
async def test_known_error_code_gets_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # 230002 is in one of _feishu_impl's hint tables; the generic path should reuse
    # them rather than hand back a bare code the caller has to look up.
    code = next(iter(_api._ALL_HINTS))
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke(ok=False, code=code))

    res = await _api.call_api_impl(method="GET", uri="/open-apis/im/v1/chats")

    assert res["ok"] is False
    assert res["hint"] == _api._ALL_HINTS[code]


@pytest.mark.asyncio
async def test_sheet_write_failure_suggests_the_dedicated_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke(ok=False, code=99999))

    res = await _api.call_api_impl(
        method="POST",
        uri="/open-apis/sheets/v2/spreadsheets/shtX/values_append",
        body_json='{"valueRange":{"range":"!A1"}}',
    )

    assert res["ok"] is False
    assert "feishu_sheet_write" in res["warning"]


@pytest.mark.asyncio
async def test_tool_wrapper_returns_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({"ok": 1}))

    out = await _tool.feishu_api(method="GET", uri="/open-apis/im/v1/chats")

    assert isinstance(out, str)
    assert json.loads(out)["ok"] is True


# ── feishu_chart dispatcher ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chart_dispatch_reaches_the_matching_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_pie(**kwargs: Any) -> str:
        seen.update(kwargs)
        return json.dumps({"ok": True, "image_path": "p.png"})

    monkeypatch.setitem(_chart._CHART_SPECS["pie"], "fn", fake_pie)

    out = await _chart.feishu_chart(
        chart_type="PIE",  # case-insensitive
        data_json='{"labels_json":["研发","市场"],"values_json":[42,28]}',
        options_json='{"unit":"人","show_values":true}',
        title="人力分布",
        document_id="doc1",
        caption="人力分布",
        user_key="ou_a",
    )

    assert json.loads(out)["ok"] is True
    # Arrays are re-serialised to the JSON strings the renderers parse themselves —
    # compare parsed, since json.dumps' separators are not part of the contract.
    assert json.loads(seen["labels_json"]) == ["研发", "市场"]
    assert json.loads(seen["values_json"]) == [42, 28]
    assert seen["unit"] == "人" and seen["show_values"] is True
    assert seen["title"] == "人力分布" and seen["document_id"] == "doc1"
    assert seen["user_key"] == "ou_a"


@pytest.mark.asyncio
async def test_chart_accepts_data_values_already_given_as_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_bar(**kwargs: Any) -> str:
        seen.update(kwargs)
        return json.dumps({"ok": True})

    monkeypatch.setitem(_chart._CHART_SPECS["bar"], "fn", fake_bar)

    await _chart.feishu_chart(chart_type="bar", data_json='{"labels_json":"[\\"a\\"]","values_json":"[1]"}')

    assert seen["labels_json"] == '["a"]'
    assert seen["values_json"] == "[1]"


@pytest.mark.asyncio
async def test_chart_rejects_wrong_shape_naming_the_accepted_keys() -> None:
    unknown = json.loads(await _chart.feishu_chart(chart_type="piechart", data_json="{}"))
    assert unknown["ok"] is False and "unknown chart_type" in unknown["message"]

    figure = json.loads(await _chart.feishu_chart(chart_type="figure", data_json="{}"))
    assert figure["ok"] is False and "feishu_chart_figure" in figure["message"]

    missing = json.loads(await _chart.feishu_chart(chart_type="pie", data_json='{"labels_json":["a"]}'))
    assert missing["ok"] is False and "values_json" in missing["message"]

    extra = json.loads(
        await _chart.feishu_chart(chart_type="pie", data_json='{"labels_json":["a"],"values_json":[1],"nope":1}')
    )
    assert extra["ok"] is False and "nope" in extra["message"]

    bad_opt = json.loads(
        await _chart.feishu_chart(
            chart_type="pie",
            data_json='{"labels_json":["a"],"values_json":[1]}',
            options_json='{"percent":true}',
        )
    )
    # `percent` is real — but on stacked_area, not pie. Silently ignoring it would
    # render a chart the caller didn't ask for.
    assert bad_opt["ok"] is False and "percent" in bad_opt["message"]

    bad_json = json.loads(await _chart.feishu_chart(chart_type="pie", data_json="{oops"))
    assert bad_json["ok"] is False and "not valid JSON" in bad_json["message"]

    as_array = json.loads(await _chart.feishu_chart(chart_type="pie", data_json="[1,2]"))
    assert as_array["ok"] is False and "must be a JSON object" in as_array["message"]


@pytest.mark.asyncio
async def test_every_chart_spec_matches_its_renderer_signature() -> None:
    """The spec table is the only map from chart_type to renderer — a typo in a key
    name would surface as a TypeError at call time, so check them against the real
    signatures here instead."""
    for kind, spec in _chart._CHART_SPECS.items():
        params = inspect.signature(spec["fn"]).parameters
        for key in list(spec["data"]) + list(spec["opts"]):
            assert key in params, f"chart_type={kind!r} lists {key!r}, absent from {spec['fn'].__name__}"
        required = [name for name, p in params.items() if p.default is inspect.Parameter.empty and name != "self"]
        assert sorted(required) == sorted(spec["data"]), (
            f"chart_type={kind!r} data keys {spec['data']} != renderer's required args {required}"
        )


@pytest.mark.asyncio
async def test_all_twentyone_types_render_for_real(tmp_path: Path) -> None:
    """One real render per chart type through the dispatcher — the collapse is only
    correct if every type still produces a file."""
    data = {
        "pie": '{"labels_json":["a","b"],"values_json":[3,1]}',
        "donut": '{"labels_json":["a","b"],"values_json":[3,1]}',
        "funnel": '{"stages_json":["访问","付费"],"values_json":[100,20]}',
        "line": '{"labels_json":["1月","2月"],"series_json":{"A":[1,2]}}',
        "area": '{"labels_json":["1月","2月"],"series_json":{"A":[1,2]}}',
        "stacked_area": '{"labels_json":["Q1","Q2"],"series_json":{"a":[1,2],"b":[2,3]}}',
        "column": '{"labels_json":["研发","市场"],"values_json":[42,28]}',
        "bar": '{"labels_json":["华东","华北"],"values_json":[520,310]}',
        "grouped_column": '{"labels_json":["Q1","Q2"],"series_json":{"a":[1,2],"b":[2,3]}}',
        "stacked_column": '{"labels_json":["Q1","Q2"],"series_json":{"a":[1,2],"b":[2,3]}}',
        "waterfall": '{"labels_json":["期初","增长"],"deltas_json":[100,20]}',
        "histogram": '{"values_json":[1,2,2,3,3,3,4]}',
        "box": '{"groups_json":{"A":[1,2,3,4],"B":[2,3,4,5]}}',
        "scatter": '{"points_json":[[1,2],[2,3],[3,5]]}',
        "bubble": '{"points_json":[[1,2,10],[2,3,20]]}',
        "heatmap": '{"row_labels_json":["A","B"],"col_labels_json":["X","Y"],"values_json":[[1,2],[3,4]]}',
        "radar": '{"axes_json":["速度","质量","成本"],"series_json":{"A":[3,4,5]}}',
        "pareto": '{"labels_json":["a","b","c"],"values_json":[120,85,42]}',
        "combo": (
            '{"labels_json":["1月","2月"],"bar_series_json":{"营收":[120,145]},"line_series_json":{"毛利率":[32,35]}}'
        ),
        "gantt": '{"tasks_json":[{"name":"设计","start":"2026-08-01","end":"2026-08-10"}]}',
        "progress": '{"items_json":{"完成率":0.7}}',
    }
    assert set(data) == set(_chart._CHART_SPECS), "a chart type lost its coverage here"

    for kind, payload in data.items():
        out = json.loads(await _chart.feishu_chart(chart_type=kind, data_json=payload, title="t"))
        assert out["ok"] is True, f"chart_type={kind!r} failed: {out.get('message')}"
        assert await anyio.Path(out["image_path"]).is_file(), f"chart_type={kind!r} produced no file"
