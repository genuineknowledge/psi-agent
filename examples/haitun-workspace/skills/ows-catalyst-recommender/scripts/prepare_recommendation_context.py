from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RecommendationContextConfig:
    knowledge_base_path: Path
    laboratory_limitations_path: Path
    history_dir: Path
    stage05_summary_path: Path
    iteration_log_path: Path
    stage06_evaluation_path: Path
    stage07_nearest_component_path: Path
    stage07_novelty_assessment_path: Path
    stage02_failure_feedback_root: Path
    output_dir: Path
    stage_root: Path | None
    round_id: str | None
    max_records: int
    archive_current_round: bool


OBSOLETE_OUTPUT_FILES = [
    "READINESS_REPORT.md",
    "MECHANISM_GATE_REVIEW.md",
    "NOVELTY_AUDIT.md",
    "CANDIDATE_RATIONALES.md",
    "EXPERIMENT_PROPOSALS.md",
    "RISK_AND_LIMITATIONS.md",
]

ROUND_ARCHIVE_FILES = [
    "RECOMMENDATION_CONTEXT.json",
    "HYPOTHESIS_POOL.csv",
    "CANDIDATE_REVIEW.md",
    "RECOMMENDED_CANDIDATES.csv",
    "ZSCHEME_SYSTEMS.csv",
    "ZSCHEME_COMPONENT_CANDIDATES.csv",
    "RECOMMENDATION_ITERATION_LOG.md",
    "RECOMMENDATION_REPORT.md",
    "PRE_RECOMMENDATION_TRACE.md",
    "REASONING_CHAIN.md",
]

DEFAULT_HISTORY_DIR = Path("data/history")
DEFAULT_OUTPUT_ROOT = Path("ows")
DEFAULT_KNOWLEDGE_BASE_PATH = Path("data/knowledge-base/knowledge_base_for_agent.json")
DEFAULT_LABORATORY_LIMITATIONS_PATH = Path("data/laboratory-limitations/laboratory_limitations_for_agent.json")
DEFAULT_HISTORY_DIR = Path("data/history")
STAGE02_DIRNAME = "02-ows-catalyst-recommender"
SINGLE_STAGE05_DIRNAME = "05-mattersim-structure-evaluator"
STAGE06_DIRNAME = "06-zscheme-system-evaluator"
STAGE07_DIRNAME = "07-reference-novelty-comparison"
ENTRY_DIRNAME = "00-coscientist-ows-entry"
STAGE02_FAILURE_FEEDBACK_RELATIVE_DIR = Path("stage02-feedback") / "rounds"
STAGE09_STAGE10_FAILURE_FEEDBACK_NAME = "STAGE09_STAGE10_FAILURE_FEEDBACK.json"
POINTER_FILES = ("CURRENT_ROUND.json", "LATEST_SUCCESSFUL.json")
FORBIDDEN_STAGE02_GENERATOR_PATTERNS = (
    "write_stage02_*",
    "generate_stage02_*",
    "fill_stage02_*",
)

LABORATORY_FEASIBILITY_COLUMNS = (
    "laboratory_feasibility_decision",
    "violated_laboratory_limitation_ids",
    "laboratory_feasibility_reason",
)


def find_forbidden_stage02_generators() -> list[Path]:
    scripts_dir = Path(__file__).resolve().parent
    forbidden: list[Path] = []
    for pattern in FORBIDDEN_STAGE02_GENERATOR_PATTERNS:
        forbidden.extend(
            path for path in scripts_dir.glob(pattern) if path.is_file() and path.name != Path(__file__).name
        )
    return sorted(set(forbidden))


def assert_no_forbidden_stage02_generators() -> None:
    forbidden = find_forbidden_stage02_generators()
    if not forbidden:
        return
    paths = ", ".join(display_path(path) for path in forbidden)
    raise RuntimeError(
        "Forbidden Stage02 recommendation generator script(s) found: "
        f"{paths}. Delete these files and write Stage02 recommendation "
        "artifacts directly from the agent/model instead."
    )


