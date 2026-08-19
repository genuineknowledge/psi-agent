from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = WORKSPACE_ROOT / "tools" / "recruitment_update.py"
RESULT_KEYS = {"ok", "target", "matched_by", "fields", "message"}
CONFIG = {
    "feishu_config": {
        "app_token": "appConfigured001",
        "talent_pool_table_id": "tblTalent001",
        "interview_table_id": "tblInterview001",
        "user_key": "ou_operator_001",
        "identity": "bot",
    }
}


def _load_module() -> Any:
    name = f"recruitment_update_under_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _result(raw: str, *secrets: str) -> dict[str, Any]:
    payload = json.loads(raw)
    assert set(payload) == RESULT_KEYS
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["target"], str)
    assert isinstance(payload["matched_by"], str)
    assert isinstance(payload["fields"], list)
    assert all(isinstance(field, str) for field in payload["fields"])
    assert isinstance(payload["message"], str) and payload["message"]
    for secret in secrets:
        assert secret not in raw
    return payload


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    module = _load_module()
    config_path = tmp_path / "flows/workflows/resume-approval/resume-approval.defaults.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(CONFIG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(module._paths, "workspace_dir", lambda: str(tmp_path))

    state: dict[str, Any] = {
        "search_results": [
            {
                "ok": True,
                "records": [{"record_id": "recName001", "fields": {"姓名": "唯一候选人"}}],
                "has_more": False,
                "page_token": "",
            }
        ],
        "update_result": None,
        "read_result": None,
        "update_error": None,
        "read_error": None,
        "stored_record_id": "",
        "stored_fields": {},
    }
    calls: dict[str, list[Any]] = {"search": [], "update": [], "read": []}

    async def search(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["search"].append((args, kwargs))
        results = state["search_results"]
        if not results:
            raise AssertionError("unexpected extra search page")
        return results.pop(0)

    async def update(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["update"].append((args, kwargs))
        if state["update_error"] is not None:
            raise state["update_error"]
        state["stored_record_id"] = args[2]
        state["stored_fields"] = json.loads(args[3])
        if state["update_result"] is not None:
            return state["update_result"]
        return {"ok": True, "updated_fields": list(state["stored_fields"])}

    async def read(request: Any, **kwargs: Any) -> dict[str, Any]:
        calls["read"].append((request, kwargs))
        if state["read_error"] is not None:
            raise state["read_error"]
        if state["read_result"] is not None:
            return state["read_result"]
        return {
            "ok": True,
            "data": {
                "record": {
                    "record_id": state["stored_record_id"],
                    "fields": dict(state["stored_fields"]),
                }
            },
        }

    monkeypatch.setattr(module._feishu, "search_bitable_records_impl", search)
    monkeypatch.setattr(module._feishu, "update_bitable_record_impl", update)
    monkeypatch.setattr(module._feishu, "_invoke", read)
    return SimpleNamespace(
        module=module,
        config_path=config_path,
        state=state,
        calls=calls,
    )


@pytest.mark.anyio
async def test_tool_is_async_documented_and_exposes_target_enum(harness: SimpleNamespace) -> None:
    function = harness.module.recruitment_update_record

    assert inspect.iscoroutinefunction(function)
    assert inspect.getdoc(function)
    schema = ToolFunction.from_callable(function).parameters
    assert schema["properties"]["target"]["enum"] == ["人才库", "面试记录"]
    assert schema["required"] == ["target", "updates_json"]


@pytest.mark.anyio
async def test_explicit_record_id_wins_and_uses_only_configured_destination(harness: SimpleNamespace) -> None:
    raw = await harness.module.recruitment_update_record(
        "人才库",
        '{"初审状态":"通过","备注":"已人工确认"}',
        record_id="  recExplicit001  ",
        candidate_name="不应搜索的姓名",
        record_link="not a link",
    )

    payload = _result(raw, "recExplicit001", "ou_operator_001", "appConfigured001", "tblTalent001")
    assert payload == {
        "ok": True,
        "target": "人才库",
        "matched_by": "明确记录",
        "fields": ["备注", "初审状态"],
        "message": "已更新并读回核验 2 个业务字段。",
    }
    assert harness.calls["search"] == []
    args, kwargs = harness.calls["update"][0]
    assert kwargs == {}
    assert args == (
        "appConfigured001",
        "tblTalent001",
        "recExplicit001",
        '{"初审状态": "通过", "备注": "已人工确认"}',
        "ou_operator_001",
        "bot",
        True,
    )
    request, read_kwargs = harness.calls["read"][0]
    assert request.paths == {
        "app_token": "appConfigured001",
        "table_id": "tblTalent001",
        "record_id": "recExplicit001",
    }
    assert read_kwargs == {"user_key": "ou_operator_001"}


@pytest.mark.anyio
async def test_row_link_contributes_only_record_id(harness: SimpleNamespace) -> None:
    raw = await harness.module.recruitment_update_record(
        "面试记录",
        '{"面试纪要":"补充事实"}',
        record_link=(
            "https://attacker.feishu.cn/base/appAttacker?"
            "table=tblAttacker&view=vewAttacker&record=recLink001#private"
        ),
    )

    payload = _result(raw, "appAttacker", "tblAttacker", "recLink001")
    assert payload["ok"] is True
    assert payload["matched_by"] == "行链接"
    args, _kwargs = harness.calls["update"][0]
    assert args[:3] == ("appConfigured001", "tblInterview001", "recLink001")


@pytest.mark.anyio
async def test_exact_name_search_paginates_and_requires_one_row(harness: SimpleNamespace) -> None:
    harness.state["search_results"] = [
        {"ok": True, "records": [], "has_more": True, "page_token": "page-2"},
        {
            "ok": True,
            "records": [{"record_id": "recUnique001", "fields": {"姓名": "唯一候选人"}}],
            "has_more": False,
            "page_token": "",
        },
    ]

    raw = await harness.module.recruitment_update_record(
        "人才库",
        '{"备注":"仅更新唯一行"}',
        candidate_name="  唯一候选人  ",
    )

    payload = _result(raw, "唯一候选人", "recUnique001")
    assert payload["ok"] is True
    assert payload["matched_by"] == "姓名精确匹配"
    assert len(harness.calls["search"]) == 2
    first_kwargs = harness.calls["search"][0][1]
    second_kwargs = harness.calls["search"][1][1]
    assert first_kwargs["page_token"] == ""
    assert second_kwargs["page_token"] == "page-2"
    assert first_kwargs["field_names"] == '["姓名"]'
    assert first_kwargs["page_size"] == 500
    assert json.loads(first_kwargs["filter_json"]) == {
        "conjunction": "and",
        "conditions": [{"field_name": "姓名", "operator": "is", "value": ["唯一候选人"]}],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 2])
async def test_name_zero_or_multiple_rows_never_writes(harness: SimpleNamespace, count: int) -> None:
    record_ids = [f"recPrivate{index:03d}" for index in range(count)]
    harness.state["search_results"] = [
        {
            "ok": True,
            "records": [{"record_id": record_id, "fields": {"姓名": "敏感姓名"}} for record_id in record_ids],
            "has_more": False,
            "page_token": "",
        }
    ]

    raw = await harness.module.recruitment_update_record(
        "人才库",
        '{"备注":"不会落库的敏感值"}',
        candidate_name="敏感姓名",
    )

    payload = _result(raw, "敏感姓名", *record_ids, "不会落库的敏感值")
    assert payload["ok"] is False
    assert harness.calls["update"] == []
    if count:
        assert f"精确匹配到 {count} 行" in payload["message"]
    else:
        assert "未找到匹配记录" in payload["message"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "record_link",
    [
        "http://example.feishu.cn/base/app?record=recLink001",
        "https://example.com/base/app?record=recLink001",
        "https://user@example.feishu.cn/base/app?record=recLink001",
        "https://example.feishu.cn/base/app",
        "https://example.feishu.cn/base/app?record=recOne&record_id=recTwo",
        "https://notfeishu.cn/base/app?record=recLink001",
    ],
)
async def test_row_link_is_strictly_allowlisted(harness: SimpleNamespace, record_link: str) -> None:
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"x"}', record_link=record_link
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "updates", "expected"),
    [
        (
            "人才库",
            {"备注": "人工备注", "初审状态": "不通过"},
            {"备注": "人工备注", "初审状态": "不通过"},
        ),
        (
            "面试记录",
            {
                "面试纪要": "纪要",
                "补充信息": "补充",
                "靠谱评分": 1,
                "专业能力评分": 2,
                "学习行动评分": 3,
                "AI Native评分": 4,
                "面试定级": "A",
                "聪明人等级": "T3",
                "疑问待验证": "疑问",
                "风险验证结果": "已验证",
                "面试结论": "结论",
                "面试状态": "已完成",
                "面试时间": "2026-08-14T10:11:12+08:00",
            },
            {
                "面试纪要": "纪要",
                "补充信息": "补充",
                "靠谱评分": 1,
                "专业能力评分": 2,
                "学习行动评分": 3,
                "AI Native评分": 4,
                "面试定级": "A",
                "聪明人等级": "T3",
                "疑问待验证": "疑问",
                "风险验证结果": "已验证",
                "面试结论": "结论",
                "面试状态": "已完成",
                "面试时间": 1786673472000,
            },
        ),
    ],
)
async def test_each_target_accepts_exactly_its_full_whitelist(
    harness: SimpleNamespace,
    target: str,
    updates: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    raw = await harness.module.recruitment_update_record(
        target,
        json.dumps(updates, ensure_ascii=False),
        record_id="recWhitelist001",
    )

    assert _result(raw)["ok"] is True
    assert harness.state["stored_fields"] == expected


@pytest.mark.anyio
async def test_unknown_or_cross_table_field_rejects_entire_update(harness: SimpleNamespace) -> None:
    raw = await harness.module.recruitment_update_record(
        "人才库",
        '{"备注":"本来也合法","面试纪要":"越权字段"}',
        record_id="recNoWrite001",
    )

    payload = _result(raw, "本来也合法", "越权字段", "recNoWrite001")
    assert payload["ok"] is False
    assert payload["fields"] == []
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("value", [True, False, 1.5, 0, 6, "5", None])
async def test_scores_require_non_bool_integers_from_one_to_five(harness: SimpleNamespace, value: Any) -> None:
    raw = await harness.module.recruitment_update_record(
        "面试记录",
        json.dumps({"靠谱评分": value}, ensure_ascii=False),
        record_id="recScore001",
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        *[("人才库", "初审状态", value) for value in ("待审批", "通过", "不通过")],
        *[("面试记录", "面试定级", value) for value in ("S", "A", "B", "C")],
        *[("面试记录", "聪明人等级", value) for value in ("T1", "T2", "T3", "T4", "T5")],
        *[
            ("面试记录", "面试状态", value)
            for value in ("待安排", "待面试", "待补充", "已完成", "录用", "不录用", "待定")
        ],
    ],
)
async def test_all_declared_enum_values_are_accepted(
    harness: SimpleNamespace,
    target: str,
    field: str,
    value: str,
) -> None:
    raw = await harness.module.recruitment_update_record(
        target,
        json.dumps({field: value}, ensure_ascii=False),
        record_id="recEnum001",
    )

    assert _result(raw)["ok"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("人才库", "初审状态", "已通过"),
        ("人才库", "初审状态", 1),
        ("面试记录", "面试定级", "D"),
        ("面试记录", "聪明人等级", "T6"),
        ("面试记录", "面试状态", "通过"),
    ],
)
async def test_invalid_enum_values_are_rejected(
    harness: SimpleNamespace,
    target: str,
    field: str,
    value: Any,
) -> None:
    raw = await harness.module.recruitment_update_record(
        target,
        json.dumps({field: value}, ensure_ascii=False),
        record_id="recBadEnum001",
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    ["2026-08-14T02:11:12Z", "2026-08-14T10:11:12.123456+08:00", "2026-08-13T21:11:12-05:00"],
)
async def test_interview_time_requires_aware_iso_and_converts_to_milliseconds(
    harness: SimpleNamespace,
    value: str,
) -> None:
    raw = await harness.module.recruitment_update_record(
        "面试记录",
        json.dumps({"面试时间": value}, ensure_ascii=False),
        record_id="recTime001",
    )

    assert _result(raw)["ok"] is True
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    assert harness.state["stored_fields"]["面试时间"] == int(
        datetime.fromisoformat(normalized).timestamp() * 1000
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [
        "2026-08-14T10:11:12",
        "2026-08-14 10:11:12+08:00",
        "2026-08-14T10:11+08:00",
        "2026-02-30T10:11:12+08:00",
        "2026-08-14T10:11:12+99:00",
        1786673472000,
    ],
)
async def test_invalid_interview_times_are_rejected(harness: SimpleNamespace, value: Any) -> None:
    raw = await harness.module.recruitment_update_record(
        "面试记录",
        json.dumps({"面试时间": value}, ensure_ascii=False),
        record_id="recBadTime001",
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "updates_json"),
    [
        ("人才库", ""),
        ("人才库", "{}"),
        ("人才库", "[]"),
        ("人才库", "null"),
        ("人才库", '"text"'),
        ("人才库", '{"备注":"a","备注":"b"}'),
        ("面试记录", '{"靠谱评分":NaN}'),
        ("面试记录", '{"靠谱评分":Infinity}'),
        ("面试记录", '{"靠谱评分":-Infinity}'),
    ],
)
async def test_updates_require_strict_nonempty_json_object(
    harness: SimpleNamespace,
    target: str,
    updates_json: str,
) -> None:
    raw = await harness.module.recruitment_update_record(
        target,
        updates_json,
        record_id="recJson001",
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("value", [1, True, None, ["text"], {"text": "value"}])
async def test_text_fields_require_strings(harness: SimpleNamespace, value: Any) -> None:
    raw = await harness.module.recruitment_update_record(
        "人才库",
        json.dumps({"备注": value}, ensure_ascii=False),
        record_id="recText001",
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
async def test_text_fields_have_a_bounded_length_but_may_be_cleared(harness: SimpleNamespace) -> None:
    too_long = "x" * (harness.module._MAX_TEXT_CHARS + 1)
    rejected = await harness.module.recruitment_update_record(
        "人才库",
        json.dumps({"备注": too_long}),
        record_id="recTextLong001",
    )
    accepted = await harness.module.recruitment_update_record(
        "人才库",
        '{"备注":""}',
        record_id="recTextEmpty001",
    )

    assert _result(rejected, too_long)["ok"] is False
    assert _result(accepted)["ok"] is True
    assert len(harness.calls["update"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "secret"),
    [
        ({"record_id": "not-a-record"}, "not-a-record"),
        ({"candidate_name": ""}, ""),
        ({"candidate_name": "x" * 101}, "x" * 101),
        ({"record_link": "https://feishu.cn/base/app?record="}, "record="),
    ],
)
async def test_row_locator_validation_fails_without_writing(
    harness: SimpleNamespace,
    kwargs: dict[str, str],
    secret: str,
) -> None:
    raw = await harness.module.recruitment_update_record("人才库", '{"备注":"x"}', **kwargs)

    payload = _result(raw, secret) if secret else _result(raw)
    assert payload["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["api_error", "exception", "dropped"])
async def test_update_failure_never_claims_success_or_leaks_details(
    harness: SimpleNamespace,
    mode: str,
) -> None:
    secret = "sentinel-private-update-detail"
    if mode == "api_error":
        harness.state["update_result"] = {"ok": False, "message": secret}
    elif mode == "exception":
        harness.state["update_error"] = RuntimeError(secret)
    else:
        harness.state["update_result"] = {"ok": True, "dropped_fields": [secret]}

    raw = await harness.module.recruitment_update_record(
        "人才库",
        '{"备注":"private-field-value"}',
        record_id="recPrivateUpdate001",
    )

    payload = _result(raw, secret, "private-field-value", "recPrivateUpdate001")
    assert payload["ok"] is False
    assert harness.calls["read"] == []


@pytest.mark.anyio
async def test_update_timeout_is_bounded_and_sanitized(
    harness: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_finishes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await anyio.sleep(10)
        return {"ok": True}

    monkeypatch.setattr(harness.module, "_REQUEST_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(harness.module._feishu, "update_bitable_record_impl", never_finishes)
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"timeout-private"}', record_id="recTimeout001"
    )

    assert _result(raw, "timeout-private", "recTimeout001")["ok"] is False
    assert harness.calls["read"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "read_result",
    [
        {"ok": False, "message": "private-read-error"},
        {"ok": True, "data": {}},
        {"ok": True, "data": {"record": {"record_id": "recWrong001", "fields": {"备注": "value"}}}},
        {"ok": True, "data": {"record": {"record_id": "recRead001", "fields": {}}}},
        {
            "ok": True,
            "data": {"record": {"record_id": "recRead001", "fields": {"备注": "different-private-value"}}},
        },
    ],
)
async def test_readback_error_or_mismatch_fails_closed(
    harness: SimpleNamespace,
    read_result: dict[str, Any],
) -> None:
    harness.state["read_result"] = read_result
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"value"}', record_id="recRead001"
    )

    assert _result(
        raw,
        "recWrong001",
        "private-read-error",
        "different-private-value",
        "recRead001",
    )["ok"] is False


@pytest.mark.anyio
async def test_readback_exception_is_sanitized(harness: SimpleNamespace) -> None:
    harness.state["read_error"] = RuntimeError("private-readback-stack-and-config")
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"value"}', record_id="recReadException001"
    )

    assert _result(raw, "private-readback-stack-and-config", "recReadException001")["ok"] is False


