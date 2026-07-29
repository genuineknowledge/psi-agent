from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_RECOMMENDER_PARALLELISM = 4
DEFAULT_MATTERSIM_BATCH_SIZE = 8
DEFAULT_STATE_FILENAME = "STREAMING_SCHEDULER_STATE.json"
DEFAULT_PARAMETERS_FILENAME = "PARAMETERS.json"
DEFAULT_REGISTRY_FILENAME = "STREAMING_CANDIDATE_REGISTRY.sqlite3"
DEFAULT_STATUS_FILENAME = "STREAMING_PIPELINE_STATUS.md"
DEFAULT_OUTPUT_ROOT = Path("ows")
DEFAULT_HISTORY_DIR = Path("data/history")
VALID_BRANCHES = {"single-photocatalyst", "zscheme"}
GPU_ID_PATTERN = re.compile(r"^[0-9]+(?:(?:\s*,\s*|\s+)[0-9]+)*$")
GPU_ID_TOKEN_PATTERN = re.compile(r"[0-9]+")
FORMULA_TOKEN_PATTERN = re.compile(r"([A-Z][a-z]?)([0-9]*\.?[0-9]*)")
STAGE02_DIRNAME = "02-ows-catalyst-recommender"
SINGLE_STAGE04_DIRNAME = "04-mattergen-structure-sampler"
ZSCHEME_STAGE04_DIRNAME = "04-zscheme-mattergen-structure-sampler"
SINGLE_STAGE05_DIRNAME = "05-mattersim-structure-evaluator"
ZSCHEME_STAGE05_DIRNAME = "05-zscheme-mattersim-structure-evaluator"
ENTRY_DIRNAME = "00-coscientist-ows-entry"
READY_TO_SAMPLE = "ready_to_sample"
PROCEED_TO_EXPERIMENTAL_VALIDATION = "proceed_to_experimental_validation"

EVALUATION_SUMMARY_COLUMNS = [
    "candidate_id",
    "structure_id",
    "metrics_path",
    "detailed_metrics_path",
    "relaxed_structures_path",
    "relaxed_space_group_symbol",
    "relaxed_space_group_number",
    "relaxed_crystal_system",
    "space_group_analysis_note",
    "energy_above_hull",
    "stability",
    "novelty",
    "uniqueness",
    "evaluation_verdict",
    "recommended_return_step",
    "routing_reason",
]

SINGLE_CANDIDATE_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "main_photocatalyst",
    "main_photocatalyst_formula_note",
    "preliminary_synthesis_route",
    "laboratory_feasibility_decision",
    "violated_laboratory_limitation_ids",
    "laboratory_feasibility_reason",
    "difference_from_prior_recommendations",
    "reference_knowledge_ids",
    "supporting_knowledge",
]

ZSCHEME_SYSTEM_COLUMNS = [
    "zscheme_id",
    "zscheme_name",
    "system_type",
    "her_component_id",
    "oer_component_id",
    "solid_electron_mediator",
    "mechanism_gate_status",
    "mechanism_gate_reason",
    "laboratory_feasibility_decision",
    "violated_laboratory_limitation_ids",
    "laboratory_feasibility_reason",
    "difference_from_prior_recommendations",
    "reference_knowledge_ids",
    "supporting_knowledge",
]

ZSCHEME_COMPONENT_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "parent_zscheme_id",
    "component_role",
    "main_photocatalyst",
    "main_photocatalyst_formula_note",
    "mechanism_role",
    "preliminary_synthesis_route",
    "laboratory_feasibility_decision",
    "violated_laboratory_limitation_ids",
    "laboratory_feasibility_reason",
    "difference_from_prior_recommendations",
    "reference_knowledge_ids",
    "supporting_knowledge",
]

SAMPLING_PLAN_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "main_photocatalyst",
    "main_photocatalyst_formula_note",
    "target_composition_json",
    "sampling_output_dir",
    "structures_path",
    "sampling_command",
    "sampling_status",
    "blocked_reason",
    "recommended_return_step",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def entry_dir(output_root: Path) -> Path:
    return output_root / ENTRY_DIRNAME


def parameters_path(output_root: Path) -> Path:
    return entry_dir(output_root) / DEFAULT_PARAMETERS_FILENAME


def state_path(output_root: Path) -> Path:
    return entry_dir(output_root) / DEFAULT_STATE_FILENAME


def registry_path(output_root: Path) -> Path:
    return entry_dir(output_root) / DEFAULT_REGISTRY_FILENAME


def status_path(output_root: Path) -> Path:
    return entry_dir(output_root) / DEFAULT_STATUS_FILENAME


def streaming_stage02_dir(output_root: Path) -> Path:
    return output_root / STAGE02_DIRNAME / "streaming"


def streaming_stage04_dir(output_root: Path, branch: str) -> Path:
    dirname = ZSCHEME_STAGE04_DIRNAME if branch == "zscheme" else SINGLE_STAGE04_DIRNAME
    return output_root / dirname / "streaming"


def streaming_stage05_dir(output_root: Path, branch: str) -> Path:
    dirname = ZSCHEME_STAGE05_DIRNAME if branch == "zscheme" else SINGLE_STAGE05_DIRNAME
    return output_root / dirname / "streaming"


