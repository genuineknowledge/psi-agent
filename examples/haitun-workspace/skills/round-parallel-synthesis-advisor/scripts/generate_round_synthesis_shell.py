from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STAGE08_DIRNAME = "08-round-parallel-synthesis-advisor"
INPUT_NAME = "SYNTHESIS_INPUT_SUMMARY.json"
INDEX_NAME = "ROUND_PARALLEL_SYNTHESIS_INDEX.json"
ROUTE_NAME = "ROUND_PARALLEL_SYNTHESIS_ROUTE.md"
CHEMSKILLS_EXECUTION_SPEC_NAME = "CHEMSKILLS_EXECUTION_SPEC.md"
SOURCE_LIQUID_PREPARATION_METHODS_NAME = "SOURCE_LIQUID_PREPARATION_METHODS.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create empty round-level Stage08 synthesis shell artifacts.")
    parser.add_argument("--input-json", type=Path, required=True)
    return parser.parse_args()


def load_payload(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    records = data.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"`records` must be a non-empty array: {path}")
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"`records[{index}]` must be an object: {path}")
    return data


def infer_round_id(input_json: Path, payload: dict[str, Any]) -> str:
    round_id = payload.get("round_id")
    if isinstance(round_id, str) and round_id.strip():
        return round_id.strip()
    parts = input_json.parts
    if len(parts) >= 2 and parts[-2]:
        return parts[-2]
    return ""


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def write_shell(input_json: Path, payload: dict[str, Any]) -> Path:
    output_dir = input_json.parent
    stage08_dir = output_dir.parent.parent
    route_path = output_dir / ROUTE_NAME
    index_path = output_dir / INDEX_NAME
    chemskills_execution_spec_path = stage08_dir / CHEMSKILLS_EXECUTION_SPEC_NAME
    source_liquid_preparation_methods_path = stage08_dir / SOURCE_LIQUID_PREPARATION_METHODS_NAME

    if not route_path.exists():
        route_path.write_text("", encoding="utf-8")
    if not chemskills_execution_spec_path.exists():
        chemskills_execution_spec_path.write_text("", encoding="utf-8")

    records = payload["records"]
    shell_records: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        shell_records.append(
            {
                "record_index": index,
                "record_id": str(record.get("record_id") or ""),
                "system": str(record.get("system") or ""),
                "catalyst_name": str(record.get("catalyst_name") or record.get("system_name") or ""),
            }
        )

    existing: dict[str, Any] = {}
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}

    index_payload = {
        "round_id": infer_round_id(input_json, payload),
        "input_json": repo_relative(input_json),
        "route_markdown": repo_relative(route_path),
        "chemskills_execution_spec": repo_relative(chemskills_execution_spec_path),
        "source_liquid_preparation_methods": repo_relative(source_liquid_preparation_methods_path),
        "source_liquid_bottle_preparation": existing.get("source_liquid_bottle_preparation"),
        "constraint_source_files": existing.get("constraint_source_files", []),
        "chemskills_source_files": existing.get("chemskills_source_files", []),
        "plate_count": 1,
        "plate_id": "p1",
        "source_liquid_limit": existing.get("source_liquid_limit"),
        "source_liquid_count": existing.get("source_liquid_count"),
        "input_record_count": len(records),
        "retained_record_count": len(existing.get("retained_records", []))
        if isinstance(existing.get("retained_records"), list)
        else 0,
        "blocked_record_count": len(existing.get("blocked_records", []))
        if isinstance(existing.get("blocked_records"), list)
        else 0,
        "input_records": shell_records,
        "retained_records": existing.get("retained_records", []),
        "blocked_records": existing.get("blocked_records", []),
        "source_liquids": existing.get("source_liquids", []),
        "well_map": existing.get("well_map", []),
        "parameter_csv_files": existing.get("parameter_csv_files", []),
        "review_status": existing.get("review_status", "needs_agent_completion"),
        "generated_by": "generate_round_synthesis_shell.py",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index_path


def main() -> None:
    args = parse_args()
    payload = load_payload(args.input_json)
    index_path = write_shell(args.input_json, payload)
    sys.stdout.write(f"{index_path}\n")


if __name__ == "__main__":
    main()