@pytest.mark.anyio
async def test_numeric_readback_accepts_equivalent_finite_numbers(harness: SimpleNamespace) -> None:
    expected_time = 1786673472000
    harness.state["read_result"] = {
        "ok": True,
        "data": {
            "record": {
                "record_id": "recNumeric001",
                "fields": {"靠谱评分": 5.0, "面试时间": float(expected_time)},
            }
        },
    }
    raw = await harness.module.recruitment_update_record(
        "面试记录",
        '{"靠谱评分":5,"面试时间":"2026-08-14T10:11:12+08:00"}',
        record_id="recNumeric001",
    )

    assert _result(raw)["ok"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "search_result",
    [
        {"ok": False, "message": "private-search-error"},
        {"ok": True, "records": {}, "has_more": False},
        {"ok": True, "records": [{}], "has_more": False},
        {"ok": True, "records": [{"record_id": "invalid-private-id"}], "has_more": False},
        {"ok": True, "records": [{"record_id": "recValid001"}], "has_more": False},
        {
            "ok": True,
            "records": [{"record_id": "recValid001", "fields": {"姓名": 1}}],
            "has_more": False,
        },
        {
            "ok": True,
            "records": [{"record_id": "recValid001", "fields": {"姓名": "other-name"}}],
            "has_more": False,
        },
        {"ok": True, "records": [], "has_more": "false"},
        {"ok": True, "records": [], "has_more": True, "page_token": ""},
    ],
)
async def test_malformed_search_response_is_remote_failure(
    harness: SimpleNamespace,
    search_result: dict[str, Any],
) -> None:
    harness.state["search_results"] = [search_result]
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"x"}', candidate_name="private-name"
    )

    payload = _result(raw, "private-search-error", "invalid-private-id", "private-name")
    assert payload["ok"] is False
    assert "更新或读回核验失败" in payload["message"]
    assert harness.calls["update"] == []


