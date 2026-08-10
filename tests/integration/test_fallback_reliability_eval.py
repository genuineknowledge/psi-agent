"""Real HTTP/SSE integration test for baseline versus Fallback."""

from __future__ import annotations

import json
import socket
from collections.abc import Sequence
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.router.client import RouterHttpClient
from psi_agent.router.fallback import FallbackConfig, FallbackStrategy
from psi_agent.router.models import RouterTarget
from psi_agent.router.server import create_router_app
from tests.evals.fallback_reliability.analyze import AnalysisConfig, analyze_records
from tests.evals.fallback_reliability.fault_proxy import (
    SEMANTIC_WRONG_ANSWER,
    FaultPlan,
    FaultProxyGroup,
    OperationalFault,
    ProxyDefinition,
    RecordingConfig,
    TrialRecorder,
    planned_fault_for_identity,
)
from tests.evals.router.run import Condition, EvalCase, EvalConfig, run_evaluation_config


def _bound_tcp_url() -> tuple[socket.socket, str]:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    _, port = server_socket.getsockname()
    return server_socket, f"http://127.0.0.1:{port}"


async def _start_app(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    await web.SockSite(runner, server_socket).start()
    return runner, f"http://127.0.0.1:{server_socket.getsockname()[1]}"


def _ai_app(*, model: str, answer: str) -> web.Application:
    async def handler(request: web.Request) -> web.StreamResponse:
        await request.json()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        events = [
            {
                "id": model,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": "stop"}],
            },
            {
                "id": f"{model}-usage",
                "model": model,
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        ]
        for event in events:
            await response.write(f"data: {json.dumps(event)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    return app


def _default_cases() -> tuple[EvalCase, ...]:
    return (
        EvalCase(
            id="answer",
            scenario="fallback-reliability",
            prompt="Return only 42.",
            grader={"type": "exact", "answer": "42"},
            expected_route=None,
            tags=("automatic",),
        ),
    )


async def _run_comparison(
    *,
    tmp_path: Any,
    fault_plan: FaultPlan,
    cases: Sequence[EvalCase] | None = None,
) -> tuple[list[dict[str, Any]], RecordingConfig]:
    selected_cases = list(cases) if cases is not None else list(_default_cases())
    runners: list[web.AppRunner] = []
    try:
        primary_runner, primary_url = await _start_app(_ai_app(model="primary-model", answer="42"))
        runners.append(primary_runner)
        backup_runner, backup_url = await _start_app(_ai_app(model="backup-model", answer="42"))
        runners.append(backup_runner)
        primary_proxy_socket, primary_proxy_url = _bound_tcp_url()
        backup_proxy_socket, backup_proxy_url = _bound_tcp_url()
        recording = RecordingConfig(
            baseline_clean_condition="primary-only-clean",
            fallback_clean_condition="fallback-clean",
            baseline_faulted_condition="primary-only-faulted",
            fallback_faulted_condition="fallback-faulted",
            fault_plan=fault_plan,
            proxies=(
                ProxyDefinition(
                    name="primary",
                    listen=primary_proxy_url,
                    upstream=primary_url,
                    role="primary",
                    candidate_id="candidate-1",
                    model="primary-model",
                    input_per_million=None,
                    cached_input_per_million=None,
                    output_per_million=None,
                ),
                ProxyDefinition(
                    name="backup",
                    listen=backup_proxy_url,
                    upstream=backup_url,
                    role="backup",
                    candidate_id="candidate-2",
                    model="backup-model",
                    input_per_million=None,
                    cached_input_per_million=None,
                    output_per_million=None,
                ),
            ),
        )
        recorder = TrialRecorder(
            config=recording,
            experiment_sha256="a" * 64,
            cases_sha256="b" * 64,
        )

        async with FaultProxyGroup(
            config=recording,
            recorder=recorder,
            listen_sockets={"primary": primary_proxy_socket, "backup": backup_proxy_socket},
        ):
            baseline = FallbackStrategy(
                config=FallbackConfig(
                    session_socket="baseline.sock",
                    targets=[RouterTarget("candidate-1", primary_proxy_url, "primary", timeout=0.2)],
                ),
                client=RouterHttpClient(),
            )
            fallback = FallbackStrategy(
                config=FallbackConfig(
                    session_socket="fallback.sock",
                    targets=[
                        RouterTarget("candidate-1", primary_proxy_url, "primary", timeout=0.2),
                        RouterTarget("candidate-2", backup_proxy_url, "backup", timeout=1.0),
                    ],
                ),
                client=RouterHttpClient(),
            )
            baseline_runner, baseline_url = await _start_app(create_router_app(strategy=baseline))
            runners.append(baseline_runner)
            fallback_runner, fallback_url = await _start_app(create_router_app(strategy=fallback))
            runners.append(fallback_runner)
            config = EvalConfig(
                conditions=tuple(
                    Condition(name=name, url=f"{url}/chat/completions", request_overrides={})
                    for name, url in (
                        ("primary-only-clean", baseline_url),
                        ("fallback-clean", fallback_url),
                        ("primary-only-faulted", baseline_url),
                        ("fallback-faulted", fallback_url),
                    )
                ),
                request={"messages": [], "temperature": 0, "max_tokens": 32, "stream": True},
                repetitions=1,
                timeout_seconds=5,
                seed=20260808,
                registered_case_ids=tuple(case.id for case in selected_cases),
            )
            await run_evaluation_config(
                config=config,
                cases=selected_cases,
                output_path=str(tmp_path / "results.jsonl"),
                observer=recorder,
            )
    finally:
        cleanup_errors: list[BaseException] = []
        with anyio.CancelScope(shield=True):
            for runner in reversed(runners):
                try:
                    await runner.cleanup()
                except BaseException as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            raise BaseExceptionGroup("one or more test servers failed to close", cleanup_errors)

    result_text = await anyio.Path(str(tmp_path / "results.jsonl")).read_text(encoding="utf-8")
    records = [json.loads(line) for line in result_text.splitlines()]
    assert all(record["experiment_sha256"] == "a" * 64 for record in records)
    assert all(record["cases_sha256"] == "b" * 64 for record in records)
    return records, recording


_FAULT_TELEMETRY: tuple[
    tuple[OperationalFault, tuple[int | None, str | None, bool, bool]],
    ...,
] = (
    ("http_503", (503, None, True, False)),
    ("timeout", (None, None, False, False)),
    ("truncated_sse", (200, None, False, False)),
    ("malformed_sse", (200, None, False, False)),
    ("error_finish", (200, "error", True, False)),
    ("empty_completion", (200, "stop", True, False)),
)


def _case_id_for_fault(*, plan: FaultPlan, expected_fault: str) -> str:
    for index in range(10_000):
        case_id = f"{expected_fault}-{index}"
        if (
            planned_fault_for_identity(
                condition_name="fallback-faulted",
                case_id=case_id,
                trial=1,
                plan=plan,
                faulted_conditions=frozenset({"primary-only-faulted", "fallback-faulted"}),
            )
            == expected_fault
        ):
            return case_id
    raise AssertionError(f"failed to find a deterministic case identity for {expected_fault}")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operational_fault", "expected_primary_telemetry"),
    _FAULT_TELEMETRY,
)
async def test_real_fallback_recovers_every_supported_primary_failure_but_single_model_does_not(
    tmp_path: Any,
    operational_fault: OperationalFault,
    expected_primary_telemetry: tuple[int | None, str | None, bool, bool],
) -> None:
    records, _ = await _run_comparison(
        tmp_path=tmp_path,
        fault_plan=FaultPlan(
            seed=20260808,
            operational_failure_rate=1.0,
            semantic_failure_rate=0.0,
            operational_faults=(operational_fault,),
            timeout_delay_seconds=0.3,
        ),
    )
    by_condition = {record["condition"]: record for record in records}

    assert by_condition["primary-only-clean"]["complete_clean_success"] is True
    assert by_condition["fallback-clean"]["complete_clean_success"] is True
    assert by_condition["fallback-clean"]["attempt_count"] == 1
    assert by_condition["primary-only-faulted"]["complete_clean_success"] is False
    assert by_condition["fallback-faulted"]["complete_clean_success"] is True
    assert by_condition["fallback-faulted"]["fallback_activated"] is True
    assert by_condition["fallback-faulted"]["recovered"] is True
    assert by_condition["fallback-faulted"]["attempt_count"] == 2
    assert by_condition["fallback-faulted"]["content"] == "42"
    assert by_condition["fallback-faulted"]["failure_marker_leaked"] is False
    for condition in ("primary-only-faulted", "fallback-faulted"):
        primary_attempt = by_condition[condition]["attempts"][0]
        assert (
            primary_attempt["http_status"],
            primary_attempt["finish_reason"],
            primary_attempt["stream_complete"],
            primary_attempt["forwarded_to_model"],
        ) == expected_primary_telemetry
        assert len(primary_attempt["request_sha256"]) == 64
    assert (
        by_condition["primary-only-faulted"]["attempts"][0]["request_sha256"]
        == by_condition["fallback-faulted"]["attempts"][0]["request_sha256"]
    )
    backup_attempt = by_condition["fallback-faulted"]["attempts"][1]
    assert (
        backup_attempt["http_status"],
        backup_attempt["finish_reason"],
        backup_attempt["stream_complete"],
        backup_attempt["forwarded_to_model"],
    ) == (200, "stop", True, True)


@pytest.mark.anyio
async def test_protocol_complete_wrong_answer_is_not_misrepresented_as_fallback_recovery(tmp_path: Any) -> None:
    records, _ = await _run_comparison(
        tmp_path=tmp_path,
        fault_plan=FaultPlan(
            seed=20260808,
            operational_failure_rate=0.0,
            semantic_failure_rate=1.0,
            operational_faults=("http_503",),
            timeout_delay_seconds=0.3,
        ),
    )
    by_condition = {record["condition"]: record for record in records}

    baseline = by_condition["primary-only-faulted"]
    fallback = by_condition["fallback-faulted"]
    assert baseline["protocol_success"] is True
    assert fallback["protocol_success"] is True
    assert baseline["complete_clean_success"] is False
    assert fallback["complete_clean_success"] is False
    assert fallback["fallback_activated"] is False
    assert fallback["recovered"] is False
    assert fallback["attempt_count"] == 1
    assert fallback["content"] == SEMANTIC_WRONG_ANSWER


@pytest.mark.anyio
async def test_real_evaluation_matrix_supports_fallback_reliability_hypothesis(tmp_path: Any) -> None:
    fault_plan = FaultPlan(
        seed=20260808,
        operational_failure_rate=0.5,
        semantic_failure_rate=0.5,
        operational_faults=("error_finish",),
        timeout_delay_seconds=0.3,
    )
    operational_case_id = _case_id_for_fault(plan=fault_plan, expected_fault="error_finish")
    semantic_case_id = _case_id_for_fault(
        plan=fault_plan,
        expected_fault="semantic_wrong_answer",
    )
    cases = tuple(
        EvalCase(
            id=case_id,
            scenario="fallback-reliability",
            prompt="Return only 42.",
            grader={"type": "exact", "answer": "42"},
            expected_route=None,
            tags=("automatic",),
        )
        for case_id in (operational_case_id, semantic_case_id)
    )

    records, recording = await _run_comparison(
        tmp_path=tmp_path,
        fault_plan=fault_plan,
        cases=cases,
    )
    report = analyze_records(
        records=records,
        registered_case_ids=tuple(case.id for case in cases),
        repetitions=1,
        recording=recording,
        analysis=AnalysisConfig(
            clean_quality_noninferiority_margin=0.0,
            minimum_faulted_success_delta=0.5,
            minimum_faulted_delta_ci_lower=0.0,
            minimum_recovery_rate=1.0,
            minimum_trials_per_operational_fault=1,
            minimum_per_fault_recovery_rate=1.0,
            minimum_operational_activation_rate=1.0,
            maximum_healthy_backup_activation_rate=0.0,
            minimum_semantic_guard_rate=1.0,
            maximum_failure_marker_leaks=0,
            minimum_internal_usage_coverage=1.0,
            bootstrap_samples=1_000,
            bootstrap_seed=20260808,
        ),
        case_graders={case.id: case.grader for case in cases},
        expected_experiment_sha256="a" * 64,
        expected_cases_sha256="b" * 64,
    )

    comparison = report["comparison"]
    assert len(records) == 8
    assert comparison["clean_quality_delta_ci_95_lower"] == 0.0
    assert comparison["faulted_quality_delta"] == 0.5
    assert comparison["recovery_rate"] == 1.0
    assert comparison["semantic_guard_rate"] == 1.0
    assert comparison["internal_usage_coverage"] == 1.0
    assert all(comparison["checks"].values())
    assert report["verdict"] == "supported"
