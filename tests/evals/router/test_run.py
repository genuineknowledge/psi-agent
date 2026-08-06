"""Executable Router evaluator contracts."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web

from evals.router.run import (
    EvaluationProtocolError,
    StreamState,
    _consume_payload,
    _load_inputs,
    _tool_calls_are_complete,
    run_evaluation,
)


def _sse_event(
    *,
    content: str = "",
    finish_reason: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
    choices: list[dict[str, Any]] | None = None,
) -> str:
    if choices is None:
        delta: dict[str, Any] = {}
        if content:
            delta["content"] = content
        if tool_calls is not None:
            delta["tool_calls"] = tool_calls
        choices = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    return json.dumps({"choices": choices, "usage": usage})


def test_payload_parser_skips_usage_heartbeats_and_keeps_real_finish_over_compaction() -> None:
    state = StreamState(started=time.perf_counter())

    _consume_payload(
        payload=_sse_event(
            choices=[],
            usage={"prompt_tokens": 12, "completion_tokens": 3},
        ),
        state=state,
    )
    _consume_payload(payload=_sse_event(content="answer", finish_reason="stop"), state=state)
    _consume_payload(payload=_sse_event(finish_reason="compaction_needed"), state=state)

    assert state.content_parts == ["answer"]
    assert state.finish_reason == "stop"
    assert state.visible_usage == {"prompt_tokens": 12, "completion_tokens": 3}
    assert state.ttft_ms is not None


def test_payload_parser_accumulates_fragmented_complete_tool_calls() -> None:
    state = StreamState(started=time.perf_counter())

    _consume_payload(
        payload=_sse_event(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q"'},
                }
            ]
        ),
        state=state,
    )
    _consume_payload(
        payload=_sse_event(
            tool_calls=[{"index": 0, "function": {"arguments": ':"value"}'}}],
            finish_reason="tool_calls",
        ),
        state=state,
    )

    assert _tool_calls_are_complete(state.tool_calls) is True
    assert state.tool_calls[0]["function"]["arguments"] == '{"q":"value"}'
    assert state.finish_reason == "tool_calls"


def test_payload_parser_rejects_multiple_choices() -> None:
    state = StreamState(started=time.perf_counter())
    choices = [
        {"index": 0, "delta": {}, "finish_reason": "stop"},
        {"index": 1, "delta": {}, "finish_reason": "stop"},
    ]

    with pytest.raises(EvaluationProtocolError, match="at most one choice"):
        _consume_payload(payload=_sse_event(choices=choices), state=state)


@pytest.mark.anyio
async def test_example_config_and_cases_remain_executable() -> None:
    repository = Path(__file__).parents[3]

    config, cases = await _load_inputs(
        config_path=str(repository / "evals" / "router" / "config.example.json"),
        cases_path=str(repository / "evals" / "router" / "cases.example.jsonl"),
    )

    assert len(config.conditions) == 8
    assert len(cases) == 10
    assert {case.scenario for case in cases} == {"routing", "aggregation", "fallback", "composition"}


@pytest.mark.anyio
async def test_run_evaluation_records_real_sse_and_refuses_implicit_overwrite(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        received.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        chunks = [
            _sse_event(content="42", finish_reason="stop"),
            _sse_event(
                choices=[],
                usage={"prompt_tokens": 9, "completion_tokens": 1},
            ),
            _sse_event(finish_reason="compaction_needed"),
        ]
        for chunk in chunks:
            await response.write(f"data: {chunk}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    await web.SockSite(runner, server_socket).start()
    url = f"http://127.0.0.1:{server_socket.getsockname()[1]}/chat/completions"

    root = anyio.Path(str(tmp_path))
    config_path = root / "config.json"
    cases_path = root / "cases.jsonl"
    output_path = root / "results.jsonl"
    await config_path.write_text(
        json.dumps(
            {
                "conditions": [{"name": "routing", "url": url, "request_overrides": {}}],
                "request": {"messages": [{"role": "system", "content": "shared"}], "temperature": 0},
                "repetitions": 1,
                "timeout_seconds": 5,
                "seed": 7,
            }
        ),
        encoding="utf-8",
    )
    await cases_path.write_text(
        json.dumps(
            {
                "id": "case-1",
                "scenario": "routing",
                "prompt": "answer",
                "grader": {"type": "exact", "answer": "42"},
                "expected_route": "candidate-1",
                "tags": ["automatic"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        await run_evaluation(
            config_path=str(config_path),
            cases_path=str(cases_path),
            output_path=str(output_path),
        )
        record = json.loads(await output_path.read_text(encoding="utf-8"))

        assert record["protocol_success"] is True
        assert record["clean_success"] is True
        assert record["score"] == 1.0
        assert record["finish_reason"] == "stop"
        assert record["visible_input_tokens"] == 9
        assert record["visible_output_tokens"] == 1
        assert record["ttft_ms"] is not None
        assert received[0]["stream"] is True
        assert received[0]["messages"][-1] == {"role": "user", "content": "answer"}

        with pytest.raises(FileExistsError, match="--overwrite"):
            await run_evaluation(
                config_path=str(config_path),
                cases_path=str(cases_path),
                output_path=str(output_path),
            )
        assert len(received) == 1

        await run_evaluation(
            config_path=str(config_path),
            cases_path=str(cases_path),
            output_path=str(output_path),
            overwrite=True,
        )
        assert len(received) == 2
    finally:
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