@pytest.mark.anyio
async def test_repeated_search_page_token_is_rejected(harness: SimpleNamespace) -> None:
    harness.state["search_results"] = [
        {"ok": True, "records": [], "has_more": True, "page_token": "same"},
        {"ok": True, "records": [], "has_more": True, "page_token": "same"},
    ]
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"x"}', candidate_name="name"
    )

    assert _result(raw)["ok"] is False
    assert harness.calls["update"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "config_value",
    [
        None,
        [],
        {},
        {"feishu_config": []},
        {"feishu_config": {}},
        {"feishu_config": {**CONFIG["feishu_config"], "app_token": "bad token"}},
        {"feishu_config": {**CONFIG["feishu_config"], "talent_pool_table_id": ""}},
        {"feishu_config": {**CONFIG["feishu_config"], "user_key": "bad key"}},
        {"feishu_config": {**CONFIG["feishu_config"], "identity": "auto"}},
    ],
)
async def test_missing_or_malformed_configuration_never_calls_feishu(
    harness: SimpleNamespace,
    config_value: Any,
) -> None:
    if config_value is None:
        harness.config_path.unlink()
    else:
        harness.config_path.write_text(json.dumps(config_value, ensure_ascii=False), encoding="utf-8")

    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"x"}', record_id="recConfig001"
    )

    assert _result(raw, "recConfig001")["ok"] is False
    assert harness.calls == {"search": [], "update": [], "read": []}


