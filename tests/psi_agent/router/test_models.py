from __future__ import annotations

import pytest

from psi_agent.router.models import RouteDecision, Upstream, parse_upstreams


def test_parse_upstreams_preserves_order_and_maps_candidate() -> None:
    targets = parse_upstreams(
        [
            '{"model_name":"qwen","addr":"http://127.0.0.1:7001","description":"simple"}',
            '{"model_name":"deepseek","addr":"http://127.0.0.1:7002","description":"complex"}',
        ]
    )
    assert targets == (
        Upstream(model_name="qwen", addr="http://127.0.0.1:7001", description="simple"),
        Upstream(model_name="deepseek", addr="http://127.0.0.1:7002", description="complex"),
    )
    decision = RouteDecision(candidate=1, reason="needs reasoning")
    assert targets[decision.candidate].addr == "http://127.0.0.1:7002"


@pytest.mark.parametrize(
    ("encoded", "message"),
    [
        ("[]", "must be a JSON object"),
        ('{"model_name":"m","addr":"a"}', "missing fields"),
        ('{"model_name":"m","addr":"a","description":"d","extra":1}', "unsupported fields"),
        ('{"model_name":" ","addr":"a","description":"d"}', "model_name must be a non-empty string"),
    ],
)
def test_parse_upstreams_rejects_invalid_values(encoded: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_upstreams([encoded])


def test_parse_upstreams_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="must be valid JSON"):
        parse_upstreams(["not-json"])


def test_parse_upstreams_requires_at_least_one_value() -> None:
    with pytest.raises(ValueError, match="at least one JSON object"):
        parse_upstreams([])


def test_parse_upstreams_rejects_duplicate_model_names() -> None:
    raw = '{"model_name":"same","addr":"http://a","description":"d"}'
    with pytest.raises(ValueError, match="duplicate upstream model_name"):
        parse_upstreams([raw, raw])
