from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "workflow"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from fusion_flow.token_usage import TokenCount, TokenUsageCollector, TokenUsageStore  # noqa: E402


def test_collector_merges_response_retries_and_preserves_unknown_usage() -> None:
    collector = TokenUsageCollector()
    collector.record(
        step_id="draft",
        executor_id="writer",
        executor_kind="Agent",
        attempt=1,
        iteration_index=None,
        usage=TokenCount(model_calls=1, input_tokens=100, output_tokens=10),
    )
    collector.record(
        step_id="draft",
        executor_id="writer",
        executor_kind="Agent",
        attempt=1,
        iteration_index=None,
        usage=TokenCount(model_calls=1, input_tokens=20, output_tokens=4),
    )
    collector.record(
        step_id="draft",
        executor_id="writer",
        executor_kind="Agent",
        attempt=2,
        iteration_index=None,
        usage=TokenCount(model_calls=1, input_tokens=None, output_tokens=None),
    )

    step = collector.snapshot()[0]
    assert step.attempts[0].usage == TokenCount(model_calls=2, input_tokens=120, output_tokens=14)
    assert step.usage.model_calls == 3
    assert step.usage.input_tokens is None
    assert step.usage.output_tokens is None
    assert not step.usage.complete
    assert not collector.totals.complete


def test_collector_keeps_foreach_iterations_separate() -> None:
    collector = TokenUsageCollector()
    for iteration_index, input_tokens in enumerate((30, 40)):
        collector.record(
            step_id="score_each",
            executor_id="scorer",
            executor_kind="Agent",
            attempt=1,
            iteration_index=iteration_index,
            usage=TokenCount(model_calls=1, input_tokens=input_tokens, output_tokens=5),
        )

    step = collector.snapshot()[0]
    assert [(item.iteration_index, item.attempt) for item in step.attempts] == [(0, 1), (1, 1)]
    assert step.usage == TokenCount(model_calls=2, input_tokens=70, output_tokens=10)


@pytest.mark.anyio
async def test_store_persists_resumes_and_finalizes(tmp_path: Path) -> None:
    run_dir = anyio.Path(tmp_path / "run-1")
    store = await TokenUsageStore.open(
        run_dir,
        run_id="run-1",
        workflow_id="wf-1",
        flow_path="flows/example.workflow",
    )
    store.collector.record(
        step_id="prepare",
        executor_id="preparer",
        executor_kind="Human",
        attempt=1,
        iteration_index=None,
        usage=TokenCount(model_calls=1, input_tokens=50, output_tokens=8),
    )
    await store.persist()

    running = json.loads(await (run_dir / "token-usage.json").read_text(encoding="utf-8"))
    assert running["status"] == "running"
    assert running["complete"] is True
    assert running["totals"] == {
        "complete": True,
        "input_tokens": 50,
        "model_calls": 1,
        "output_tokens": 8,
        "total_tokens": 58,
    }

    resumed = await TokenUsageStore.open(
        run_dir,
        run_id="run-1",
        workflow_id="wf-1",
        flow_path="flows/example.workflow",
    )
    resumed.collector.record(
        step_id="finish",
        executor_id="closer",
        executor_kind="Program",
        attempt=1,
        iteration_index=None,
        usage=TokenCount(model_calls=2, input_tokens=70, output_tokens=12),
    )
    await resumed.finalize(status="completed", error_type=None)

    completed = json.loads(await (run_dir / "token-usage.json").read_text(encoding="utf-8"))
    assert completed["status"] == "completed"
    assert completed["totals"]["model_calls"] == 3
    assert completed["totals"]["input_tokens"] == 120
    assert completed["totals"]["output_tokens"] == 20
    assert [step["step_id"] for step in completed["steps"]] == ["finish", "prepare"]