@pytest.mark.anyio
async def test_invalid_json_configuration_never_calls_feishu(harness: SimpleNamespace) -> None:
    harness.config_path.write_text('{"feishu_config": NaN}', encoding="utf-8")

    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"x"}', record_id="recConfigJson001"
    )

    assert _result(raw, "recConfigJson001")["ok"] is False
    assert harness.calls == {"search": [], "update": [], "read": []}


@pytest.mark.anyio
async def test_result_never_echoes_locator_config_exception_or_field_value(harness: SimpleNamespace) -> None:
    secrets = (
        "recPrivateSecret001",
        "candidate-private-name",
        "private-field-value@example.com",
        "appConfigured001",
        "tblTalent001",
        "ou_operator_001",
        "private-exception-stack",
    )
    harness.state["update_error"] = RuntimeError("private-exception-stack")
    raw = await harness.module.recruitment_update_record(
        "人才库",
        '{"备注":"private-field-value@example.com"}',
        record_id="recPrivateSecret001",
        candidate_name="candidate-private-name",
    )

    payload = _result(raw, *secrets)
    assert payload["fields"] == ["备注"]
    assert payload["ok"] is False


@pytest.mark.anyio
async def test_unexpected_internal_exception_is_also_sanitized(
    harness: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_target: str, _updates: str) -> dict[str, Any]:
        raise RuntimeError("unexpected-private-stack")

    monkeypatch.setattr(harness.module, "_validate_updates", explode)
    raw = await harness.module.recruitment_update_record(
        "人才库", '{"备注":"x"}', record_id="recUnexpected001"
    )

    assert _result(raw, "unexpected-private-stack", "recUnexpected001")["ok"] is False


@pytest.mark.anyio
async def test_non_string_runtime_arguments_return_fixed_safe_shape(harness: SimpleNamespace) -> None:
    target: Any = ["人才库"]
    raw = await harness.module.recruitment_update_record(
        target,
        '{"备注":"x"}',
    )

    payload = _result(raw)
    assert payload["ok"] is False
    assert payload["target"] == "未识别目标"
    assert harness.calls == {"search": [], "update": [], "read": []}