class _TableData:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    @property
    def empty(self) -> bool:
        return not self.rows

    def __len__(self) -> int:
        return len(self.rows)

    def get(self, key: str, default: Any = None) -> Any:
        values = [row.get(key) for row in self.rows if key in row]
        if values:
            return values
        return default

    def head(self, count: int) -> _TableData:
        return _TableData(self.rows[:count])

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError(f"Unsupported orient: {orient}")
        return [dict(row) for row in self.rows]


def ensure_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        pass
    return str(resolved)


def read_pointer(stage_root: Path) -> dict[str, Any]:
    for pointer_name in POINTER_FILES:
        pointer_path = stage_root / pointer_name
        if not pointer_path.exists():
            continue
        try:
            data = json.loads(pointer_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def latest_artifact_from_children(stage_root: Path, container_name: str, artifact_name: str) -> Path | None:
    container = stage_root / container_name
    if not container.exists():
        return None
    candidates = [
        path / artifact_name for path in container.iterdir() if path.is_dir() and (path / artifact_name).exists()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def resolve_pointed_path(output_root: Path, value: str) -> Path:
    pointed = Path(value)
    if pointed.is_absolute():
        return pointed
    output_candidate = output_root / pointed
    if output_candidate.exists():
        return output_candidate
    return pointed


def resolve_artifact_path(path: Path, output_root: Path) -> Path:
    if path.exists():
        return path
    try:
        rel = path.relative_to(output_root)
    except ValueError:
        rel = path
    if len(rel.parts) < 2:
        return path

    stage_root = output_root / rel.parts[0]
    artifact_name = Path(*rel.parts[1:]).as_posix()
    if "/" in artifact_name:
        return path

    pointer = read_pointer(stage_root)
    artifacts = pointer.get("artifacts", {})
    pointed = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
    if isinstance(pointed, str):
        pointed_path = resolve_pointed_path(output_root, pointed)
        if pointed_path.exists():
            return pointed_path
    for key in ("round_dir", "run_dir"):
        pointed_dir = pointer.get(key)
        if isinstance(pointed_dir, str):
            pointed_path = resolve_pointed_path(output_root, pointed_dir) / artifact_name
            if pointed_path.exists():
                return pointed_path

    latest = latest_artifact_from_children(stage_root, "rounds", artifact_name)
    if latest is not None:
        return latest
    latest = latest_artifact_from_children(stage_root, "runs", artifact_name)
    if latest is not None:
        return latest
    return path


def round_sort_key(round_id: str) -> tuple[int, int, str]:
    if round_id.startswith("r") and round_id[1:].isdigit():
        return (0, int(round_id[1:]), round_id)
    return (1, 0, round_id)


def next_round_id(stage_root: Path) -> str:
    existing: set[str] = set()
    for container_name in ("rounds", "iteration-archive"):
        container = stage_root / container_name
        if not container.exists():
            continue
        existing.update(path.name for path in container.iterdir() if path.is_dir() and path.name.startswith("r"))
    max_round = 0
    for round_id in existing:
        if round_id.startswith("r") and round_id[1:].isdigit():
            max_round = max(max_round, int(round_id[1:]))
    if max_round:
        return f"r{max_round + 1}"
    if any((stage_root / name).exists() for name in ROUND_ARCHIVE_FILES):
        return "r1"
    return "r1"


def paths_equal(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def resolve_round_output_dir(
    output_dir: Path,
    output_root: Path,
    round_id: str | None,
) -> tuple[Path, Path | None, str | None]:
    normalized_default = output_root / STAGE02_DIRNAME
    if paths_equal(output_dir, normalized_default):
        effective_round_id = round_id or next_round_id(normalized_default)
        return normalized_default / "rounds" / effective_round_id, normalized_default, effective_round_id
    return output_dir, None, round_id


def write_round_manifest(stage_root: Path | None, output_dir: Path, round_id: str | None) -> None:
    artifact_paths = {
        file_name: display_path(output_dir / file_name)
        for file_name in ROUND_ARCHIVE_FILES
        if (output_dir / file_name).exists()
    }
    manifest = {
        "round_id": round_id,
        "round_dir": display_path(output_dir),
        "artifact_layout": "stage/rounds/round_id" if round_id else "explicit_output_dir",
        "updated_at": datetime.now(UTC).isoformat(),
        "artifacts": artifact_paths,
        "legacy_iteration_archive_supported": True,
    }
    (output_dir / "ROUND_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not stage_root or not round_id:
        return
    stage_root.mkdir(parents=True, exist_ok=True)
    for pointer_name in POINTER_FILES:
        (stage_root / pointer_name).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def load_pandas() -> Any:
    try:
        return importlib.import_module("pandas")
    except ModuleNotFoundError:
        return None


def read_csv_table(path: Path) -> _TableData:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return _TableData(list(csv.DictReader(handle)))


def read_table_if_present(path: Path) -> _TableData:
    if not path.exists():
        return _TableData()

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_table(path)
    if suffix in {".xlsx", ".xls"}:
        pd = load_pandas()
        if pd is None:
            raise ModuleNotFoundError(
                "pandas is required to read Excel inputs; provide CSV inputs or install pandas in the project "
                "environment."
            )
        return pd.read_excel(path)
    raise ValueError(f"Unsupported table format: {path}")


def read_json_if_present(path: Path) -> tuple[Any, str]:
    if not path.exists():
        return None, ""
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except json.JSONDecodeError as exc:
        return None, f"{path}: {exc}"


def raw_record_count(data: Any) -> int:
    if isinstance(data, (list, dict)):
        return len(data)
    return 0


def validate_laboratory_limitations(data: Any, path: Path) -> str:
    if not isinstance(data, list):
        return f"{path}: expected a JSON array of laboratory-limitation records"

    errors: list[str] = []
    for index, record in enumerate(data, start=1):
        if not isinstance(record, dict):
            errors.append(f"record {index} is not an object")
            continue
        for field_name in ("limitation_id", "limitation"):
            value = record.get(field_name)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"record {index} missing non-empty {field_name}")
    if errors:
        return f"{path}: " + "; ".join(errors)
    return ""


def read_excel_sheets_if_present(path: Path, max_records: int) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    pd = load_pandas()
    if pd is None:
        raise ModuleNotFoundError(
            "pandas is required to read Stage07 Excel inputs; install pandas in the project environment."
        )
    sheets = pd.read_excel(path, sheet_name=None)
    return {sheet_name: frame.head(max_records).to_dict(orient="records") for sheet_name, frame in sheets.items()}


def read_text_excerpt_if_present(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def read_history_cumulative_routes(history_dir: Path) -> dict[str, Any]:
    if not history_dir.exists():
        return {
            "available": False,
            "history_dir": str(history_dir),
            "routes": [],
        }

    routes = []
    for route_file in sorted(history_dir.glob("*_CUMULATIVE_SYNTHESIS_ROUTE.md")):
        route_text = read_text_excerpt_if_present(route_file, limit=8000)
        if route_text:
            routes.append(
                {
                    "batch_id": route_file.stem.replace("_CUMULATIVE_SYNTHESIS_ROUTE", ""),
                    "file_path": str(route_file),
                    "content": route_text,
                }
            )

    return {
        "available": bool(routes),
        "history_dir": str(history_dir),
        "route_count": len(routes),
        "routes": routes,
        "usage_policy": (
            "Use historical success routes as a hard formula-exclusion source. Do not recommend any "
            "candidate whose normalized reduced formula is already present in these successful routes. "
            "The streaming scheduler also rejects such candidates at register-candidate time with "
            "status=duplicate_history_formula."
        ),
    }


def read_stage09_stage10_failure_feedback(
    feedback_rounds_root: Path,
    current_round_id: str | None,
    max_records: int,
) -> dict[str, Any]:
    if not feedback_rounds_root.exists():
        return {
            "available": False,
            "feedback_rounds_root": str(feedback_rounds_root),
            "files": [],
            "failure_count": 0,
            "records": [],
        }

    current_sort_key = round_sort_key(current_round_id) if current_round_id else None
    files: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for feedback_path in sorted(
        feedback_rounds_root.glob(f"*/{STAGE09_STAGE10_FAILURE_FEEDBACK_NAME}"),
        key=lambda path: round_sort_key(path.parent.name),
    ):
        round_id = feedback_path.parent.name
        if current_sort_key is not None and round_sort_key(round_id) >= current_sort_key:
            continue
        payload, error = read_json_if_present(feedback_path)
        if error or not isinstance(payload, dict):
            files.append(
                {
                    "round_id": round_id,
                    "path": display_path(feedback_path),
                    "error": error or "payload is not a JSON object",
                }
            )
            continue
        feedback_records = payload.get("feedback_records")
        if not isinstance(feedback_records, list):
            feedback_records = payload.get("failures")
        if not isinstance(feedback_records, list) or not feedback_records:
            continue
        files.append(
            {
                "round_id": round_id,
                "path": display_path(feedback_path),
                "feedback_record_count": len(feedback_records),
                "failure_count": payload.get("failure_count", 0),
                "synthesis_feasibility_insufficient_information_count": payload.get(
                    "synthesis_feasibility_insufficient_information_count", 0
                ),
            }
        )
        for feedback_record in feedback_records:
            if not isinstance(feedback_record, dict):
                continue
            item = dict(feedback_record)
            item.setdefault("round_id", round_id)
            records.append(item)
            if len(records) >= max_records:
                break
        if len(records) >= max_records:
            break

    return {
        "available": bool(records),
        "feedback_rounds_root": str(feedback_rounds_root),
        "files": files,
        "feedback_record_count": len(records),
        "failure_count": sum(1 for record in records if record.get("is_failure") is not False),
        "synthesis_feasibility_insufficient_information_count": sum(
            1 for record in records if record.get("feedback_type") == "synthesis_feasibility_insufficient_information"
        ),
        "records": records,
        "usage_policy": (
            "Use explicit Stage09 synthesis-feasibility failures, Stage10 catalytic-performance failures, "
            "and Stage09 synthesis-feasibility information-insufficient records. Treat information-insufficient "
            "records as unresolved route uncertainty, not as failed catalysts. Do not infer feedback from absent "
            "files, passing records, or chemical-safety-only conclusions."
        ),
    }


def create_empty_artifact(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def remove_obsolete_outputs(output_dir: Path) -> None:
    for file_name in OBSOLETE_OUTPUT_FILES:
        path = output_dir / file_name
        if path.exists():
            path.unlink()


def write_missing_artifacts_note(archive_dir: Path, missing: list[str]) -> None:
    if not missing:
        return
    lines = [
        "# Missing Artifacts",
        "",
        "The following recommendation artifacts were absent when this round archive was created:",
        "",
    ]
    lines.extend(f"- `{name}`" for name in missing)
    (archive_dir / "MISSING_ARTIFACTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive_current_round(stage_root: Path, round_id: str | None = None) -> Path:
    round_id = round_id or next_round_id(stage_root)
    archive_dir = stage_root / "rounds" / round_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for file_name in ROUND_ARCHIVE_FILES:
        source = stage_root / file_name
        if source.exists():
            target = archive_dir / file_name
            target.write_bytes(source.read_bytes())
        else:
            missing.append(file_name)
    write_missing_artifacts_note(archive_dir, missing)
    return archive_dir


def write_empty_stage02_artifacts(output_dir: Path) -> None:
    remove_obsolete_outputs(output_dir)
    for file_name in (
        "HYPOTHESIS_POOL.csv",
        "RECOMMENDED_CANDIDATES.csv",
        "ZSCHEME_SYSTEMS.csv",
        "ZSCHEME_COMPONENT_CANDIDATES.csv",
        "CANDIDATE_REVIEW.md",
        "RECOMMENDATION_REPORT.md",
        "PRE_RECOMMENDATION_TRACE.md",
        "REASONING_CHAIN.md",
        "RECOMMENDATION_ITERATION_LOG.md",
    ):
        create_empty_artifact(output_dir / file_name)


def count_rows_if_present(path: Path) -> int:
    if not path.exists():
        return 0
    return len(read_csv_table(path))


def csv_columns_if_present(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or [])


def row_label(row: dict[str, Any], id_columns: tuple[str, ...], row_number: int) -> str:
    for column_name in id_columns:
        value = str(row.get(column_name) or "").strip()
        if value:
            return value
    return f"row_{row_number}"


def missing_required_columns(path: Path, required_columns: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return []
    columns = set(csv_columns_if_present(path))
    return [column_name for column_name in required_columns if column_name not in columns]


def laboratory_feasibility_warnings(path: Path, id_columns: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return []
    warnings: list[str] = []
    for row_number, row in enumerate(read_csv_table(path).rows, start=2):
        label = row_label(row, id_columns, row_number)
        decision = str(row.get("laboratory_feasibility_decision") or "").strip().lower()
        violated_ids = str(row.get("violated_laboratory_limitation_ids") or "").strip().lower()
        reason = str(row.get("laboratory_feasibility_reason") or "").strip()
        if decision != "pass":
            warnings.append(f"{display_path(path)}:{label}:laboratory_feasibility_decision_not_pass")
        if violated_ids not in {"", "none"}:
            warnings.append(f"{display_path(path)}:{label}:violated_laboratory_limitation_ids_not_empty")
        if not reason:
            warnings.append(f"{display_path(path)}:{label}:missing_laboratory_feasibility_reason")
    return warnings


def truthy(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized not in {"false", "0", "no", "n", "非", "否"}


def zscheme_component_coverage_issues(systems_path: Path, components_path: Path) -> list[str]:
    system_rows = read_csv_table(systems_path).rows if systems_path.exists() else []
    if not system_rows:
        return []
    component_rows = read_csv_table(components_path).rows if components_path.exists() else []
    component_ids = {
        str(row.get("candidate_id") or "").strip()
        for row in component_rows
        if str(row.get("candidate_id") or "").strip()
    }
    components_by_parent: dict[str, set[str]] = {}
    for row in component_rows:
        parent_id = str(row.get("parent_zscheme_id") or "").strip()
        component_id = str(row.get("candidate_id") or "").strip()
        if parent_id and component_id:
            components_by_parent.setdefault(parent_id, set()).add(component_id)

    issues: list[str] = []
    if not component_rows:
        return [f"{display_path(components_path)}:missing_zscheme_component_rows"]

    for row_number, system in enumerate(system_rows, start=2):
        zscheme_id = str(system.get("zscheme_id") or "").strip()
        label = zscheme_id or f"row_{row_number}"
        her_component_id = str(system.get("her_component_id") or "").strip()
        oer_component_id = str(system.get("oer_component_id") or "").strip()
        if not zscheme_id or not her_component_id or not oer_component_id:
            issues.append(f"{display_path(systems_path)}:{label}:missing_zscheme_or_component_id")
            continue
        missing_components = [
            component_id for component_id in (her_component_id, oer_component_id) if component_id not in component_ids
        ]
        if missing_components:
            issues.append(f"{display_path(components_path)}:{label}:missing_components:{','.join(missing_components)}")
        parent_components = components_by_parent.get(zscheme_id, set())
        if parent_components and not {her_component_id, oer_component_id}.issubset(parent_components):
            issues.append(f"{display_path(components_path)}:{label}:parent_zscheme_component_coverage_incomplete")
    return issues


def build_stage02_compliance(output_dir: Path) -> dict[str, Any]:
    single_path = output_dir / "RECOMMENDED_CANDIDATES.csv"
    systems_path = output_dir / "ZSCHEME_SYSTEMS.csv"
    components_path = output_dir / "ZSCHEME_COMPONENT_CANDIDATES.csv"
    single_count = count_rows_if_present(single_path)
    zscheme_system_count = count_rows_if_present(systems_path)
    zscheme_component_count = count_rows_if_present(components_path)
    recommendation_unit_count = single_count + zscheme_system_count
    paths_to_check = [single_path, systems_path, components_path]
    missing_laboratory_feasibility_columns = {
        str(path): missing_columns
        for path in paths_to_check
        if count_rows_if_present(path) > 0
        and (missing_columns := missing_required_columns(path, LABORATORY_FEASIBILITY_COLUMNS))
    }
    blockers: list[str] = []
    laboratory_feasibility_warning_rows = (
        laboratory_feasibility_warnings(single_path, ("candidate_id",))
        + laboratory_feasibility_warnings(systems_path, ("zscheme_id",))
        + laboratory_feasibility_warnings(components_path, ("candidate_id", "parent_zscheme_id"))
    )
    zscheme_component_coverage_warning_rows = zscheme_component_coverage_issues(systems_path, components_path)
    if missing_laboratory_feasibility_columns:
        blockers.append("missing_laboratory_feasibility_columns")
    if laboratory_feasibility_warning_rows:
        blockers.append("laboratory_feasibility_review_incomplete_or_failed")
    if zscheme_component_coverage_warning_rows:
        blockers.append("zscheme_component_coverage_incomplete")
    return {
        "single_candidate_count": single_count,
        "zscheme_system_count": zscheme_system_count,
        "zscheme_component_count": zscheme_component_count,
        "recommendation_unit_count": recommendation_unit_count,
        "knowledge_similarity_review_enabled": False,
        "missing_laboratory_feasibility_columns": missing_laboratory_feasibility_columns,
        "laboratory_feasibility_warning_rows": laboratory_feasibility_warning_rows,
        "zscheme_component_coverage_warning_rows": zscheme_component_coverage_warning_rows,
        "compliance_blockers": blockers,
        "compliant": not blockers,
    }


def prepare_recommendation_context(config: RecommendationContextConfig) -> dict[str, Any]:
    output_dir = ensure_output_dir(config.output_dir)
    archived_round_path = ""
    if config.archive_current_round and config.stage_root is not None:
        archived_round_path = str(archive_current_round(config.stage_root))
    raw_knowledge_base, knowledge_base_error = read_json_if_present(config.knowledge_base_path)
    raw_laboratory_limitations, laboratory_limitations_error = read_json_if_present(config.laboratory_limitations_path)
    if not laboratory_limitations_error and config.laboratory_limitations_path.exists():
        laboratory_limitations_error = validate_laboratory_limitations(
            raw_laboratory_limitations,
            config.laboratory_limitations_path,
        )
    missing_inputs = [
        str(path) for path in (config.knowledge_base_path, config.laboratory_limitations_path) if not path.exists()
    ]
    ready = (
        not missing_inputs
        and not knowledge_base_error
        and not laboratory_limitations_error
        and raw_record_count(raw_knowledge_base) > 0
    )

    stage05_summary = read_table_if_present(config.stage05_summary_path)
    stage05_feedback_records = (
        stage05_summary.head(config.max_records).to_dict(orient="records") if not stage05_summary.empty else []
    )
    stage06_evaluation = read_table_if_present(config.stage06_evaluation_path)
    stage06_feedback_records = (
        stage06_evaluation.head(config.max_records).to_dict(orient="records") if not stage06_evaluation.empty else []
    )
    stage07_nearest_component_sheets = read_excel_sheets_if_present(
        config.stage07_nearest_component_path, config.max_records
    )
    stage07_novelty_assessment_sheets = read_excel_sheets_if_present(
        config.stage07_novelty_assessment_path, config.max_records
    )
    history_data = read_history_cumulative_routes(config.history_dir)
    stage09_stage10_failure_feedback = read_stage09_stage10_failure_feedback(
        config.stage02_failure_feedback_root,
        config.round_id,
        config.max_records,
    )
    stage02_compliance = build_stage02_compliance(output_dir)
    context = {
        "ready_for_recommendation": ready,
        "missing_inputs": missing_inputs,
        "input_mode": "raw_knowledge_base",
        "input_artifact_paths": {
            "knowledge_base_path": display_path(config.knowledge_base_path),
            "laboratory_limitations_path": display_path(config.laboratory_limitations_path),
            "history_dir": display_path(config.history_dir),
            "stage05_summary_path": display_path(config.stage05_summary_path),
            "stage06_evaluation_path": display_path(config.stage06_evaluation_path),
            "stage07_nearest_component_path": display_path(config.stage07_nearest_component_path),
            "stage07_novelty_assessment_path": display_path(config.stage07_novelty_assessment_path),
            "stage02_failure_feedback_root": display_path(config.stage02_failure_feedback_root),
        },
        "knowledge_context_mode": (
            "Stage02 uses the raw knowledge-base JSON directly. The helper does not rank, filter, summarize, "
            "classify, or otherwise derive Stage01-style evidence artifacts from the knowledge base."
        ),
        "record_limits": {
            "feedback_max_records": config.max_records,
        },
        "knowledge_base_error": knowledge_base_error,
        "knowledge_base_record_count": raw_record_count(raw_knowledge_base),
        "knowledge_base_records": raw_knowledge_base,
        "laboratory_limitations_error": laboratory_limitations_error,
        "laboratory_limitations_record_count": raw_record_count(raw_laboratory_limitations),
        "laboratory_limitations_records": (
            raw_laboratory_limitations if raw_laboratory_limitations is not None else []
        ),
        "laboratory_limitations_policy": {
            "constraint_level": "hard_stage02_recommendation_constraint",
            "source_records": "laboratory_limitations_records",
            "application_scope": [
                "single_photocatalyst_recommendations",
                "zscheme_component_recommendations",
                "zscheme_system_recommendations",
            ],
            "rule": (
                "Do not recommend a candidate when its composition, necessary precursor/reagent, atmosphere, "
                "equipment requirement, or expected synthesis route violates any raw laboratory-limitation record. "
                "Apply ambiguous limitations conservatively."
            ),
        },
        "stage05_feedback_records": stage05_feedback_records,
        "stage06_feedback_records": stage06_feedback_records,
        "stage07_nearest_component_sheets": stage07_nearest_component_sheets,
        "stage07_novelty_assessment_sheets": stage07_novelty_assessment_sheets,
        "stage09_stage10_failure_feedback": stage09_stage10_failure_feedback,
        "historical_success_routes": history_data,
        "stage02_current_recommendation_compliance": stage02_compliance,
        "iteration_log_excerpt": read_text_excerpt_if_present(config.iteration_log_path),
        "recommendation_iteration_log_excerpt": read_text_excerpt_if_present(
            output_dir / "RECOMMENDATION_ITERATION_LOG.md"
        ),
        "readiness_summary": {
            "ready_for_recommendation": ready,
            "knowledge_base_records": raw_record_count(raw_knowledge_base),
            "laboratory_limitations_records": raw_record_count(raw_laboratory_limitations),
            "stage05_feedback_rows": len(stage05_summary),
            "stage06_feedback_rows": len(stage06_evaluation),
            "stage07_nearest_component_available": bool(stage07_nearest_component_sheets),
            "stage07_novelty_assessment_available": bool(stage07_novelty_assessment_sheets),
            "stage09_stage10_failure_feedback_available": bool(stage09_stage10_failure_feedback["available"]),
            "stage09_stage10_feedback_records": stage09_stage10_failure_feedback["feedback_record_count"],
            "stage09_stage10_failure_feedback_records": stage09_stage10_failure_feedback["failure_count"],
            "stage09_synthesis_feasibility_insufficient_information_records": stage09_stage10_failure_feedback[
                "synthesis_feasibility_insufficient_information_count"
            ],
            "stage02_current_recommendation_compliant": stage02_compliance["compliant"],
            "stage02_current_recommendation_units": stage02_compliance["recommendation_unit_count"],
            "stage02_compliance_blockers": stage02_compliance["compliance_blockers"],
            "knowledge_similarity_review_enabled": False,
            "missing_inputs": missing_inputs,
        },
    }
    (output_dir / "RECOMMENDATION_CONTEXT.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    write_empty_stage02_artifacts(output_dir)
    write_round_manifest(config.stage_root, output_dir, config.round_id)
    return {
        "status": "ready" if ready else "blocked",
        "output_dir": str(output_dir),
        "round_id": config.round_id,
        "missing_inputs": missing_inputs,
        "knowledge_base_record_count": raw_record_count(raw_knowledge_base),
        "knowledge_base_error": knowledge_base_error,
        "laboratory_limitations_record_count": raw_record_count(raw_laboratory_limitations),
        "laboratory_limitations_error": laboratory_limitations_error,
        "recommendation_iteration_log_path": str(output_dir / "RECOMMENDATION_ITERATION_LOG.md"),
        "archived_round_path": archived_round_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare compact recommendation context under the configured output root."
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--knowledge-base-path", default=str(DEFAULT_KNOWLEDGE_BASE_PATH))
    parser.add_argument("--laboratory-limitations-path", default=str(DEFAULT_LABORATORY_LIMITATIONS_PATH))
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument(
        "--stage05-summary-path",
        default=None,
    )
    parser.add_argument(
        "--iteration-log-path",
        default=None,
    )
    parser.add_argument(
        "--stage06-evaluation-path",
        default=None,
    )
    parser.add_argument(
        "--stage07-nearest-component-path",
        default=None,
    )
    parser.add_argument(
        "--stage07-novelty-assessment-path",
        default=None,
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--round-id",
        default=None,
        help="Round identifier used under <output_root>/02-ows-catalyst-recommender/rounds/<round-id>.",
    )
    parser.add_argument("--max-records", type=int, default=1000)
    parser.add_argument(
        "--archive-current-round",
        action="store_true",
        help="Archive current top-level recommendation artifacts under rounds/rN before writing context.",
    )
    return parser.parse_args()


def main() -> None:
    assert_no_forbidden_stage02_generators()
    args = parse_args()
    output_root = Path(args.output_root)
    stage05_root = output_root / SINGLE_STAGE05_DIRNAME
    stage06_root = output_root / STAGE06_DIRNAME
    stage07_root = output_root / STAGE07_DIRNAME
    stage02_failure_feedback_root = output_root / ENTRY_DIRNAME / STAGE02_FAILURE_FEEDBACK_RELATIVE_DIR
    requested_output_dir = Path(args.output_dir) if args.output_dir else output_root / STAGE02_DIRNAME
    output_dir, stage_root, round_id = resolve_round_output_dir(requested_output_dir, output_root, args.round_id)
    knowledge_base_path = Path(args.knowledge_base_path)
    laboratory_limitations_path = Path(args.laboratory_limitations_path)
    history_dir = Path(args.history_dir)
    stage05_summary_path = (
        Path(args.stage05_summary_path)
        if args.stage05_summary_path
        else (stage05_root / "STRUCTURE_EVALUATION_SUMMARY.csv")
    )
    iteration_log_path = (
        Path(args.iteration_log_path) if args.iteration_log_path else (stage05_root / "ITERATION_LOG.md")
    )
    stage06_evaluation_path = (
        Path(args.stage06_evaluation_path)
        if args.stage06_evaluation_path
        else (stage06_root / "ZSCHEME_SYSTEM_EVALUATION.csv")
    )
    stage07_nearest_component_path = (
        Path(args.stage07_nearest_component_path)
        if args.stage07_nearest_component_path
        else stage07_root / "NEAREST_COMPONENT_COMPARISON.xlsx"
    )
    stage07_novelty_assessment_path = (
        Path(args.stage07_novelty_assessment_path)
        if args.stage07_novelty_assessment_path
        else stage07_root / "NOVELTY_ASSESSMENT.xlsx"
    )
    config = RecommendationContextConfig(
        knowledge_base_path=resolve_artifact_path(knowledge_base_path, output_root),
        laboratory_limitations_path=resolve_artifact_path(laboratory_limitations_path, output_root),
        history_dir=resolve_artifact_path(history_dir, output_root),
        stage05_summary_path=resolve_artifact_path(stage05_summary_path, output_root),
        iteration_log_path=resolve_artifact_path(iteration_log_path, output_root),
        stage06_evaluation_path=resolve_artifact_path(stage06_evaluation_path, output_root),
        stage07_nearest_component_path=resolve_artifact_path(stage07_nearest_component_path, output_root),
        stage07_novelty_assessment_path=resolve_artifact_path(stage07_novelty_assessment_path, output_root),
        stage02_failure_feedback_root=stage02_failure_feedback_root,
        output_dir=output_dir,
        stage_root=stage_root,
        round_id=round_id,
        max_records=args.max_records,
        archive_current_round=args.archive_current_round,
    )
    result = prepare_recommendation_context(config)
    sys.stdout.write(f"{json.dumps(result, indent=2)}\n")


if __name__ == "__main__":
    main()
