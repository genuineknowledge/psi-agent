"""Run deterministic, serial Router evaluations against OpenAI SSE endpoints."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import aiohttp
import anyio
from rich.console import Console

from evals.router.metrics import grade_content


@dataclass(frozen=True)
class Condition:
    """One endpoint and its top-level request overrides."""

    name: str
    url: str
    request_overrides: dict[str, Any]


@dataclass(frozen=True)
class EvalCase:
    """One prompt and its grading metadata."""

    id: str
    scenario: str
    prompt: str
    grader: object
    expected_route: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True)
class EvalConfig:
    """Validated evaluator configuration."""

    conditions: tuple[Condition, ...]
    request: dict[str, Any]
    repetitions: int
    timeout_seconds: float
    seed: int


@dataclass(frozen=True)
class WorkItem:
    """One deterministic case/condition/trial combination."""

    condition: Condition
    case: EvalCase
    trial: int


@dataclass
class StreamState:
    """Mutable accumulation state for one SSE response."""

    started: float
    content_parts: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    finish_reason: str | None = None
    visible_usage: dict[str, Any] | None = None
    ttft_ms: float | None = None


class EvaluationProtocolError(ValueError):
    """An endpoint returned a response outside the single-choice SSE contract."""


def _require_exact_keys(*, value: dict[str, Any], required: set[str], label: str) -> None:
    missing = required - set(value)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{label} is missing required field(s): {names}")
    unexpected = set(value) - required
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ValueError(f"{label} contains unexpected field(s): {names}")


def _non_empty_string(*, value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _object(*, value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _validate_messages(*, value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(message, dict) for message in value):
        raise ValueError(f"{label} must be a list of objects")
    return cast(list[dict[str, Any]], value)


def _parse_config(raw: object) -> EvalConfig:
    config = _object(value=raw, label="config")
    _require_exact_keys(
        value=config,
        required={"conditions", "request", "repetitions", "timeout_seconds", "seed"},
        label="config",
    )

    raw_conditions = config["conditions"]
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise ValueError("config.conditions must be a non-empty list")
    conditions: list[Condition] = []
    for index, raw_condition in enumerate(raw_conditions, start=1):
        condition = _object(value=raw_condition, label=f"config.conditions[{index}]")
        _require_exact_keys(
            value=condition,
            required={"name", "url", "request_overrides"},
            label=f"config.conditions[{index}]",
        )
        name = _non_empty_string(value=condition["name"], label=f"config.conditions[{index}].name")
        url = _non_empty_string(value=condition["url"], label=f"config.conditions[{index}].url")
        parsed_url = urlsplit(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"config.conditions[{index}].url must be a complete HTTP(S) endpoint")
        overrides = _object(
            value=condition["request_overrides"],
            label=f"config.conditions[{index}].request_overrides",
        )
        if "messages" in overrides:
            _validate_messages(
                value=overrides["messages"],
                label=f"config.conditions[{index}].request_overrides.messages",
            )
        conditions.append(Condition(name=name, url=url, request_overrides=deepcopy(overrides)))
    names = [condition.name for condition in conditions]
    if len(names) != len(set(names)):
        raise ValueError("config.conditions names must be unique")

    request = _object(value=config["request"], label="config.request")
    if "messages" in request:
        _validate_messages(value=request["messages"], label="config.request.messages")
    repetitions = config["repetitions"]
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions <= 0:
        raise ValueError("config.repetitions must be a positive integer")
    timeout_seconds = config["timeout_seconds"]
    if (
        not isinstance(timeout_seconds, int | float)
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("config.timeout_seconds must be a finite positive number")
    seed = config["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("config.seed must be an integer")
    return EvalConfig(
        conditions=tuple(conditions),
        request=deepcopy(request),
        repetitions=repetitions,
        timeout_seconds=float(timeout_seconds),
        seed=seed,
    )


def _parse_case(*, raw: object, line_number: int) -> EvalCase:
    label = f"cases line {line_number}"
    case = _object(value=raw, label=label)
    _require_exact_keys(
        value=case,
        required={"id", "scenario", "prompt", "grader", "expected_route", "tags"},
        label=label,
    )
    case_id = _non_empty_string(value=case["id"], label=f"{label}.id")
    scenario = _non_empty_string(value=case["scenario"], label=f"{label}.scenario")
    prompt = _non_empty_string(value=case["prompt"], label=f"{label}.prompt")
    grader = _object(value=case["grader"], label=f"{label}.grader")
    # Validate the complete grader schema before any network request is made.
    grade_content(content="", grader=grader)
    expected_route = case["expected_route"]
    if expected_route is not None:
        expected_route = _non_empty_string(value=expected_route, label=f"{label}.expected_route")
    raw_tags = case["tags"]
    if not isinstance(raw_tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in raw_tags):
        raise ValueError(f"{label}.tags must be a list of non-empty strings")
    tags = tuple(tag.strip() for tag in raw_tags)
    if len(tags) != len(set(tags)):
        raise ValueError(f"{label}.tags must not contain duplicates")
    return EvalCase(
        id=case_id,
        scenario=scenario,
        prompt=prompt,
        grader=deepcopy(grader),
        expected_route=expected_route,
        tags=tags,
    )


async def _load_inputs(*, config_path: str, cases_path: str) -> tuple[EvalConfig, list[EvalCase]]:
    config_text = await anyio.Path(config_path).read_text(encoding="utf-8")
    try:
        raw_config = json.loads(config_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid config JSON: {error.msg} at line {error.lineno}") from error
    config = _parse_config(raw_config)

    cases_text = await anyio.Path(cases_path).read_text(encoding="utf-8")
    cases: list[EvalCase] = []
    for line_number, line in enumerate(cases_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw_case = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid cases JSON on line {line_number}: {error.msg}") from error
        cases.append(_parse_case(raw=raw_case, line_number=line_number))
    if not cases:
        raise ValueError("cases JSONL must contain at least one case")
    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case ids must be unique")
    return config, cases


def _build_request(*, config: EvalConfig, item: WorkItem) -> dict[str, Any]:
    body = deepcopy(config.request)
    body.update(deepcopy(item.condition.request_overrides))
    messages = _validate_messages(value=body.get("messages", []), label="effective request.messages")
    body["messages"] = [*messages, {"role": "user", "content": item.case.prompt}]
    body["stream"] = True
    return body


def _set_ttft(*, state: StreamState) -> None:
    if state.ttft_ms is None:
        state.ttft_ms = round((time.perf_counter() - state.started) * 1000, 3)


def _record_usage(*, state: StreamState, raw_usage: object) -> None:
    if raw_usage is None:
        return
    if not isinstance(raw_usage, dict):
        raise EvaluationProtocolError("SSE usage must be an object when present")
    state.visible_usage = deepcopy(cast(dict[str, Any], raw_usage))


def _accumulate_tool_calls(*, state: StreamState, raw_calls: object) -> None:
    if raw_calls is None:
        return
    if not isinstance(raw_calls, list):
        raise EvaluationProtocolError("SSE delta.tool_calls must be a list")
    if raw_calls:
        _set_ttft(state=state)
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise EvaluationProtocolError("SSE tool call must be an object")
        index = raw_call.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise EvaluationProtocolError("SSE tool call index must be a non-negative integer")
        call = state.tool_calls.setdefault(index, {"function": {"arguments": ""}})
        for key in ("id", "type"):
            value = raw_call.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise EvaluationProtocolError(f"SSE tool call {key} must be a string")
                call[key] = value
        function = raw_call.get("function")
        if function is None:
            continue
        if not isinstance(function, dict):
            raise EvaluationProtocolError("SSE tool call function must be an object")
        stored_function = call["function"]
        name = function.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise EvaluationProtocolError("SSE tool function name must be a string")
            stored_function["name"] = name
        arguments = function.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, str):
                raise EvaluationProtocolError("SSE tool function arguments must be a string")
            stored_function["arguments"] += arguments


def _consume_payload(*, payload: str, state: StreamState) -> None:
    try:
        raw_event = json.loads(payload)
    except json.JSONDecodeError as error:
        raise EvaluationProtocolError(f"malformed SSE JSON: {error.msg}") from error
    if not isinstance(raw_event, dict):
        raise EvaluationProtocolError("SSE payload must be an object")
    event = cast(dict[str, Any], raw_event)
    _record_usage(state=state, raw_usage=event.get("usage"))
    choices = event.get("choices")
    if not isinstance(choices, list):
        raise EvaluationProtocolError("SSE choices must be a list")
    if not choices:
        return
    if len(choices) != 1:
        raise EvaluationProtocolError(f"SSE event must contain at most one choice, got {len(choices)}")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise EvaluationProtocolError("SSE choice must be an object")
    delta = choice.get("delta")
    if delta is None:
        delta = {}
    if not isinstance(delta, dict):
        raise EvaluationProtocolError("SSE choice delta must be an object")
    content = delta.get("content")
    if content is not None:
        if not isinstance(content, str):
            raise EvaluationProtocolError("SSE delta.content must be a string or null")
        if content:
            _set_ttft(state=state)
            state.content_parts.append(content)
    _accumulate_tool_calls(state=state, raw_calls=delta.get("tool_calls"))
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise EvaluationProtocolError("SSE finish_reason must be a string or null")
    if finish_reason == "compaction_needed":
        return
    if isinstance(finish_reason, str):
        state.finish_reason = finish_reason
        if finish_reason == "error":
            state.errors.append("upstream returned finish_reason='error'")


async def _consume_sse(*, response: aiohttp.ClientResponse, state: StreamState) -> None:
    content_type = response.headers.get("Content-Type", "")
    if not content_type.lower().startswith("text/event-stream"):
        raise EvaluationProtocolError(f"HTTP 200 response is not text/event-stream: {content_type!r}")
    data_lines: list[str] = []
    while True:
        raw_line = await response.content.readline()
        if not raw_line:
            if data_lines:
                payload = "\n".join(data_lines)
                if payload != "[DONE]":
                    _consume_payload(payload=payload, state=state)
            return
        try:
            line = raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as error:
            raise EvaluationProtocolError("SSE stream is not valid UTF-8") from error
        if line:
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            continue
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        data_lines.clear()
        if payload == "[DONE]":
            return
        _consume_payload(payload=payload, state=state)


def _tool_calls_are_complete(tool_calls: dict[int, dict[str, Any]]) -> bool:
    if not tool_calls:
        return False
    for call in tool_calls.values():
        function = call.get("function")
        if (
            not isinstance(call.get("id"), str)
            or call.get("type") != "function"
            or not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not isinstance(function.get("arguments"), str)
        ):
            return False
    return True


def _visible_token(*, usage: dict[str, Any] | None, keys: tuple[str, ...], state: StreamState) -> int | None:
    if usage is None:
        return None
    for key in keys:
        if key not in usage:
            continue
        value = usage[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            state.errors.append(f"visible usage {key} must be a non-negative integer")
            return None
        return value
    return None


async def _run_trial(
    *,
    session: aiohttp.ClientSession,
    config: EvalConfig,
    item: WorkItem,
) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    state = StreamState(started=started)
    http_status: int | None = None
    cancelled_error = anyio.get_cancelled_exc_class()
    try:
        async with session.post(item.condition.url, json=_build_request(config=config, item=item)) as response:
            http_status = response.status
            if response.status != 200:
                detail = (await response.text())[:1000]
                state.errors.append(f"HTTP {response.status}: {detail}")
            else:
                await _consume_sse(response=response, state=state)
    except cancelled_error:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        state.errors.append(f"{type(error).__name__}: {error}")
    except EvaluationProtocolError as error:
        state.errors.append(str(error))
    except Exception as error:
        state.errors.append(f"{type(error).__name__}: {error}")

    content = "".join(state.content_parts)
    complete_tool_calls = _tool_calls_are_complete(state.tool_calls)
    if state.tool_calls and not complete_tool_calls:
        state.errors.append("response contained an incomplete tool call")
    if state.finish_reason == "tool_calls" and not complete_tool_calls:
        state.errors.append("finish_reason='tool_calls' without a complete tool call")
    if state.finish_reason is None:
        state.errors.append("response ended without a completion finish reason")
    usable = bool(content.strip() or complete_tool_calls)
    if not usable:
        state.errors.append("response contained no usable content or tool calls")
    visible_input_tokens = _visible_token(
        usage=state.visible_usage,
        keys=("prompt_tokens", "input_tokens"),
        state=state,
    )
    visible_output_tokens = _visible_token(
        usage=state.visible_usage,
        keys=("completion_tokens", "output_tokens"),
        state=state,
    )
    protocol_success = bool(
        http_status == 200
        and not state.errors
        and state.finish_reason not in {None, "error", "compaction_needed"}
        and usable
    )
    grade = grade_content(content=content, grader=item.case.grader)
    clean_success = None if grade.score is None else protocol_success and grade.score == 1.0 and not grade.contaminated
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "condition": item.condition.name,
        "case": item.case.id,
        "scenario": item.case.scenario,
        "trial": item.trial,
        "started_at": started_at,
        "http_status": http_status,
        "content": content,
        "finish_reason": state.finish_reason,
        "errors": state.errors,
        "ttft_ms": state.ttft_ms,
        "latency_ms": latency_ms,
        "visible_usage": state.visible_usage,
        "visible_input_tokens": visible_input_tokens,
        "visible_output_tokens": visible_output_tokens,
        "protocol_success": protocol_success,
        "score": grade.score,
        "contaminated": grade.contaminated,
        "clean_success": clean_success,
    }


async def run_evaluation(
    *,
    config_path: str,
    cases_path: str,
    output_path: str,
    overwrite: bool = False,
) -> None:
    """Load inputs, execute every trial serially, and write JSONL records."""

    config, cases = await _load_inputs(config_path=config_path, cases_path=cases_path)
    work = [
        WorkItem(condition=condition, case=case, trial=trial)
        for case in cases
        for condition in config.conditions
        for trial in range(1, config.repetitions + 1)
    ]
    random.Random(config.seed).shuffle(work)
    output = anyio.Path(output_path)
    if await output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}; pass --overwrite to replace it")
    await output.parent.mkdir(parents=True, exist_ok=True)
    console = Console(highlight=False)
    console.print(
        f"Running {len(work)} serial Router evaluation trial(s) with seed {config.seed}",
        markup=False,
    )
    timeout = aiohttp.ClientTimeout(total=config.timeout_seconds)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        await anyio.open_file(output, "w", encoding="utf-8") as destination,
    ):
        for index, item in enumerate(work, start=1):
            record = await _run_trial(session=session, config=config, item=item)
            await destination.write(json.dumps(record, ensure_ascii=False) + "\n")
            await destination.flush()
            status = "ok" if record["protocol_success"] else "failed"
            console.print(
                f"[{index}/{len(work)}] {item.condition.name} / {item.case.id} / trial {item.trial}: {status}",
                markup=False,
            )
    console.print(f"Wrote {len(work)} record(s) to {output}", markup=False)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Evaluator config JSON path")
    parser.add_argument("--cases", required=True, help="Evaluation cases JSONL path")
    parser.add_argument("--output", required=True, help="Output records JSONL path")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    return parser.parse_args()


async def _run_from_arguments(args: argparse.Namespace) -> None:
    await run_evaluation(
        config_path=args.config,
        cases_path=args.cases,
        output_path=args.output,
        overwrite=args.overwrite,
    )


def main() -> None:
    """CLI entry point for ``python -m evals.router.run``."""

    args = _arguments()
    anyio.run(_run_from_arguments, args)


if __name__ == "__main__":
    main()
