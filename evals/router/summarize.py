"""Render a Rich summary table for Router evaluation JSONL records."""

from __future__ import annotations

import argparse
import json
import math
from typing import Any, cast

import anyio
from rich.console import Console
from rich.table import Table

from evals.router.metrics import summarize_records

_REQUIRED_FIELDS = {
    "condition",
    "case",
    "scenario",
    "trial",
    "started_at",
    "http_status",
    "content",
    "finish_reason",
    "errors",
    "ttft_ms",
    "latency_ms",
    "visible_usage",
    "protocol_success",
    "score",
    "contaminated",
    "clean_success",
}


def _optional_number(*, value: object, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number or null")
    return float(value)


def _optional_token(*, value: object, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer or null")
    return value


def _usage_token(*, usage: dict[str, Any] | None, keys: tuple[str, ...]) -> int | None:
    if usage is None:
        return None
    for key in keys:
        if key in usage:
            return _optional_token(value=usage[key], label=f"visible_usage.{key}")
    return None


def _validate_record(*, raw: object, line_number: int) -> dict[str, Any]:
    label = f"record line {line_number}"
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    record = cast(dict[str, Any], raw)
    missing = _REQUIRED_FIELDS - set(record)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{label} is missing required field(s): {names}")
    for key in ("condition", "case", "scenario", "started_at", "content"):
        if not isinstance(record[key], str):
            raise ValueError(f"{label}.{key} must be a string")
    trial = record["trial"]
    if not isinstance(trial, int) or isinstance(trial, bool) or trial <= 0:
        raise ValueError(f"{label}.trial must be a positive integer")
    http_status = record["http_status"]
    if http_status is not None and (
        not isinstance(http_status, int) or isinstance(http_status, bool) or not 100 <= http_status <= 599
    ):
        raise ValueError(f"{label}.http_status must be an HTTP status integer or null")
    finish_reason = record["finish_reason"]
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ValueError(f"{label}.finish_reason must be a string or null")
    errors = record["errors"]
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError(f"{label}.errors must be a list of strings")
    _optional_number(value=record["ttft_ms"], label=f"{label}.ttft_ms")
    _optional_number(value=record["latency_ms"], label=f"{label}.latency_ms")
    usage = record["visible_usage"]
    if usage is not None and not isinstance(usage, dict):
        raise ValueError(f"{label}.visible_usage must be an object or null")
    typed_usage = cast(dict[str, Any] | None, usage)
    for key in ("protocol_success", "contaminated"):
        if not isinstance(record[key], bool):
            raise ValueError(f"{label}.{key} must be a boolean")
    _optional_number(value=record["score"], label=f"{label}.score")
    clean_success = record["clean_success"]
    if clean_success is not None and not isinstance(clean_success, bool):
        raise ValueError(f"{label}.clean_success must be a boolean or null")

    normalized = dict(record)
    input_value = normalized.get("visible_input_tokens")
    output_value = normalized.get("visible_output_tokens")
    if "visible_input_tokens" in normalized:
        _optional_token(value=input_value, label=f"{label}.visible_input_tokens")
    else:
        input_value = _usage_token(usage=typed_usage, keys=("prompt_tokens", "input_tokens"))
        normalized["visible_input_tokens"] = input_value
    if "visible_output_tokens" in normalized:
        _optional_token(value=output_value, label=f"{label}.visible_output_tokens")
    else:
        output_value = _usage_token(usage=typed_usage, keys=("completion_tokens", "output_tokens"))
        normalized["visible_output_tokens"] = output_value
    return normalized


async def load_records(path: str) -> list[dict[str, Any]]:
    """Load and validate JSONL records using asynchronous file I/O."""

    text = await anyio.Path(path).read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON on record line {line_number}: {error.msg}") from error
        records.append(_validate_record(raw=raw, line_number=line_number))
    if not records:
        raise ValueError("records JSONL must contain at least one record")
    return records


def _format_value(*, key: str, value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if key.endswith("_rate") or key == "mean_score":
            return f"{value:.3f}"
        return f"{value:.1f}"
    return str(value)


def render_summary(*, summaries: list[dict[str, Any]], console: Console) -> None:
    """Render condition summaries without assuming undocumented extra columns."""

    if not summaries:
        raise ValueError("summary must contain at least one condition")
    preferred = [
        "condition",
        "n",
        "protocol_success_rate",
        "mean_score",
        "clean_success_rate",
        "contamination_rate",
        "ttft_ms_p50",
        "ttft_ms_p95",
        "latency_ms_p50",
        "latency_ms_p95",
        "visible_input_tokens",
        "visible_output_tokens",
    ]
    all_keys = {key for summary in summaries for key in summary}
    columns = [key for key in preferred if key in all_keys]
    columns.extend(sorted(all_keys - set(columns)))
    table = Table(title="Router evaluation summary", show_lines=False)
    for column in columns:
        justify = "left" if column == "condition" else "right"
        table.add_column(column, justify=justify, no_wrap=True)
    for summary in summaries:
        table.add_row(*[_format_value(key=column, value=summary.get(column)) for column in columns])
    console.print(table)


async def summarize_file(*, input_path: str) -> None:
    """Load one result file and print its per-condition summary."""

    records = await load_records(input_path)
    summaries = summarize_records(records)
    render_summary(summaries=summaries, console=Console(highlight=False))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Evaluation records JSONL path")
    return parser.parse_args()


async def _summarize_from_arguments(args: argparse.Namespace) -> None:
    await summarize_file(input_path=args.input)


def main() -> None:
    """CLI entry point for ``python -m evals.router.summarize``."""

    args = _arguments()
    anyio.run(_summarize_from_arguments, args)


if __name__ == "__main__":
    main()