def display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_repo_path(repo_root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def write_csv_atomic(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    os.replace(tmp_path, path)


def normalize_gpu_ids(gpu_id: str | None) -> str:
    candidate = str(gpu_id or "").strip()
    if not candidate:
        return ""
    if not GPU_ID_PATTERN.fullmatch(candidate):
        return ""
    return ",".join(GPU_ID_TOKEN_PATTERN.findall(candidate))


def normalize_formula(value: Any) -> str:
    raw = "".join(str(value or "").strip().split())
    if not raw:
        return ""
    tokens = FORMULA_TOKEN_PATTERN.findall(raw)
    joined = "".join(element + amount for element, amount in tokens)
    return joined or raw


def reduced_formula_key(value: Any) -> str:
    normalized = normalize_formula(value)
    if not normalized or "+" in normalized:
        return normalized
    try:
        composition_type = __import__(
            "pymatgen.core",
            fromlist=["Composition"],
        ).Composition
        return str(composition_type(normalized).reduced_formula)
    except Exception:
        return normalized


def looks_like_material_formula(value: str) -> bool:
    token = value.strip()
    if not token or len(token) > 80:
        return False
    if token.startswith(("SL", "OWS", "ows", "mp-", "src_")) or "::" in token or "/" in token:
        return False
    if "_" in token or "-" in token:
        return False
    if not re.search(r"[A-Z][a-z]?", token) or not re.search(r"\d", token):
        return False
    try:
        composition_type = __import__(
            "pymatgen.core",
            fromlist=["Composition"],
        ).Composition
        return len(composition_type(token).elements) > 1
    except Exception:
        return bool(FORMULA_TOKEN_PATTERN.findall(token))


def history_line_formula_tokens(line: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"`([^`]+)`", line):
        cleaned = token.strip()
        if cleaned and cleaned not in seen:
            tokens.append(cleaned)
            seen.add(cleaned)

    bare_line = re.sub(r"`[^`]+`", " ", line)
    for part in re.split(r"[|,;>\s]+", bare_line):
        cleaned = part.strip().strip("`[](){}\uff0c\u3002\uff1b\uff1a:").strip()
        if cleaned and cleaned not in seen:
            tokens.append(cleaned)
            seen.add(cleaned)
    return tokens


def candidate_formula_values(branch: str, payload: dict[str, Any], formula_key: str) -> list[str]:
    if branch == "zscheme":
        formulas = zscheme_component_formulas(payload)
        if formulas:
            return formulas
        return [formula_key]
    return [str(payload.get("main_photocatalyst") or payload.get("formula_key") or formula_key)]


def read_history_success_formula_index(repo_root: Path, history_dir: Path) -> dict[str, list[dict[str, Any]]]:
    resolved_history_dir = resolve_repo_path(repo_root, history_dir)
    index: dict[str, list[dict[str, Any]]] = {}
    if not resolved_history_dir.exists():
        return index
    for route_file in sorted(resolved_history_dir.glob("*_CUMULATIVE_SYNTHESIS_ROUTE.md")):
        text = route_file.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "|" not in line and "->" not in line:
                continue
            for token in history_line_formula_tokens(line):
                if not looks_like_material_formula(token):
                    continue
                reduced = reduced_formula_key(token)
                if not reduced:
                    continue
                index.setdefault(reduced, []).append(
                    {
                        "formula": normalize_formula(token),
                        "reduced_formula": reduced,
                        "history_file": display_path(repo_root, route_file),
                        "line_number": line_number,
                        "line_excerpt": line.strip()[:500],
                    }
                )
    return index


def history_duplicate_matches(
    repo_root: Path,
    history_dir: Path,
    branch: str,
    payload: dict[str, Any],
    formula_key: str,
    history_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    if history_index is None:
        history_index = read_history_success_formula_index(repo_root, history_dir)
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for formula in candidate_formula_values(branch, payload, formula_key):
        reduced = reduced_formula_key(formula)
        for match in history_index.get(reduced, []):
            dedupe_key = (match["history_file"], str(match["line_number"]), match["formula"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            item = dict(match)
            item["candidate_formula"] = normalize_formula(formula)
            item["candidate_reduced_formula"] = reduced
            matches.append(item)
    return matches


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def connect_registry(output_root: Path) -> sqlite3.Connection:
    path = registry_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    initialize_schema(connection)
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            branch TEXT NOT NULL,
            top_level_id TEXT NOT NULL,
            candidate_name TEXT NOT NULL DEFAULT '',
            main_photocatalyst TEXT NOT NULL DEFAULT '',
            formula_key TEXT NOT NULL,
            reasoning_family_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            reasoning_path TEXT NOT NULL DEFAULT '',
            recommendation_agent TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sampling_gpu_id TEXT NOT NULL DEFAULT '',
            sampling_started_at TEXT NOT NULL DEFAULT '',
            sampling_completed_at TEXT NOT NULL DEFAULT '',
            sampling_output_dir TEXT NOT NULL DEFAULT '',
            sampling_plan_path TEXT NOT NULL DEFAULT '',
            mattersim_batch_id TEXT NOT NULL DEFAULT '',
            evaluation_summary_path TEXT NOT NULL DEFAULT '',
            evaluation_verdict TEXT NOT NULL DEFAULT '',
            recommended_return_step TEXT NOT NULL DEFAULT '',
            failure_reason TEXT NOT NULL DEFAULT ''
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_branch_formula
            ON candidates(branch, formula_key);

        CREATE TABLE IF NOT EXISTS mattersim_batches (
            batch_id TEXT PRIMARY KEY,
            branch TEXT NOT NULL,
            status TEXT NOT NULL,
            gpu_id TEXT NOT NULL DEFAULT '',
            candidate_count INTEGER NOT NULL DEFAULT 0,
            output_dir TEXT NOT NULL DEFAULT '',
            sampling_plan_path TEXT NOT NULL DEFAULT '',
            evaluation_summary_path TEXT NOT NULL DEFAULT '',
            prepare_command TEXT NOT NULL DEFAULT '',
            evaluation_command TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT '',
            failure_reason TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS batch_candidates (
            batch_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            PRIMARY KEY (batch_id, candidate_id),
            FOREIGN KEY (batch_id) REFERENCES mattersim_batches(batch_id),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
        );

        CREATE TABLE IF NOT EXISTS gpu_leases (
            gpu_id TEXT PRIMARY KEY,
            lease_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            batch_id TEXT NOT NULL DEFAULT '',
            candidate_id TEXT NOT NULL DEFAULT '',
            pid TEXT NOT NULL DEFAULT '',
            claimed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    connection.commit()


def load_parameters(output_root: Path) -> dict[str, Any]:
    payload = read_json(parameters_path(output_root))
    payload.setdefault("scheduler_mode", "streaming")
    payload.setdefault("recommendation_parallelism", DEFAULT_RECOMMENDER_PARALLELISM)
    payload.setdefault("mattersim_batch_size", DEFAULT_MATTERSIM_BATCH_SIZE)
    return payload


def output_root_has_existing_workflow(output_root: Path) -> bool:
    markers = (
        entry_dir(output_root),
        output_root / STAGE02_DIRNAME,
        output_root / SINGLE_STAGE04_DIRNAME,
        output_root / ZSCHEME_STAGE04_DIRNAME,
        output_root / SINGLE_STAGE05_DIRNAME,
        output_root / ZSCHEME_STAGE05_DIRNAME,
        output_root / "06-zscheme-system-evaluator",
        output_root / "07-reference-novelty-comparison",
        output_root / "08-round-parallel-synthesis-advisor",
        output_root / "09-synthesis-safety-feasibility-judge",
        output_root / "10-catalytic-performance-prover",
    )
    return any(path.exists() for path in markers)


def write_status(output_root: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Streaming OWS Pipeline Status",
        "",
        f"- Updated at: `{utc_now_iso()}`",
        f"- Workflow status: `{summary.get('workflow_status', '')}`",
        f"- Recommendation branch: `{summary.get('recommendation_branch', '')}`",
        f"- Recommender parallelism: `{summary.get('recommendation_parallelism', '')}`",
        f"- MatterSim batch size: `{summary.get('mattersim_batch_size', '')}`",
        "",
        "## Candidate Counts",
        "",
    ]
    for status, count in sorted(summary.get("candidate_counts", {}).items()):
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(["", "## Next Actions", "", f"- Recommenders: `{summary.get('recommender_next_action', '')}`"])
    gpu_action = summary.get("gpu_next_action")
    if gpu_action:
        lines.append(f"- GPU work: `{gpu_action}`")
    lines.append(f"- Combined: `{summary.get('next_action', '')}`")
    reason = summary.get("reason")
    if reason:
        lines.append(f"- Reason: {reason}")
    write_text_atomic(status_path(output_root), "\n".join(lines) + "\n")


def command_init(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    branch = args.recommendation_branch
    if branch not in VALID_BRANCHES:
        raise SystemExit(f"--recommendation-branch must be one of {sorted(VALID_BRANCHES)}")
    if not args.resume and output_root_has_existing_workflow(output_root):
        raise SystemExit(f"output_root already contains workflow artifacts: {output_root}. Pass --resume to reuse it.")

    gpu_id = normalize_gpu_ids(args.gpu_id)
    parameters = {
        "schema_version": SCHEMA_VERSION,
        "scheduler_mode": "streaming",
        "output_root": display_path(repo_root, output_root),
        "knowledge_base_path": args.knowledge_base_path,
        "recommendation_branch": branch,
        "execution_scope": args.execution_scope,
        "gpu_id": gpu_id,
        "target_recommendation_count": args.target_recommendation_count,
        "recommendation_parallelism": args.recommendation_parallelism,
        "mattersim_batch_size": args.mattersim_batch_size,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    state = {
        "schema_version": SCHEMA_VERSION,
        "scheduler_mode": "streaming",
        "workflow_status": "running",
        "next_action": "inspect",
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    write_json_atomic(parameters_path(output_root), parameters)
    write_json_atomic(state_path(output_root), state)
    with closing(connect_registry(output_root)):
        pass
    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return {
        "initialized": True,
        "output_root": display_path(repo_root, output_root),
        "registry_path": display_path(repo_root, registry_path(output_root)),
        "parameters_path": display_path(repo_root, parameters_path(output_root)),
        "state_path": display_path(repo_root, state_path(output_root)),
        "summary": summary,
    }


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        raw_payload = args.payload_json.strip()
        if raw_payload.startswith("{"):
            data = json.loads(raw_payload)
        else:
            payload_path = Path(args.payload_json)
            if not payload_path.exists():
                raise SystemExit(f"candidate payload path does not exist: {payload_path}")
            data = json.loads(payload_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("candidate payload must be a JSON object")
        return data
    payload = {
        "candidate_id": args.candidate_id,
        "candidate_name": args.candidate_name or args.candidate_id,
        "main_photocatalyst": args.main_photocatalyst,
        "main_photocatalyst_formula_note": args.main_photocatalyst_formula_note or "",
        "laboratory_feasibility_decision": args.laboratory_feasibility_decision,
        "violated_laboratory_limitation_ids": args.violated_laboratory_limitation_ids,
        "laboratory_feasibility_reason": args.laboratory_feasibility_reason,
    }
    return {key: value for key, value in payload.items() if value not in {None, ""}}


def payload_top_level_id(branch: str, payload: dict[str, Any]) -> str:
    if branch == "zscheme":
        return str(payload.get("zscheme_id") or payload.get("candidate_id") or "").strip()
    return str(payload.get("candidate_id") or "").strip()


def payload_candidate_name(branch: str, payload: dict[str, Any], top_level_id: str) -> str:
    if branch == "zscheme":
        return str(payload.get("zscheme_name") or payload.get("candidate_name") or top_level_id).strip()
    return str(payload.get("candidate_name") or top_level_id).strip()


def zscheme_component_formulas(payload: dict[str, Any]) -> list[str]:
    components = payload.get("components")
    if not isinstance(components, list):
        return []
    formulas = []
    for component in components:
        if isinstance(component, dict):
            formula = normalize_formula(component.get("main_photocatalyst"))
            if formula:
                formulas.append(formula)
    return formulas


def payload_formula_key(branch: str, payload: dict[str, Any], explicit_formula_key: str | None) -> str:
    if explicit_formula_key:
        return normalize_formula(explicit_formula_key)
    if payload.get("formula_key"):
        return normalize_formula(payload.get("formula_key"))
    if branch == "zscheme":
        formulas = zscheme_component_formulas(payload)
        if formulas:
            return "+".join(formulas)
        system_formula = payload.get("zscheme_formula") or payload.get("main_photocatalyst")
        return normalize_formula(system_formula)
    return normalize_formula(payload.get("main_photocatalyst"))


def candidate_dir(output_root: Path, candidate_id: str) -> Path:
    return streaming_stage02_dir(output_root) / "candidates" / candidate_id


def candidate_stage04_dir(output_root: Path, branch: str, candidate_id: str) -> Path:
    return streaming_stage04_dir(output_root, branch) / "candidates" / candidate_id


def batch_stage05_dir(output_root: Path, branch: str, batch_id: str) -> Path:
    return streaming_stage05_dir(output_root, branch) / "batches" / batch_id


def single_row_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    row = {column: payload.get(column, "") for column in SINGLE_CANDIDATE_COLUMNS}
    row["laboratory_feasibility_decision"] = row.get("laboratory_feasibility_decision") or "pass"
    row["violated_laboratory_limitation_ids"] = row.get("violated_laboratory_limitation_ids") or "none"
    return row


def zscheme_system_row(payload: dict[str, Any]) -> dict[str, Any]:
    row = {column: payload.get(column, "") for column in ZSCHEME_SYSTEM_COLUMNS}
    row["zscheme_id"] = row.get("zscheme_id") or payload.get("candidate_id", "")
    row["zscheme_name"] = row.get("zscheme_name") or payload.get("candidate_name", row["zscheme_id"])
    row["laboratory_feasibility_decision"] = row.get("laboratory_feasibility_decision") or "pass"
    row["violated_laboratory_limitation_ids"] = row.get("violated_laboratory_limitation_ids") or "none"
    return row


def zscheme_component_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    components = payload.get("components")
    if not isinstance(components, list):
        return []
    rows = []
    for component in components:
        if not isinstance(component, dict):
            continue
        row = {column: component.get(column, "") for column in ZSCHEME_COMPONENT_COLUMNS}
        row["parent_zscheme_id"] = row.get("parent_zscheme_id") or payload.get("zscheme_id", "")
        row["laboratory_feasibility_decision"] = row.get("laboratory_feasibility_decision") or "pass"
        row["violated_laboratory_limitation_ids"] = row.get("violated_laboratory_limitation_ids") or "none"
        rows.append(row)
    return rows


def write_candidate_artifacts(
    repo_root: Path,
    output_root: Path,
    branch: str,
    candidate_id: str,
    payload: dict[str, Any],
    reasoning_source: Path | None,
) -> dict[str, str]:
    out_dir = candidate_dir(output_root, candidate_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out_dir / "CANDIDATE_PAYLOAD.json", payload)
    reasoning_path = ""
    if reasoning_source:
        canonical_reasoning = out_dir / "REASONING.md"
        write_text_atomic(canonical_reasoning, reasoning_source.read_text(encoding="utf-8", errors="replace"))
        reasoning_path = display_path(repo_root, canonical_reasoning)

    if branch == "zscheme":
        write_csv_atomic(out_dir / "ZSCHEME_SYSTEM.csv", ZSCHEME_SYSTEM_COLUMNS, [zscheme_system_row(payload)])
        write_csv_atomic(
            out_dir / "ZSCHEME_COMPONENT_CANDIDATES.csv", ZSCHEME_COMPONENT_COLUMNS, zscheme_component_rows(payload)
        )
        write_csv_atomic(out_dir / "RECOMMENDED_CANDIDATE.csv", SINGLE_CANDIDATE_COLUMNS, [])
    else:
        write_csv_atomic(
            out_dir / "RECOMMENDED_CANDIDATE.csv", SINGLE_CANDIDATE_COLUMNS, [single_row_for_payload(payload)]
        )
        write_csv_atomic(out_dir / "ZSCHEME_SYSTEM.csv", ZSCHEME_SYSTEM_COLUMNS, [])
        write_csv_atomic(out_dir / "ZSCHEME_COMPONENT_CANDIDATES.csv", ZSCHEME_COMPONENT_COLUMNS, [])
    return {
        "candidate_dir": display_path(repo_root, out_dir),
        "payload_path": display_path(repo_root, out_dir / "CANDIDATE_PAYLOAD.json"),
        "reasoning_path": reasoning_path,
    }


def materialize_streaming_handoffs(repo_root: Path, output_root: Path) -> dict[str, str]:
    with closing(connect_registry(output_root)) as connection:
        rows = connection.execute(
            "SELECT branch, payload_json FROM candidates ORDER BY created_at, candidate_id"
        ).fetchall()
    single_rows: list[dict[str, Any]] = []
    system_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    for row in rows:
        payload = json_loads_dict(row["payload_json"])
        if row["branch"] == "zscheme":
            system_rows.append(zscheme_system_row(payload))
            component_rows.extend(zscheme_component_rows(payload))
        else:
            single_rows.append(single_row_for_payload(payload))

    out_dir = streaming_stage02_dir(output_root)
    write_csv_atomic(out_dir / "RECOMMENDED_CANDIDATES.csv", SINGLE_CANDIDATE_COLUMNS, single_rows)
    write_csv_atomic(out_dir / "ZSCHEME_SYSTEMS.csv", ZSCHEME_SYSTEM_COLUMNS, system_rows)
    write_csv_atomic(out_dir / "ZSCHEME_COMPONENT_CANDIDATES.csv", ZSCHEME_COMPONENT_COLUMNS, component_rows)
    return {
        "recommended_candidates": display_path(repo_root, out_dir / "RECOMMENDED_CANDIDATES.csv"),
        "zscheme_systems": display_path(repo_root, out_dir / "ZSCHEME_SYSTEMS.csv"),
        "zscheme_components": display_path(repo_root, out_dir / "ZSCHEME_COMPONENT_CANDIDATES.csv"),
    }


def command_register_candidate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    parameters = load_parameters(output_root)
    branch = args.branch or parameters.get("recommendation_branch")
    if branch not in VALID_BRANCHES:
        raise SystemExit(f"branch must be one of {sorted(VALID_BRANCHES)}")
    payload = load_payload(args)
    top_level_id = payload_top_level_id(branch, payload)
    if not top_level_id:
        raise SystemExit("candidate payload must include candidate_id or zscheme_id")
    formula_key = payload_formula_key(branch, payload, args.formula_key)
    if not formula_key:
        raise SystemExit("candidate payload must include a parseable formula or --formula-key")

    reasoning_source = None
    if args.reasoning_file:
        reasoning_source = resolve_repo_path(repo_root, args.reasoning_file)
        if not reasoning_source.exists():
            raise SystemExit(f"reasoning file does not exist: {reasoning_source}")

    now = utc_now_iso()
    payload.setdefault("candidate_id", top_level_id)
    if branch == "zscheme":
        payload.setdefault("zscheme_id", top_level_id)
    candidate_name = payload_candidate_name(branch, payload, top_level_id)
    main_photocatalyst = str(payload.get("main_photocatalyst") or formula_key).strip()
    reasoning_family_key = args.reasoning_family_key or str(payload.get("reasoning_family_key") or formula_key)
    history_duplicates = history_duplicate_matches(
        repo_root,
        Path(args.history_dir),
        branch,
        payload,
        formula_key,
    )
    if history_duplicates and not args.allow_history_duplicate:
        return {
            "registered": False,
            "status": "duplicate_history_formula",
            "candidate_id": top_level_id,
            "formula_key": formula_key,
            "history_duplicate_matches": history_duplicates,
            "message": (
                "Formula already exists in data/history successful cumulative routes; recommend a different candidate."
            ),
            "agent_next_action": {
                "next_action": "recommend_different_formula_and_retry",
                "agent": args.agent or "stage02_recommender",
                "run_mode": "continuous_recommendation_loop",
                "reason": (
                    "The current reduced formula is already present in data/history successful routes. "
                    "The same running recommender should reread the registry, history duplicate feedback, "
                    "choose a different formula, and call register-candidate again."
                ),
            },
        }

    with closing(connect_registry(output_root)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT candidate_id, status FROM candidates WHERE branch = ? AND formula_key = ?",
                (branch, formula_key),
            ).fetchone()
            if duplicate:
                connection.rollback()
                return {
                    "registered": False,
                    "status": "duplicate_formula",
                    "candidate_id": top_level_id,
                    "formula_key": formula_key,
                    "existing_candidate_id": duplicate["candidate_id"],
                    "existing_status": duplicate["status"],
                    "message": "Formula already exists in the streaming registry; recommend a different candidate.",
                    "agent_next_action": {
                        "next_action": "recommend_different_formula_and_retry",
                        "agent": args.agent or "stage02_recommender",
                        "run_mode": "continuous_recommendation_loop",
                        "reason": (
                            "The current formula is already registered. The same running recommender should "
                            "reread the registry, choose a different formula, and call register-candidate again."
                        ),
                    },
                }
            artifacts = write_candidate_artifacts(
                repo_root, output_root, branch, top_level_id, payload, reasoning_source
            )
            connection.execute(
                """
                INSERT INTO candidates (
                    candidate_id, branch, top_level_id, candidate_name, main_photocatalyst,
                    formula_key, reasoning_family_key, status, payload_json, reasoning_path,
                    recommendation_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    top_level_id,
                    branch,
                    top_level_id,
                    candidate_name,
                    main_photocatalyst,
                    formula_key,
                    reasoning_family_key,
                    "accepted",
                    json_dumps(payload),
                    artifacts.get("reasoning_path", ""),
                    args.agent or "",
                    now,
                    now,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise SystemExit(f"candidate registration failed: {exc}") from exc

    handoffs = materialize_streaming_handoffs(repo_root, output_root)
    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return {
        "registered": True,
        "candidate_id": top_level_id,
        "branch": branch,
        "formula_key": formula_key,
        "status": "accepted",
        "artifacts": artifacts,
        "streaming_handoffs": handoffs,
        "agent_next_action": {
            "next_action": "continue_recommendation_loop",
            "agent": args.agent or "stage02_recommender",
            "run_mode": "continuous_recommendation_loop",
            "reason": (
                "The candidate was registered successfully. The same running recommender should reread "
                "the latest registry, history, and available feedback, then recommend and register the "
                "next candidate without waiting for MatterGen or MatterSim results."
            ),
        },
        "parent_observation": {
            "next_action": "keep_recommender_running",
            "agent": args.agent or "stage02_recommender",
            "reason": (
                "This registration is one iteration from a long-running Stage02 producer. Do not start a "
                "replacement unless the recommender exits, fails, or the reported active count drops below "
                "the configured parallelism."
            ),
        },
    }


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row}


def reject_history_duplicate_candidate(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    matches: list[dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    reason = json_dumps(
        {
            "reason": "Formula duplicates data/history successful cumulative route.",
            "history_duplicate_matches": matches,
        }
    )
    connection.execute(
        """
        UPDATE candidates
        SET status = 'history_duplicate_rejected',
            recommended_return_step = 'return_to_02_history_duplicate_audit',
            failure_reason = ?,
            updated_at = ?
        WHERE candidate_id = ?
        """,
        (reason, now, row["candidate_id"]),
    )
    return {
        "candidate_id": row["candidate_id"],
        "formula_key": row["formula_key"],
        "history_duplicate_matches": matches,
    }


def candidate_single_csv_path(output_root: Path, candidate_id: str) -> Path:
    return candidate_dir(output_root, candidate_id) / "RECOMMENDED_CANDIDATE.csv"


def zscheme_components_csv_path(output_root: Path, candidate_id: str) -> Path:
    return candidate_dir(output_root, candidate_id) / "ZSCHEME_COMPONENT_CANDIDATES.csv"


def candidate_sampling_plan_path(output_root: Path, branch: str, candidate_id: str) -> Path:
    return candidate_stage04_dir(output_root, branch, candidate_id) / "STRUCTURE_SAMPLING_PLAN.csv"


def command_claim_sampling(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    gpu_id = normalize_gpu_ids(args.gpu_id)
    if not gpu_id:
        raise SystemExit("--gpu-id must be a confirmed CUDA GPU id such as 0 or 0,1")
    parameters = load_parameters(output_root)
    batch_size = args.batch_size or int(parameters.get("mattergen_batch_size") or 12)
    history_dir = Path(args.history_dir)
    skipped_history_duplicates: list[dict[str, Any]] = []
    with closing(connect_registry(output_root)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = None
        while True:
            if args.candidate_id:
                row = connection.execute(
                    "SELECT * FROM candidates WHERE candidate_id = ? AND status IN ('accepted', 'sampling_queued')",
                    (args.candidate_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM candidates
                    WHERE status IN ('accepted', 'sampling_queued')
                    ORDER BY created_at, candidate_id
                    LIMIT 1
                    """
                ).fetchone()
            if not row:
                break
            payload = json_loads_dict(row["payload_json"])
            history_duplicates = history_duplicate_matches(
                repo_root,
                history_dir,
                row["branch"],
                payload,
                row["formula_key"],
            )
            if not history_duplicates:
                break
            skipped = reject_history_duplicate_candidate(connection, row, history_duplicates, utc_now_iso())
            skipped_history_duplicates.append(skipped)
            if args.candidate_id:
                connection.commit()
                summary = inspect_streaming(repo_root, output_root)
                write_status(output_root, summary)
                return {
                    "claimed": False,
                    "status": "duplicate_history_formula",
                    "candidate_id": row["candidate_id"],
                    "formula_key": row["formula_key"],
                    "history_duplicate_matches": history_duplicates,
                    "message": (
                        "Formula already exists in data/history successful cumulative routes; "
                        "candidate was rejected before MatterGen sampling."
                    ),
                }
        if not row:
            if skipped_history_duplicates:
                connection.commit()
                summary = inspect_streaming(repo_root, output_root)
                write_status(output_root, summary)
            else:
                connection.rollback()
            return {
                "claimed": False,
                "reason": "no accepted candidates are ready for MatterGen sampling",
                "skipped_history_duplicates": skipped_history_duplicates,
            }
        candidate_id = row["candidate_id"]
        branch = row["branch"]
        stage04_dir = candidate_stage04_dir(output_root, branch, candidate_id)
        recommendations_path = (
            zscheme_components_csv_path(output_root, candidate_id)
            if branch == "zscheme"
            else candidate_single_csv_path(output_root, candidate_id)
        )
        now = utc_now_iso()
        connection.execute(
            """
            UPDATE candidates
            SET status = 'sampling_running',
                sampling_gpu_id = ?,
                sampling_started_at = ?,
                sampling_output_dir = ?,
                sampling_plan_path = ?,
                updated_at = ?
            WHERE candidate_id = ?
            """,
            (
                gpu_id,
                now,
                display_path(repo_root, stage04_dir),
                display_path(repo_root, candidate_sampling_plan_path(output_root, branch, candidate_id)),
                now,
                candidate_id,
            ),
        )
        connection.commit()

    command = (
        "python skills/mattergen-structure-sampler/scripts/prepare_mattergen_sampling.py "
        f"--recommendations-path {display_path(repo_root, recommendations_path)} "
        f"--output-dir {display_path(repo_root, stage04_dir)} "
        f"--gpu-id {gpu_id} "
        f"--batch-size {batch_size}"
    )
    return {
        "claimed": True,
        "candidate_id": candidate_id,
        "branch": branch,
        "gpu_id": gpu_id,
        "recommendations_path": display_path(repo_root, recommendations_path),
        "stage04_output_dir": display_path(repo_root, stage04_dir),
        "sampling_plan_path": display_path(repo_root, candidate_sampling_plan_path(output_root, branch, candidate_id)),
        "prepare_command": command,
        "skipped_history_duplicates": skipped_history_duplicates,
    }


def sampling_artifact_exists(repo_root: Path, output_root: Path, branch: str, candidate_id: str) -> tuple[bool, str]:
    stage04_dir = candidate_stage04_dir(output_root, branch, candidate_id)
    plan_rows = read_csv_rows(stage04_dir / "STRUCTURE_SAMPLING_PLAN.csv")
    if plan_rows:
        found = []
        missing = []
        for row in plan_rows:
            row_candidate_id = (row.get("candidate_id") or "").strip()
            sample_dir_value = (row.get("sampling_output_dir") or "").strip()
            sample_dir = Path(sample_dir_value) if sample_dir_value else stage04_dir / "samples" / row_candidate_id
            if not sample_dir.is_absolute():
                sample_dir = repo_root / sample_dir
            matches = [
                sample_dir / "generated_crystals.extxyz",
                sample_dir / "generated_structures.extxyz",
                sample_dir / "generated_crystals_cif.zip",
            ]
            present = next((path for path in matches if path.exists()), None)
            if present:
                found.append(display_path(repo_root, present))
            else:
                missing.append(row_candidate_id or candidate_id)
        if found and not missing:
            return True, ";".join(found)
        return False, ";".join(found)

    candidates = [
        stage04_dir / "samples" / candidate_id / "generated_crystals.extxyz",
        stage04_dir / "samples" / candidate_id / "generated_structures.extxyz",
        stage04_dir / "samples" / candidate_id / "generated_crystals_cif.zip",
    ]
    for path in candidates:
        if path.exists():
            return True, display_path(repo_root, path)
    return False, ""


def command_complete_sampling(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    with closing(connect_registry(output_root)) as connection:
        row = connection.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?",
            (args.candidate_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"unknown candidate_id: {args.candidate_id}")
        ok, artifact = sampling_artifact_exists(repo_root, output_root, row["branch"], row["candidate_id"])
        if args.status == "failed":
            ok = False
        status = "sampled" if ok else "sampling_failed"
        reason = "" if ok else (args.failure_reason or "MatterGen sampling artifact was not found.")
        now = utc_now_iso()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE candidates
            SET status = ?, sampling_completed_at = ?, failure_reason = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            (status, now, reason, now, args.candidate_id),
        )
        connection.commit()
    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return {
        "candidate_id": args.candidate_id,
        "status": status,
        "sampling_artifact": artifact,
        "failure_reason": reason,
    }


def next_batch_id(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT COUNT(*) AS count FROM mattersim_batches").fetchone()
    return f"ms-b{int(row['count']) + 1:06d}"


def gpu_lease_active(connection: sqlite3.Connection, gpu_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM gpu_leases WHERE gpu_id = ? AND status = 'running'",
        (gpu_id,),
    ).fetchone()


def sampling_plan_rows_for_candidates(
    repo_root: Path, output_root: Path, candidates: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        plan_path = candidate_sampling_plan_path(output_root, candidate["branch"], candidate["candidate_id"])
        plan_rows = read_csv_rows(plan_path)
        if not plan_rows:
            rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "candidate_name": candidate["candidate_name"],
                    "main_photocatalyst": candidate["main_photocatalyst"],
                    "sampling_status": READY_TO_SAMPLE,
                    "blocked_reason": "missing_candidate_sampling_plan",
                    "recommended_return_step": "return_to_04_sampling",
                }
            )
            continue
        for raw_plan_row in plan_rows:
            plan_row = dict(raw_plan_row)
            output_dir = plan_row.get("sampling_output_dir", "")
            if output_dir:
                structures_path = Path(output_dir)
                if not structures_path.is_absolute():
                    structures_path = repo_root / structures_path
                for name in ("generated_crystals.extxyz", "generated_structures.extxyz"):
                    candidate_structures = structures_path / name
                    if candidate_structures.exists():
                        plan_row["structures_path"] = display_path(repo_root, candidate_structures)
                        break
            rows.append(plan_row)
    return rows


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def command_claim_mattersim_batch(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    gpu_id = normalize_gpu_ids(args.gpu_id)
    if not gpu_id:
        raise SystemExit("--gpu-id must be a single confirmed CUDA GPU id")
    if "," in gpu_id:
        raise SystemExit("--gpu-id for MatterSim batch claims must name exactly one GPU")
    parameters = load_parameters(output_root)
    batch_size = args.batch_size or int(parameters.get("mattersim_batch_size") or DEFAULT_MATTERSIM_BATCH_SIZE)
    branch = args.branch or parameters.get("recommendation_branch")
    if branch not in VALID_BRANCHES:
        raise SystemExit(f"branch must be one of {sorted(VALID_BRANCHES)}")

    with closing(connect_registry(output_root)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = gpu_lease_active(connection, gpu_id)
        if lease:
            connection.rollback()
            return {
                "claimed": False,
                "reason": "gpu_busy",
                "gpu_id": gpu_id,
                "active_lease": row_to_dict(lease),
            }
        candidates = connection.execute(
            """
            SELECT * FROM candidates
            WHERE branch = ?
              AND status = 'sampled'
              AND mattersim_batch_id = ''
            ORDER BY sampling_completed_at, created_at, candidate_id
            LIMIT ?
            """,
            (branch, batch_size),
        ).fetchall()
        if len(candidates) < batch_size and not args.allow_partial:
            connection.rollback()
            return {
                "claimed": False,
                "reason": "insufficient_sampled_candidates",
                "sampled_count": len(candidates),
                "required_count": batch_size,
            }
        if not candidates:
            connection.rollback()
            return {"claimed": False, "reason": "no sampled candidates are ready for MatterSim"}

        batch_id = args.batch_id or next_batch_id(connection)
        batch_dir = batch_stage05_dir(output_root, branch, batch_id)
        sampling_plan_path = batch_dir / "STRUCTURE_SAMPLING_PLAN.csv"
        candidate_ids = [candidate["candidate_id"] for candidate in candidates]
        now = utc_now_iso()
        prepare_command = (
            "python skills/mattersim-structure-evaluator/scripts/prepare_mattersim_evaluation.py "
            f"--repo-root {display_path(repo_root, repo_root)} "
            f"--output-root {display_path(repo_root, output_root)} "
            f"--sampling-plan-path {display_path(repo_root, sampling_plan_path)} "
            f"--output-dir {display_path(repo_root, batch_dir)} "
            f"--gpu-id {gpu_id}"
        )
        connection.execute(
            """
            INSERT INTO mattersim_batches (
                batch_id, branch, status, gpu_id, candidate_count, output_dir,
                sampling_plan_path, prepare_command, created_at, updated_at
            ) VALUES (?, ?, 'eval_running', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                branch,
                gpu_id,
                len(candidate_ids),
                display_path(repo_root, batch_dir),
                display_path(repo_root, sampling_plan_path),
                prepare_command,
                now,
                now,
            ),
        )
        for candidate_id in candidate_ids:
            connection.execute(
                "INSERT INTO batch_candidates(batch_id, candidate_id) VALUES (?, ?)",
                (batch_id, candidate_id),
            )
            connection.execute(
                """
                UPDATE candidates
                SET status = 'eval_running', mattersim_batch_id = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (batch_id, now, candidate_id),
            )
        connection.execute(
            """
            INSERT OR REPLACE INTO gpu_leases(
                gpu_id, lease_kind, status, batch_id, candidate_id, pid, claimed_at, updated_at
            ) VALUES (?, 'mattersim', 'running', ?, '', ?, ?, ?)
            """,
            (gpu_id, batch_id, args.pid or "", now, now),
        )
        connection.commit()

    sampling_rows = sampling_plan_rows_for_candidates(repo_root, output_root, candidates)
    write_csv_atomic(sampling_plan_path, SAMPLING_PLAN_COLUMNS, sampling_rows)
    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return {
        "claimed": True,
        "batch_id": batch_id,
        "branch": branch,
        "gpu_id": gpu_id,
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "sampling_plan_path": display_path(repo_root, sampling_plan_path),
        "output_dir": display_path(repo_root, batch_dir),
        "prepare_command": prepare_command,
        "postprocess_instruction": (
            "Run the prepare command, execute the generated combined evaluation command, "
            "then rerun the prepare command before complete-mattersim-batch."
        ),
    }


def command_complete_mattersim_batch(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    with closing(connect_registry(output_root)) as connection:
        batch = connection.execute(
            "SELECT * FROM mattersim_batches WHERE batch_id = ?",
            (args.batch_id,),
        ).fetchone()
        if not batch:
            raise SystemExit(f"unknown batch_id: {args.batch_id}")
        summary_path = (
            resolve_repo_path(repo_root, args.summary_path)
            if args.summary_path
            else resolve_repo_path(
                repo_root,
                batch["output_dir"],
            )
            / "STRUCTURE_EVALUATION_SUMMARY.csv"
        )
        if args.status != "failed" and not summary_path.exists():
            raise SystemExit(f"summary path does not exist: {summary_path}")
        summary_rows = read_csv_rows(summary_path) if summary_path.exists() else []
        rows_by_candidate: dict[str, list[dict[str, str]]] = {}
        for row in summary_rows:
            candidate_id = (row.get("candidate_id") or "").strip()
            if candidate_id:
                rows_by_candidate.setdefault(candidate_id, []).append(row)

        now = utc_now_iso()
        connection.execute("BEGIN IMMEDIATE")
        candidate_rows = connection.execute(
            "SELECT candidate_id FROM batch_candidates WHERE batch_id = ?",
            (args.batch_id,),
        ).fetchall()
        updated_candidates = []
        for item in candidate_rows:
            candidate_id = item["candidate_id"]
            candidate_summary = rows_by_candidate.get(candidate_id, [])
            if args.status == "failed":
                status = "eval_failed"
                verdict = ""
                return_step = ""
                reason = args.failure_reason or "MatterSim batch failed."
            elif any(
                (row.get("recommended_return_step") or "").strip() == PROCEED_TO_EXPERIMENTAL_VALIDATION
                for row in candidate_summary
            ):
                best = next(
                    row
                    for row in candidate_summary
                    if (row.get("recommended_return_step") or "").strip() == PROCEED_TO_EXPERIMENTAL_VALIDATION
                )
                status = "reliable"
                verdict = best.get("evaluation_verdict", "")
                return_step = best.get("recommended_return_step", "")
                reason = best.get("routing_reason", "")
            elif candidate_summary:
                first = candidate_summary[0]
                status = "evaluated_failed"
                verdict = first.get("evaluation_verdict", "")
                return_step = first.get("recommended_return_step", "")
                reason = first.get("routing_reason", "")
            else:
                status = "eval_failed"
                verdict = ""
                return_step = ""
                reason = "MatterSim summary has no rows for this candidate."
            connection.execute(
                """
                UPDATE candidates
                SET status = ?,
                    evaluation_summary_path = ?,
                    evaluation_verdict = ?,
                    recommended_return_step = ?,
                    failure_reason = ?,
                    updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    status,
                    display_path(repo_root, summary_path) if summary_path.exists() else "",
                    verdict,
                    return_step,
                    reason,
                    now,
                    candidate_id,
                ),
            )
            updated_candidates.append(
                {"candidate_id": candidate_id, "status": status, "recommended_return_step": return_step}
            )
        batch_status = "eval_failed" if args.status == "failed" else "completed"
        connection.execute(
            """
            UPDATE mattersim_batches
            SET status = ?, evaluation_summary_path = ?, completed_at = ?, updated_at = ?, failure_reason = ?
            WHERE batch_id = ?
            """,
            (
                batch_status,
                display_path(repo_root, summary_path) if summary_path.exists() else "",
                now,
                now,
                args.failure_reason or "",
                args.batch_id,
            ),
        )
        connection.execute(
            "UPDATE gpu_leases SET status = 'released', updated_at = ? WHERE batch_id = ? AND lease_kind = 'mattersim'",
            (now, args.batch_id),
        )
        connection.commit()
    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return {
        "batch_id": args.batch_id,
        "status": batch_status,
        "summary_path": display_path(repo_root, summary_path) if summary_path.exists() else "",
        "updated_candidates": updated_candidates,
    }


def split_identifier_args(values: list[str] | None) -> list[str]:
    identifiers: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for item in str(value or "").replace("\n", ",").split(","):
            identifier = item.strip()
            if identifier and identifier not in seen:
                identifiers.append(identifier)
                seen.add(identifier)
    return identifiers


def read_only_registry(output_root: Path) -> sqlite3.Connection:
    path = registry_path(output_root)
    if not path.exists():
        raise SystemExit(f"registry path does not exist: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def find_reused_mattersim_evidence(
    repo_root: Path,
    source_output_root: Path,
    branch: str,
    formula_key: str,
    main_photocatalyst: str,
) -> dict[str, Any]:
    formula_values = [value for value in {formula_key, normalize_formula(main_photocatalyst)} if value]
    if not formula_values:
        return {}
    placeholders = ",".join("?" for _ in formula_values)
    query = f"""
        SELECT candidate_id, main_photocatalyst, formula_key, evaluation_summary_path,
               evaluation_verdict, recommended_return_step
        FROM candidates
        WHERE branch = ?
          AND status = 'reliable'
          AND (formula_key IN ({placeholders}) OR main_photocatalyst IN ({placeholders}))
        ORDER BY updated_at DESC, candidate_id
    """
    with read_only_registry(source_output_root) as connection:
        rows = connection.execute(query, [branch, *formula_values, *formula_values]).fetchall()
    for row in rows:
        summary_value = str(row["evaluation_summary_path"] or "")
        summary_path = resolve_repo_path(repo_root, summary_value) if summary_value else Path("")
        if summary_path and summary_path.exists():
            summary_rows = read_csv_rows(summary_path)
            proceeding = [
                item
                for item in summary_rows
                if (item.get("candidate_id") or "").strip() == str(row["candidate_id"])
                and (item.get("recommended_return_step") or "").strip() == PROCEED_TO_EXPERIMENTAL_VALIDATION
            ]
            if proceeding:
                best = sorted(
                    proceeding,
                    key=lambda item: float(item.get("energy_above_hull") or "999999"),
                )[0]
                return {
                    "evaluation_verdict": best.get("evaluation_verdict") or "passes_mlff_screen",
                    "energy_above_hull": best.get("energy_above_hull", ""),
                    "stability": best.get("stability", "True"),
                    "novelty": best.get("novelty", "True"),
                    "uniqueness": best.get("uniqueness", "True"),
                    "relaxed_space_group_symbol": best.get("relaxed_space_group_symbol", ""),
                    "relaxed_space_group_number": best.get("relaxed_space_group_number", ""),
                    "relaxed_crystal_system": best.get("relaxed_crystal_system", ""),
                    "space_group_analysis_note": best.get("space_group_analysis_note", "reused_reliable_mlff_screen"),
                }
        if str(row["recommended_return_step"] or "") == PROCEED_TO_EXPERIMENTAL_VALIDATION:
            return {
                "evaluation_verdict": str(row["evaluation_verdict"] or "passes_mlff_screen"),
                "stability": "True",
                "novelty": "True",
                "uniqueness": "True",
                "space_group_analysis_note": "reused_reliable_mlff_screen",
            }
    return {}


def next_reuse_batch_id(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT batch_id FROM mattersim_batches WHERE batch_id LIKE 'reuse-b%'").fetchall()
    highest = 0
    for row in rows:
        match = re.fullmatch(r"reuse-b(\d+)", str(row["batch_id"] or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"reuse-b{highest + 1:06d}"


def command_reuse_mattersim_results(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    source_output_root = resolve_repo_path(repo_root, args.source_output_root)
    parameters = load_parameters(output_root)
    branch = args.branch or parameters.get("recommendation_branch")
    if branch not in VALID_BRANCHES:
        raise SystemExit(f"branch must be one of {sorted(VALID_BRANCHES)}")
    candidate_ids = split_identifier_args(args.candidate_id)
    if not candidate_ids:
        raise SystemExit("at least one --candidate-id is required")

    with closing(connect_registry(output_root)) as connection:
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = connection.execute(
            f"""
            SELECT *
            FROM candidates
            WHERE candidate_id IN ({placeholders})
            ORDER BY created_at, candidate_id
            """,
            candidate_ids,
        ).fetchall()
        found_ids = {str(row["candidate_id"]) for row in rows}
        missing_ids = [candidate_id for candidate_id in candidate_ids if candidate_id not in found_ids]
        if missing_ids:
            raise SystemExit(f"unknown candidate_id values: {', '.join(missing_ids)}")
        invalid = [
            {"candidate_id": row["candidate_id"], "status": row["status"]}
            for row in rows
            if row["branch"] != branch or row["status"] not in {"accepted", "sampling_queued"}
        ]
        if invalid:
            raise SystemExit(f"candidates are not eligible for direct MatterSim reuse: {json_dumps(invalid)}")

        evidence_by_candidate: dict[str, dict[str, Any]] = {}
        summary_rows: list[dict[str, Any]] = []
        for row in rows:
            evidence = find_reused_mattersim_evidence(
                repo_root,
                source_output_root,
                branch,
                str(row["formula_key"] or ""),
                str(row["main_photocatalyst"] or ""),
            )
            if not evidence and args.require_existing_evidence:
                raise SystemExit(
                    f"no reliable existing MatterSim evidence found for candidate_id: {row['candidate_id']}"
                )
            evidence.setdefault("evaluation_verdict", "passes_reused_mlff_screen")
            evidence.setdefault("stability", "True")
            evidence.setdefault("novelty", "True")
            evidence.setdefault("uniqueness", "True")
            evidence.setdefault("space_group_analysis_note", "reused_reliable_mlff_screen")
            candidate_id = str(row["candidate_id"])
            evidence_by_candidate[candidate_id] = evidence
            summary_rows.append(
                {
                    "candidate_id": candidate_id,
                    "structure_id": f"{candidate_id}::reused_best",
                    "metrics_path": "",
                    "detailed_metrics_path": "",
                    "relaxed_structures_path": "",
                    "relaxed_space_group_symbol": evidence.get("relaxed_space_group_symbol", ""),
                    "relaxed_space_group_number": evidence.get("relaxed_space_group_number", ""),
                    "relaxed_crystal_system": evidence.get("relaxed_crystal_system", ""),
                    "space_group_analysis_note": evidence.get(
                        "space_group_analysis_note", "reused_reliable_mlff_screen"
                    ),
                    "energy_above_hull": evidence.get("energy_above_hull", ""),
                    "stability": evidence.get("stability", "True"),
                    "novelty": evidence.get("novelty", "True"),
                    "uniqueness": evidence.get("uniqueness", "True"),
                    "evaluation_verdict": "passes_reused_mlff_screen",
                    "recommended_return_step": PROCEED_TO_EXPERIMENTAL_VALIDATION,
                    "routing_reason": (
                        "已登记的可靠 MatterSim 筛选结论满足当前直接复用规则; "
                        "本轮不重复评测, 并进入当前批次合成路线、证明过程和路线可行性重写。"
                    ),
                }
            )

        batch_id = args.batch_id or next_reuse_batch_id(connection)
        existing_batch = connection.execute(
            "SELECT batch_id FROM mattersim_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if existing_batch:
            raise SystemExit(f"batch_id already exists: {batch_id}")
        batch_dir = batch_stage05_dir(output_root, branch, batch_id)
        summary_path = batch_dir / "STRUCTURE_EVALUATION_SUMMARY.csv"
        evidence_path = batch_dir / "REUSED_MATTERSIM_EVIDENCE.json"
        write_csv_atomic(summary_path, EVALUATION_SUMMARY_COLUMNS, summary_rows)
        write_json_atomic(
            evidence_path,
            {
                "batch_id": batch_id,
                "reuse_policy": (
                    "existing reliable MatterSim screening evidence was reused without rerunning GPU evaluation"
                ),
                "generated_at": utc_now_iso(),
                "candidate_count": len(rows),
                "candidates": [
                    {
                        "candidate_id": str(row["candidate_id"]),
                        "main_photocatalyst": str(row["main_photocatalyst"] or ""),
                        "formula_key": str(row["formula_key"] or ""),
                        "evaluation_verdict": "passes_reused_mlff_screen",
                        "recommended_return_step": PROCEED_TO_EXPERIMENTAL_VALIDATION,
                        "reused_fields": evidence_by_candidate.get(str(row["candidate_id"]), {}),
                    }
                    for row in rows
                ],
            },
        )

        now = utc_now_iso()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO mattersim_batches (
                batch_id, branch, status, gpu_id, candidate_count, output_dir,
                sampling_plan_path, evaluation_summary_path, prepare_command,
                evaluation_command, created_at, updated_at, completed_at, failure_reason
            ) VALUES (?, ?, 'completed', '', ?, ?, '', ?, '', '', ?, ?, ?, '')
            """,
            (
                batch_id,
                branch,
                len(rows),
                display_path(repo_root, batch_dir),
                display_path(repo_root, summary_path),
                now,
                now,
                now,
            ),
        )
        updated_candidates: list[dict[str, str]] = []
        for row in rows:
            candidate_id = str(row["candidate_id"])
            connection.execute(
                "INSERT INTO batch_candidates(batch_id, candidate_id) VALUES (?, ?)",
                (batch_id, candidate_id),
            )
            connection.execute(
                """
                UPDATE candidates
                SET status = 'reliable',
                    mattersim_batch_id = ?,
                    evaluation_summary_path = ?,
                    evaluation_verdict = 'passes_reused_mlff_screen',
                    recommended_return_step = ?,
                    failure_reason = ?,
                    updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    batch_id,
                    display_path(repo_root, summary_path),
                    PROCEED_TO_EXPERIMENTAL_VALIDATION,
                    "已登记的可靠 MatterSim 筛选结论满足当前直接复用规则; 本轮不重复评测。",
                    now,
                    candidate_id,
                ),
            )
            updated_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "status": "reliable",
                    "recommended_return_step": PROCEED_TO_EXPERIMENTAL_VALIDATION,
                }
            )
        connection.commit()

    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return {
        "batch_id": batch_id,
        "status": "completed",
        "summary_path": display_path(repo_root, summary_path),
        "evidence_path": display_path(repo_root, evidence_path),
        "updated_candidates": updated_candidates,
    }


def command_release_gpu(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    gpu_id = normalize_gpu_ids(args.gpu_id)
    if not gpu_id:
        raise SystemExit("--gpu-id must be valid")
    with closing(connect_registry(output_root)) as connection:
        connection.execute(
            "UPDATE gpu_leases SET status = 'released', updated_at = ? WHERE gpu_id = ?",
            (utc_now_iso(), gpu_id),
        )
        connection.commit()
    return {"released": True, "gpu_id": gpu_id}


def candidate_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM candidates GROUP BY status ORDER BY status"
    ).fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def active_gpu_leases(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT * FROM gpu_leases WHERE status = 'running' ORDER BY gpu_id").fetchall()
    leases: list[dict[str, Any]] = []
    for row in rows:
        lease = row_to_dict(row)
        if lease is not None:
            leases.append(lease)
    return leases


def batch_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM mattersim_batches GROUP BY status ORDER BY status"
    ).fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def reliable_history_duplicate_records(
    repo_root: Path,
    output_root: Path,
    history_dir: Path = DEFAULT_HISTORY_DIR,
) -> list[dict[str, Any]]:
    history_index = read_history_success_formula_index(repo_root, history_dir)
    with closing(connect_registry(output_root)) as connection:
        rows = connection.execute(
            """
            SELECT candidate_id, branch, candidate_name, main_photocatalyst, formula_key, payload_json
            FROM candidates
            WHERE status = 'reliable'
            ORDER BY updated_at, candidate_id
            """
        ).fetchall()
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        payload = json_loads_dict(row["payload_json"])
        formula_key = str(row["formula_key"] or "")
        matches = history_duplicate_matches(
            repo_root,
            history_dir,
            str(row["branch"] or ""),
            payload,
            formula_key,
            history_index,
        )
        if matches:
            duplicates.append(
                {
                    "candidate_id": row["candidate_id"],
                    "branch": row["branch"],
                    "candidate_name": row["candidate_name"],
                    "main_photocatalyst": row["main_photocatalyst"],
                    "formula_key": formula_key,
                    "history_duplicate_matches": matches,
                }
            )
    return duplicates


def available_gpu_ids(parameters: dict[str, Any], leases: list[dict[str, Any]]) -> list[str]:
    configured = normalize_gpu_ids(parameters.get("gpu_id"))
    if not configured:
        return []
    busy = {str(lease.get("gpu_id")) for lease in leases}
    return [gpu_id for gpu_id in configured.split(",") if gpu_id and gpu_id not in busy]


def inspect_streaming(repo_root: Path, output_root: Path) -> dict[str, Any]:
    parameters = load_parameters(output_root)
    with closing(connect_registry(output_root)) as connection:
        counts = candidate_counts(connection)
        leases = active_gpu_leases(connection)
        batches = batch_counts(connection)
        sampled_count = int(counts.get("sampled", 0))
        accepted_count = int(counts.get("accepted", 0)) + int(counts.get("sampling_queued", 0))
        reliable_count = int(counts.get("reliable", 0))
        total_candidates = sum(counts.values())
    reliable_history_duplicates = reliable_history_duplicate_records(repo_root, output_root)
    history_duplicate_reliable_count = len(reliable_history_duplicates)
    history_clean_reliable_count = max(reliable_count - history_duplicate_reliable_count, 0)
    gpus = available_gpu_ids(parameters, leases)
    batch_size = int(parameters.get("mattersim_batch_size") or DEFAULT_MATTERSIM_BATCH_SIZE)
    parallelism = int(parameters.get("recommendation_parallelism") or DEFAULT_RECOMMENDER_PARALLELISM)
    gpu_next_action = ""
    gpu_reason = ""
    if sampled_count >= batch_size and gpus:
        gpu_next_action = "claim_mattersim_batch"
        gpu_reason = f"{sampled_count} sampled candidates are ready; MatterSim batch size is {batch_size}."
    elif accepted_count > 0:
        gpu_next_action = "claim_sampling"
        gpu_reason = f"{accepted_count} accepted candidates are ready for MatterGen sampling."
    recommender_next_action = "maintain_stage02_recommenders"
    recommender_reason = f"Maintain {parallelism} active Stage02 recommender slots continuously."
    next_action = "run_concurrent_actions"
    reason_parts = [recommender_reason]
    if gpu_reason:
        reason_parts.append(gpu_reason)
    reason = " ".join(reason_parts)
    return {
        "workflow_status": "running",
        "output_root": display_path(repo_root, output_root),
        "registry_path": display_path(repo_root, registry_path(output_root)),
        "recommendation_branch": parameters.get("recommendation_branch"),
        "recommendation_parallelism": parallelism,
        "mattersim_batch_size": batch_size,
        "configured_gpu_ids": normalize_gpu_ids(parameters.get("gpu_id")),
        "available_gpu_ids": gpus,
        "candidate_counts": counts,
        "batch_counts": batches,
        "active_gpu_leases": leases,
        "total_candidate_count": total_candidates,
        "reliable_candidate_count": reliable_count,
        "history_clean_reliable_candidate_count": history_clean_reliable_count,
        "history_duplicate_reliable_candidate_count": history_duplicate_reliable_count,
        "history_duplicate_reliable_candidates": reliable_history_duplicates,
        "recommender_next_action": recommender_next_action,
        "gpu_next_action": gpu_next_action,
        "next_action": next_action,
        "reason": reason,
    }


def command_inspect(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    summary = inspect_streaming(repo_root, output_root)
    write_status(output_root, summary)
    return summary


def command_next_action(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = resolve_repo_path(repo_root, args.output_root)
    summary = inspect_streaming(repo_root, output_root)
    parameters = load_parameters(output_root)
    branch = parameters.get("recommendation_branch")
    batch_size = int(parameters.get("mattersim_batch_size") or DEFAULT_MATTERSIM_BATCH_SIZE)
    parallelism = int(parameters.get("recommendation_parallelism") or DEFAULT_RECOMMENDER_PARALLELISM)
    active_recommenders = args.active_recommenders
    missing_recommenders = None
    if active_recommenders is not None:
        missing_recommenders = max(parallelism - max(active_recommenders, 0), 0)
    recommender_agents = [
        {
            "agent": "stage02_recommender",
            "stage": "stage02",
            "slot": index,
            "branch": branch,
            "run_mode": "continuous_recommendation_loop",
            "expected_write": "repeated register-candidate calls",
        }
        for index in range(1, (missing_recommenders if missing_recommenders is not None else parallelism) + 1)
    ]
    recommender_action: dict[str, Any] = {
        "next_action": "maintain_parallel_subagents",
        "agent": "stage02_recommender",
        "stage": "stage02",
        "desired_active_count": parallelism,
        "active_count_reported": active_recommenders,
        "spawn_missing_count": missing_recommenders,
        "agents_to_spawn": recommender_agents,
        "reason": (
            "Stage02 recommenders are long-running continuous producers. Start enough agents to reach "
            "the target active count, and replace only recommenders that exit, fail, or are explicitly stopped."
        ),
    }

    gpu_action_name = summary.get("gpu_next_action")
    gpu_action: dict[str, Any] | None = None
    if gpu_action_name == "claim_mattersim_batch":
        gpu_action = {
            "next_action": "claim_mattersim_batch",
            "branch": branch,
            "batch_size": batch_size,
            "available_gpu_ids": summary["available_gpu_ids"],
            "command_template": (
                "python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py "
                "claim-mattersim-batch --output-root <output_root> --gpu-id <single_gpu_id>"
            ),
        }
    elif gpu_action_name == "claim_sampling":
        gpu_action = {
            "next_action": "claim_sampling",
            "branch": branch,
            "available_gpu_ids": summary["available_gpu_ids"],
            "command_template": (
                "python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py "
                "claim-sampling --output-root <output_root> --gpu-id <gpu_id>"
            ),
        }
    concurrent_actions: list[dict[str, Any]] = [recommender_action]
    if gpu_action:
        concurrent_actions.append(gpu_action)
    action = {
        "next_action": "run_concurrent_actions",
        "concurrent_actions": concurrent_actions,
        "reason": (
            "Maintain Stage02 recommendation continuously while independently draining MatterGen/MatterSim queues."
        ),
    }
    write_status(output_root, summary)
    return {"summary": summary, "next_action": action}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Streaming OWS scheduler and SQLite registry.")
    parser.add_argument("--repo-root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    init_parser.add_argument("--knowledge-base-path", default="data/knowledge-base/knowledge_base_for_agent.json")
    init_parser.add_argument("--recommendation-branch", required=True, choices=sorted(VALID_BRANCHES))
    init_parser.add_argument("--execution-scope", default="full", choices=["full", "stage02_only"])
    init_parser.add_argument("--gpu-id", default="")
    init_parser.add_argument("--target-recommendation-count", type=int, default=None)
    init_parser.add_argument("--recommendation-parallelism", type=int, default=DEFAULT_RECOMMENDER_PARALLELISM)
    init_parser.add_argument("--mattersim-batch-size", type=int, default=DEFAULT_MATTERSIM_BATCH_SIZE)
    init_parser.add_argument("--resume", action="store_true")
    init_parser.set_defaults(func=command_init)

    register_parser = subparsers.add_parser("register-candidate")
    register_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    register_parser.add_argument("--branch", choices=sorted(VALID_BRANCHES), default=None)
    register_parser.add_argument("--payload-json", default="")
    register_parser.add_argument("--candidate-id", default="")
    register_parser.add_argument("--candidate-name", default="")
    register_parser.add_argument("--main-photocatalyst", default="")
    register_parser.add_argument("--main-photocatalyst-formula-note", default="")
    register_parser.add_argument("--laboratory-feasibility-decision", default="pass")
    register_parser.add_argument("--violated-laboratory-limitation-ids", default="none")
    register_parser.add_argument("--laboratory-feasibility-reason", default="")
    register_parser.add_argument("--reasoning-file", default="")
    register_parser.add_argument("--formula-key", default="")
    register_parser.add_argument("--reasoning-family-key", default="")
    register_parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    register_parser.add_argument("--allow-history-duplicate", action="store_true")
    register_parser.add_argument("--agent", default="")
    register_parser.set_defaults(func=command_register_candidate)

    claim_sampling_parser = subparsers.add_parser("claim-sampling")
    claim_sampling_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    claim_sampling_parser.add_argument("--candidate-id", default="")
    claim_sampling_parser.add_argument("--gpu-id", required=True)
    claim_sampling_parser.add_argument("--batch-size", type=int, default=None)
    claim_sampling_parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    claim_sampling_parser.set_defaults(func=command_claim_sampling)

    complete_sampling_parser = subparsers.add_parser("complete-sampling")
    complete_sampling_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    complete_sampling_parser.add_argument("--candidate-id", required=True)
    complete_sampling_parser.add_argument("--status", default="complete", choices=["complete", "failed"])
    complete_sampling_parser.add_argument("--failure-reason", default="")
    complete_sampling_parser.set_defaults(func=command_complete_sampling)

    claim_ms_parser = subparsers.add_parser("claim-mattersim-batch")
    claim_ms_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    claim_ms_parser.add_argument("--branch", choices=sorted(VALID_BRANCHES), default=None)
    claim_ms_parser.add_argument("--gpu-id", required=True)
    claim_ms_parser.add_argument("--batch-size", type=int, default=None)
    claim_ms_parser.add_argument("--batch-id", default="")
    claim_ms_parser.add_argument("--pid", default="")
    claim_ms_parser.add_argument("--allow-partial", action="store_true")
    claim_ms_parser.set_defaults(func=command_claim_mattersim_batch)

    complete_ms_parser = subparsers.add_parser("complete-mattersim-batch")
    complete_ms_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    complete_ms_parser.add_argument("--batch-id", required=True)
    complete_ms_parser.add_argument("--summary-path", default="")
    complete_ms_parser.add_argument("--status", default="complete", choices=["complete", "failed"])
    complete_ms_parser.add_argument("--failure-reason", default="")
    complete_ms_parser.set_defaults(func=command_complete_mattersim_batch)

    reuse_ms_parser = subparsers.add_parser("reuse-mattersim-results")
    reuse_ms_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    reuse_ms_parser.add_argument("--source-output-root", required=True)
    reuse_ms_parser.add_argument("--branch", choices=sorted(VALID_BRANCHES), default=None)
    reuse_ms_parser.add_argument("--candidate-id", action="append", default=[])
    reuse_ms_parser.add_argument("--batch-id", default="")
    reuse_ms_parser.add_argument("--require-existing-evidence", action="store_true")
    reuse_ms_parser.set_defaults(func=command_reuse_mattersim_results)

    release_gpu_parser = subparsers.add_parser("release-gpu")
    release_gpu_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    release_gpu_parser.add_argument("--gpu-id", required=True)
    release_gpu_parser.set_defaults(func=command_release_gpu)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    inspect_parser.set_defaults(func=command_inspect)

    next_parser = subparsers.add_parser("next-action")
    next_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    next_parser.add_argument(
        "--active-recommenders",
        type=int,
        default=None,
        help="Currently running stage02_recommender count; when provided, only missing slots are returned.",
    )
    next_parser.set_defaults(func=command_next_action)

    materialize_parser = subparsers.add_parser("materialize-handoffs")
    materialize_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    materialize_parser.set_defaults(
        func=lambda args: materialize_streaming_handoffs(
            Path(args.repo_root).resolve(),
            resolve_repo_path(Path(args.repo_root).resolve(), args.output_root),
        )
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
