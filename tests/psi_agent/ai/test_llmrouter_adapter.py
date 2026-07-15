from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path

import anyio
import pytest
import yaml
from llmrouter import prompts as llm_prompts

import psi_agent.ai.llmrouter_adapter as adapter_module
from psi_agent.ai.llmrouter_adapter import LLMRouterAdapter, RouteTarget, parse_upstreams, serialize_context

_REQUIRED_PROMPTS = ("agent_decomp_route.yaml", "agent_decomp_cot.yaml", "agent_prompt.yaml")


def test_packaged_custom_tasks_are_valid_templates() -> None:
    custom_tasks = files("psi_agent.ai").joinpath("custom_tasks")

    for filename in _REQUIRED_PROMPTS:
        resource = custom_tasks.joinpath(filename)
        assert resource.is_file(), filename
        payload = yaml.safe_load(resource.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert isinstance(payload.get("template"), str)
        assert payload["template"].strip()


def test_build_router_configures_packaged_custom_tasks_before_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, str] = {}

    class FakeRouter:
        def __init__(self, *, yaml_path: str) -> None:
            observed["yaml_path"] = yaml_path
            self.base_model = "router-small"
            self.llm_data = {"reasoner": {"feature": "Reasoning", "model": "reasoner"}}
            custom_tasks = Path(str(llm_prompts._CUSTOM_TASKS_DIR))
            project_root = Path(str(llm_prompts._PROJECT_ROOT))
            assert custom_tasks.name == "custom_tasks"
            assert custom_tasks.parent == project_root
            observed["template"] = llm_prompts.load_prompt_template("agent_decomp_route")

    monkeypatch.setattr(adapter_module, "LLMMultiRoundRouter", FakeRouter)

    router = LLMRouterAdapter._build_router_sync(
        "runtime.yaml",
        "router-small",
        "https://router.example/v1",
    )

    assert isinstance(router, FakeRouter)
    assert observed["yaml_path"] == "runtime.yaml"
    assert observed["template"].strip()


def test_build_router_injects_cli_endpoint_after_candidate_prompt_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    construction_keys: tuple[str, ...] = ()

    class FakeRouter:
        def __init__(self, *, yaml_path: str) -> None:
            nonlocal construction_keys
            assert yaml_path == "runtime.yaml"
            self.base_model = "router-small"
            self.llm_data = {
                "reasoner": {"feature": "Reasoning", "model": "reasoner"},
                "coder": {"feature": "Coding", "model": "coder"},
            }
            construction_keys = tuple(self.llm_data)

    monkeypatch.setattr(adapter_module, "LLMMultiRoundRouter", FakeRouter)

    router = LLMRouterAdapter._build_router_sync(
        "runtime.yaml",
        "router-small",
        "https://router.example/v1",
    )

    assert construction_keys == ("reasoner", "coder")
    assert router.llm_data["router-small"] == {
        "model": "router-small",
        "api_endpoint": "https://router.example/v1",
    }


