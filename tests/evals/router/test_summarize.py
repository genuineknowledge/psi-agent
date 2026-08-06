"""Router evaluator result loading and rendering contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from rich.console import Console

from evals.router.metrics import summarize_records
from evals.router.summarize import load_records, render_summary


def _record(**changes: object) -> dict[str, Any]:
    record: dict[str, Any] = {
        "condition": "fallback",
        "case": "case-1",
        "scenario": "fallback",
        "trial": 1,
        "started_at": "2026-08-06T00:00:00+00:00",
        "http_status": 200,
        "content": "answer",
        "finish_reason": "stop",
        "errors": [],
        "ttft_ms": 10,
        "latency_ms": 20,
        "visible_usage": {"prompt_tokens": 7, "completion_tokens": 2},
        "protocol_success": True,
        "score": 1.0,
        "contaminated": False,
        "clean_success": True,
    }
    record.update(changes)
    return record


@pytest.mark.anyio
async def test_load_records_infers_visible_tokens_and_summary_renders(tmp_path: Path) -> None:
    path = anyio.Path(str(tmp_path)) / "results.jsonl"
    await path.write_text(json.dumps(_record()) + "\n", encoding="utf-8")

    records = await load_records(str(path))
    summaries = summarize_records(records)
    console = Console(record=True, width=200)
    render_summary(summaries=summaries, console=console)

    assert records[0]["visible_input_tokens"] == 7
    assert records[0]["visible_output_tokens"] == 2
    rendered = console.export_text()
    assert "fallback" in rendered
    assert "protocol_success_rate" in rendered


@pytest.mark.anyio
async def test_load_records_rejects_empty_input(tmp_path: Path) -> None:
    path = anyio.Path(str(tmp_path)) / "empty.jsonl"
    await path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="at least one record"):
        await load_records(str(path))
