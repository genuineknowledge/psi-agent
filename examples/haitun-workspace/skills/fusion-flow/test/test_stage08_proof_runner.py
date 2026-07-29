from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
_RUNNER_PATH = _WORKSPACE_DIR / "skills" / "stage08-catalytic-performance-prover" / "LLM_proof" / "run_llm_proof.py"


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("stage08_proof_runner", _RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Stage08 proof runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Any, module)


def test_proof_runner_does_not_send_openai_key_to_default_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    monkeypatch.delenv("LLM_PROOF_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")

    with pytest.raises(RuntimeError, match="LLM_PROOF_API_KEY"):
        runner.api_settings(
            argparse.Namespace(api_key=None, model=None, base_url=None),
            require_api_key=True,
        )


class _FakeResponse:
    status = 200

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": "proof result"}}]}

    async def text(self) -> str:
        return ""

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.url = ""
        self.headers: dict[str, str] = {}
        self.payload: dict[str, object] = {}

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> _FakeResponse:
        self.url = url
        self.headers = headers
        self.payload = json
        return _FakeResponse()


@pytest.mark.anyio
async def test_proof_runner_uses_existing_http_dependency() -> None:
    runner = _load_runner()
    session = _FakeSession()

    content = await runner.request_completion(
        session=cast(Any, session),
        base_url="https://example.test/v1/",
        api_key="test-key",
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
        temperature=0.5,
        max_tokens=123,
    )

    assert content == "proof result"
    assert session.url == "https://example.test/v1/chat/completions"
    assert session.headers["Authorization"] == "Bearer test-key"
    assert session.payload["model"] == "test-model"
    assert session.payload["max_tokens"] == 123