def test_build_router_preserves_same_name_candidate_description(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRouter:
        def __init__(self, *, yaml_path: str) -> None:
            assert yaml_path == "runtime.yaml"
            self.base_model = "router-small"
            self.llm_data = {
                "router-small": {
                    "feature": "Selectable general model",
                    "model": "router-small",
                }
            }

    monkeypatch.setattr(adapter_module, "LLMMultiRoundRouter", FakeRouter)

    router = LLMRouterAdapter._build_router_sync(
        "runtime.yaml",
        "router-small",
        "https://router.example/v1",
    )

    assert router.llm_data["router-small"] == {
        "feature": "Selectable general model",
        "model": "router-small",
        "api_endpoint": "https://router.example/v1",
    }


@pytest.mark.parametrize(
    ("base_model", "llm_data", "message"),
    [
        ("wrong-model", {}, "base_model mismatch"),
        ("router-small", [], "llm_data must be a mutable dictionary"),
    ],
)
def test_build_router_rejects_incompatible_instance_state(
    base_model: str,
    llm_data: object,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRouter:
        def __init__(self, *, yaml_path: str) -> None:
            assert yaml_path == "runtime.yaml"
            self.base_model = base_model
            self.llm_data = llm_data

    monkeypatch.setattr(adapter_module, "LLMMultiRoundRouter", FakeRouter)

    with pytest.raises(RuntimeError, match=message):
        LLMRouterAdapter._build_router_sync(
            "runtime.yaml",
            "router-small",
            "https://router.example/v1",
        )


def test_build_router_reports_missing_packaged_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePackage:
        @staticmethod
        def joinpath(name: str) -> Path:
            assert name == "custom_tasks"
            return tmp_path

    class UnexpectedRouter:
        def __init__(self, *, yaml_path: str) -> None:
            pytest.fail(f"Router construction must not run for missing prompts: {yaml_path}")

    monkeypatch.setattr(adapter_module, "files", lambda package: FakePackage())
    monkeypatch.setattr(adapter_module, "LLMMultiRoundRouter", UnexpectedRouter)

    with pytest.raises(FileNotFoundError, match=r"agent_decomp_route\.yaml"):
        LLMRouterAdapter._build_router_sync(
            "runtime.yaml",
            "router-small",
            "https://router.example/v1",
        )


def test_parse_upstreams_accepts_independent_json_objects_in_order() -> None:
    raw = [
        json.dumps({"addr": "./qwen.sock", "model": "qwen-plus", "description": "General tasks"}),
        json.dumps(
            {
                "addr": "./reasoner.sock",
                "model": "deepseek-reasoner",
                "description": "Complex reasoning",
            }
        ),
    ]

    assert parse_upstreams(raw) == [
        RouteTarget("./qwen.sock", "qwen-plus", "General tasks"),
        RouteTarget("./reasoner.sock", "deepseek-reasoner", "Complex reasoning"),
    ]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "at least one"),
        (["not-json"], r"upstream\[0\].*valid JSON"),
        (["[]"], r"upstream\[0\].*object"),
        (["{}"], r"upstream\[0\].*addr"),
        (
            ['{"addr":"a","model":"m","description":"d","api_key":"secret"}'],
            "unsupported fields",
        ),
        (
            [
                '{"addr":"a","model":"m","description":"d"}',
                '{"addr":"b","model":"m","description":"d2"}',
            ],
            "duplicate upstream model",
        ),
    ],
)
def test_parse_upstreams_rejects_invalid_values(raw: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_upstreams(raw)


def test_parse_upstreams_reports_json_error_location_and_powershell_hint() -> None:
    raw = [
        '{"addr":"a","model":"m","description":"d"}',
        "{addr:http://127.0.0.1:8101,model:qwen-plus,description:general}",
    ]

    with pytest.raises(ValueError) as error:
        parse_upstreams(raw)

    message = str(error.value)
    assert "upstream[1]" in message
    assert "line 1 column 2" in message
    assert "PowerShell" in message
    assert r"\"addr\"" in message


def test_serialize_context_keeps_context_and_omits_tool_bodies() -> None:
    messages = [
        {"role": "system", "content": "Python framework"},
        {"role": "user", "content": "Find the cancellation leak"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file", "arguments": "secret"}}]},
        {"role": "tool", "content": "large sensitive result"},
        {"role": "user", "content": "Now propose the patch"},
    ]
    result = serialize_context(messages, max_chars=1_000)

    assert "[SYSTEM]\nPython framework" in result
    assert "Find the cancellation leak" in result
    assert "Now propose the patch" in result
    assert "read_file" in result
    assert "large sensitive result" not in result
    assert "secret" not in result


def test_serialize_context_truncates_to_budget() -> None:
    result = serialize_context([{"role": "user", "content": "x" * 100}], max_chars=32)
    assert len(result) <= 32
    assert result.endswith("[TRUNCATED]")


def test_serialize_context_requires_user_text() -> None:
    assert serialize_context([{"role": "assistant", "content": "hello"}], max_chars=100) == ""


@pytest.mark.anyio
async def test_adapter_builds_once_routes_and_restores_api_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []

    class FakeRouter:
        def _decompose_and_route(self, context: str) -> list[tuple[str, str]]:
            assert os.environ["API_KEYS"] == "router-secret"
            return [(context, "reasoner"), ("write", "coder"), ("verify", "reasoner")]

    def fake_build(path: str, router_model: str, router_base_url: str) -> FakeRouter:
        built.append(path)
        assert router_model == "router-small"
        assert router_base_url == "https://router.example/v1"
        return FakeRouter()

    monkeypatch.setattr(LLMRouterAdapter, "_build_router_sync", staticmethod(fake_build))
    monkeypatch.setenv("API_KEYS", "original")
    adapter = LLMRouterAdapter(
        router_model="router-small",
        router_base_url="https://router.example/v1",
        router_api_key="router-secret",
        targets=[
            RouteTarget("./reasoner.sock", "reasoner", "Reasoning"),
            RouteTarget("./coder.sock", "coder", "Coding"),
        ],
        runtime_root=str(tmp_path),
    )

    await adapter.start()
    decision = await adapter.route("analyze")

    assert len(built) == 1
    assert decision.target.model == "reasoner"
    assert decision.votes == {"reasoner": 2, "coder": 1}
    assert os.environ["API_KEYS"] == "original"
    runtime = yaml.safe_load(await adapter.runtime_yaml.read_text(encoding="utf-8"))
    data = json.loads(await adapter.llm_data.read_text(encoding="utf-8"))
    assert runtime["base_model"] == "router-small"
    assert data == {
        "reasoner": {"feature": "Reasoning", "model": "reasoner"},
        "coder": {"feature": "Coding", "model": "coder"},
    }
    assert "router-secret" not in await adapter.llm_data.read_text(encoding="utf-8")
    await adapter.close()
    assert await anyio.Path(str(tmp_path)).exists()
    assert not await adapter.runtime_dir.exists()


@pytest.mark.anyio
async def test_adapter_routes_with_cli_base_endpoint_and_candidate_only_disk_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRouter:
        def __init__(self, *, yaml_path: str) -> None:
            runtime = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
            self.base_model = runtime["base_model"]
            self.api_endpoint = runtime["api_endpoint"]
            self.llm_data = json.loads(Path(runtime["data_path"]["llm_data"]).read_text(encoding="utf-8"))
            self.candidate_keys = tuple(self.llm_data)

        def _decompose_and_route(self, context: str) -> list[tuple[str, str]]:
            assert context == "route this"
            assert os.environ["API_KEYS"] == "router-secret"
            assert self.candidate_keys == ("reasoner", "coder")
            assert self.llm_data["router-small"] == {
                "model": "router-small",
                "api_endpoint": "https://router.example/v1",
            }
            return [("analyze", "reasoner"), ("implement", "coder"), ("verify", "reasoner")]

    monkeypatch.setattr(adapter_module, "LLMMultiRoundRouter", FakeRouter)
    adapter = LLMRouterAdapter(
        router_model="router-small",
        router_base_url="https://router.example/v1",
        router_api_key="router-secret",
        targets=[
            RouteTarget("./reasoner.sock", "reasoner", "Reasoning"),
            RouteTarget("./coder.sock", "coder", "Coding"),
        ],
        runtime_root=str(tmp_path),
    )

    await adapter.start()
    disk_data = json.loads(await adapter.llm_data.read_text(encoding="utf-8"))
    runtime_text = await adapter.runtime_yaml.read_text(encoding="utf-8")
    decision = await adapter.route("route this")

    assert tuple(disk_data) == ("reasoner", "coder")
    assert "router-small" not in disk_data
    assert "router-secret" not in runtime_text
    assert "router-secret" not in await adapter.llm_data.read_text(encoding="utf-8")
    assert decision.target.model == "reasoner"
    assert decision.source == "llmrouter_majority"
    await adapter.close()
