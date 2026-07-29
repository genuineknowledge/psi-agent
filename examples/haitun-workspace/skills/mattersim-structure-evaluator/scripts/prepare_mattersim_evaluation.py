from __future__ import annotations

import argparse
import csv
import importlib
import io
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROUTE_CANDIDATE_CONCRETIZATION = "return_to_02_candidate_concretization"
ROUTE_MECHANISM_GATE_REVIEW = "return_to_02_mechanism_gate_review"
ROUTE_NOVELTY_AUDIT = "return_to_02_novelty_audit"
ROUTE_SAMPLING = "return_to_04_sampling"
ROUTE_EXPERIMENTAL_VALIDATION = "proceed_to_experimental_validation"
FILTER_DUPLICATE_STRUCTURE = "filtered_lower_stability_duplicate_structure"

OBSOLETE_OUTPUT_FILES = [
    "EVALUATION_READINESS.md",
    "MATTERSIM_EVALUATION_COMMANDS.md",
    "OPTIMIZATION_ROUTING.md",
    "EVALUATION_MANIFEST.md",
]

PLAN_COLUMNS = [
    "candidate_id",
    "sampling_status",
    "upstream_return_step",
    "structures_path",
    "evaluation_dir",
    "metrics_path",
    "detailed_metrics_path",
    "relaxed_structures_path",
    "effective_reference_dataset",
    "reference_dataset_path",
    "sampling_artifact_status",
    "metrics_status",
    "evaluation_command",
]

COMBINED_PLAN_COLUMNS = [
    "combined_structures_path",
    "combined_manifest_path",
    "combined_metrics_path",
    "combined_detailed_metrics_path",
    "combined_relaxed_structures_path",
    "effective_reference_dataset",
    "reference_dataset_path",
    "combined_structure_count",
    "candidate_count",
    "evaluation_command",
]

MANIFEST_COLUMNS = [
    "global_structure_index",
    "candidate_id",
    "local_structure_index",
    "source_structures_path",
    "candidate_detailed_metrics_path",
    "candidate_relaxed_structures_path",
]

SUMMARY_COLUMNS = [
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

DEFAULT_MATTERGEN_HOME = "mattergen"
DEFAULT_OUTPUT_ROOT = Path("ows")
STAGE02_DIRNAME = "02-ows-catalyst-recommender"
SINGLE_STAGE04_DIRNAME = "04-mattergen-structure-sampler"
SINGLE_STAGE05_DIRNAME = "05-mattersim-structure-evaluator"
ZSCHEME_STAGE04_DIRNAME = "04-zscheme-mattergen-structure-sampler"
ZSCHEME_STAGE05_DIRNAME = "05-zscheme-mattersim-structure-evaluator"
STAGE08_DIRNAME = "08-round-parallel-synthesis-advisor"
SYNTHESIS_INPUT_SUMMARY_NAME = "SYNTHESIS_INPUT_SUMMARY.json"
SYNTHESIS_ROUTE_INDEX_NAME = "ROUND_PARALLEL_SYNTHESIS_INDEX.json"
ROUND_POINTER_NAMES = ("CURRENT_ROUND.json", "LATEST_SUCCESSFUL.json")
MATTERGEN_HOME_ENV = "MATTERGEN_HOME"
MATTERGEN_EVALUATE_BIN_ENV = "MATTERGEN_EVALUATE_BIN"
MATTERSIM_MODEL_PATH_ENV = "MATTERSIM_MODEL_PATH"
MATTERGEN_TRI_REFERENCE_PATH_ENV = "MATTERGEN_TRI_REFERENCE_PATH"
GPU_ID_PATTERN = re.compile(r"^[0-9]+(?:(?:\s*,\s*|\s+)[0-9]+)*$")
GPU_ID_TOKEN_PATTERN = re.compile(r"[0-9]+")
THREAD_LIMIT_ENV = "OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1"
SPACE_GROUP_SYMPREC = 0.1
SPACE_GROUP_ANGLE_TOLERANCE = 5.0


@dataclass(frozen=True)
class EvaluationConfig:
    repo_root: Path
    output_root: Path
    sampling_plan_path: Path
    samples_root: Path
    output_dir: Path
    stage_root: Path | None
    round_id: str | None
    evaluator_path: Path
    potential_load_path: Path
    tri_reference_path: Path
    reference_dataset_path: Path | None
    device: str
    gpu_id: str
    structure_matcher: str
    energy_correction_scheme: str


@dataclass(frozen=True)
class CandidatePlan:
    candidate_id: str
    sampling_status: str
    upstream_return_step: str
    structures_path: Path
    evaluation_dir: Path
    metrics_path: Path
    detailed_metrics_path: Path
    relaxed_structures_path: Path
    effective_reference_dataset: str
    reference_dataset_path: Path | None
    sampling_artifact_status: str
    metrics_status: str
    evaluation_command: str


@dataclass(frozen=True)
class CombinedEvaluation:
    evaluation_dir: Path
    structures_path: Path
    manifest_path: Path
    metrics_path: Path
    detailed_metrics_path: Path
    relaxed_structures_path: Path
    effective_reference_dataset: str
    reference_dataset_path: Path | None
    evaluation_command: str


def default_mattergen_home() -> Path:
    return Path(os.environ.get(MATTERGEN_HOME_ENV, DEFAULT_MATTERGEN_HOME))


def default_evaluator_path() -> Path:
    configured = os.environ.get(MATTERGEN_EVALUATE_BIN_ENV)
    if configured:
        return Path(configured)
    return default_mattergen_home() / ".venv" / "bin" / "mattergen-evaluate"


def default_mattersim_model_path() -> Path:
    configured = os.environ.get(MATTERSIM_MODEL_PATH_ENV)
    if configured:
        return Path(configured)
    return default_mattergen_home() / "mattersim" / "mattersim-v1.0.0-5M.pth"


def default_tri_reference_path() -> Path:
    configured = os.environ.get(MATTERGEN_TRI_REFERENCE_PATH_ENV)
    if configured:
        return Path(configured)
    return default_mattergen_home() / "data-release" / "reference_TRI2024correction.gz"


def relpath(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_under_root(repo_root: Path, path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return repo_root / candidate


def display_ows_path(path: Path, repo_root: Path | None = None) -> str:
    resolved = path.resolve()
    if repo_root is not None:
        try:
            return resolved.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            pass
    if "ows" in resolved.parts:
        index = resolved.parts.index("ows")
        return str(Path(*resolved.parts[index:]))
    return str(path)


def read_json_if_present(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def pointer_round_id(stage_root: Path) -> str | None:
    for pointer_name in ROUND_POINTER_NAMES:
        pointer = read_json_if_present(stage_root / pointer_name)
        round_id = pointer.get("round_id")
        if isinstance(round_id, str) and round_id:
            return round_id
    return None


def next_timestamp_round_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")


def paths_equal(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def resolve_pointed_path(output_root: Path, repo_root: Path, value: str) -> Path:
    pointed = Path(value)
    if pointed.is_absolute():
        return pointed
    output_candidate = output_root / pointed
    if output_candidate.exists():
        return output_candidate
    return repo_root / pointed


def resolve_artifact_path(path: Path, output_root: Path, repo_root: Path) -> Path:
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
    for pointer_name in (*ROUND_POINTER_NAMES, "CURRENT_RUN.json"):
        pointer = read_json_if_present(stage_root / pointer_name)
        artifacts = pointer.get("artifacts", {})
        if isinstance(artifacts, dict):
            pointed = artifacts.get(artifact_name)
            if isinstance(pointed, str):
                pointed_path = resolve_pointed_path(output_root, repo_root, pointed)
                if pointed_path.exists():
                    return pointed_path
        for key in ("round_dir", "run_dir"):
            pointed_dir = pointer.get(key)
            if isinstance(pointed_dir, str):
                pointed_path = resolve_pointed_path(output_root, repo_root, pointed_dir) / artifact_name
                if pointed_path.exists():
                    return pointed_path
    for container_name in ("rounds", "runs"):
        container = stage_root / container_name
        if not container.exists():
            continue
        candidates = [
            child / artifact_name
            for child in container.iterdir()
            if child.is_dir() and (child / artifact_name).exists()
        ]
        if candidates:
            return max(candidates, key=lambda item: item.stat().st_mtime)
    return path


def single_stage04_dir(output_root: Path) -> Path:
    return output_root / SINGLE_STAGE04_DIRNAME


def zscheme_stage04_dir(output_root: Path) -> Path:
    return output_root / ZSCHEME_STAGE04_DIRNAME


def single_stage05_dir(output_root: Path) -> Path:
    return output_root / SINGLE_STAGE05_DIRNAME


def zscheme_stage05_dir(output_root: Path) -> Path:
    return output_root / ZSCHEME_STAGE05_DIRNAME


def is_default_stage05_output(path: Path, output_root: Path) -> bool:
    return any(
        paths_equal(path, stage_dir)
        for stage_dir in {single_stage05_dir(output_root), zscheme_stage05_dir(output_root)}
    )


def matching_stage04_root(output_dir: Path, sampling_plan_path: Path, output_root: Path) -> Path:
    if paths_equal(output_dir, zscheme_stage05_dir(output_root)) or "zscheme" in sampling_plan_path.as_posix().lower():
        return zscheme_stage04_dir(output_root)
    return single_stage04_dir(output_root)


def resolve_round_paths(
    repo_root: Path,
    output_root: Path,
    output_dir_arg: str | None,
    sampling_plan_arg: str | None,
    samples_root_arg: str | None,
    round_id: str | None,
) -> tuple[Path, Path | None, str | None, Path, Path]:
    output_dir = Path(output_dir_arg) if output_dir_arg else single_stage05_dir(output_root)
    sampling_plan = (
        Path(sampling_plan_arg)
        if sampling_plan_arg
        else single_stage04_dir(output_root) / "STRUCTURE_SAMPLING_PLAN.csv"
    )
    samples_root = Path(samples_root_arg) if samples_root_arg else single_stage04_dir(output_root) / "samples"
    if is_default_stage05_output(output_dir, output_root):
        stage_root = output_dir
        stage04_root = matching_stage04_root(output_dir, sampling_plan, output_root)
        default_single_plan = single_stage04_dir(output_root) / "STRUCTURE_SAMPLING_PLAN.csv"
        default_single_samples = single_stage04_dir(output_root) / "samples"
        if paths_equal(output_dir, zscheme_stage05_dir(output_root)) and paths_equal(
            sampling_plan, default_single_plan
        ):
            sampling_plan = zscheme_stage04_dir(output_root) / "STRUCTURE_SAMPLING_PLAN.csv"
        if paths_equal(output_dir, zscheme_stage05_dir(output_root)) and paths_equal(
            samples_root, default_single_samples
        ):
            samples_root = zscheme_stage04_dir(output_root) / "samples"
        stage04_root = matching_stage04_root(output_dir, sampling_plan, output_root)
        effective_round_id = round_id or pointer_round_id(stage04_root) or next_timestamp_round_id()
        effective_output_dir = stage_root / "rounds" / effective_round_id
        if paths_equal(sampling_plan, stage04_root / "STRUCTURE_SAMPLING_PLAN.csv"):
            sampling_plan = stage04_root / "rounds" / effective_round_id / "STRUCTURE_SAMPLING_PLAN.csv"
        if paths_equal(samples_root, stage04_root / "samples"):
            samples_root = stage04_root / "rounds" / effective_round_id / "samples"
        return (
            resolve_under_root(repo_root, effective_output_dir),
            resolve_under_root(repo_root, stage_root),
            effective_round_id,
            resolve_under_root(repo_root, resolve_artifact_path(sampling_plan, output_root, repo_root)),
            resolve_under_root(repo_root, samples_root),
        )
    return (
        resolve_under_root(repo_root, output_dir),
        None,
        round_id,
        resolve_under_root(repo_root, resolve_artifact_path(sampling_plan, output_root, repo_root)),
        resolve_under_root(repo_root, samples_root),
    )


def normalize_gpu_ids(gpu_id: str) -> str:
    candidate = gpu_id.strip()
    if not GPU_ID_PATTERN.fullmatch(candidate):
        return ""
    return ",".join(GPU_ID_TOKEN_PATTERN.findall(candidate))


def gpu_id_is_safe(gpu_id: str) -> bool:
    return bool(normalize_gpu_ids(gpu_id))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: stringify(row.get(column, "")) for column in columns})


def write_csv_if_changed(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> bool:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: stringify(row.get(column, "")) for column in columns})
    text = buffer.getvalue()
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def remove_obsolete_outputs(output_dir: Path) -> None:
    for file_name in OBSOLETE_OUTPUT_FILES:
        path = output_dir / file_name
        if path.exists():
            path.unlink()


def remove_legacy_candidate_metrics(output_dir: Path) -> None:
    evaluations_dir = output_dir / "evaluations"
    if not evaluations_dir.exists():
        return
    for path in evaluations_dir.glob("*/metrics.json"):
        path.unlink()


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def load_json(path: Path) -> tuple[Any | None, str]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), "present"
    except json.JSONDecodeError:
        return None, "malformed"


def first_present(row: dict[str, str], keys: list[str]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def candidate_search_dirs(config: EvaluationConfig, row: dict[str, str], candidate_id: str) -> list[Path]:
    dirs: list[Path] = []
    explicit_dir = first_present(row, ["sampling_output_dir", "sample_dir", "structures_dir"])
    if explicit_dir:
        dirs.append(resolve_under_root(config.repo_root, explicit_dir))
    dirs.extend(
        [
            config.samples_root / candidate_id,
        ]
    )
    if config.round_id:
        for stage04_root in (single_stage04_dir(config.output_root), zscheme_stage04_dir(config.output_root)):
            dirs.extend(
                [
                    config.repo_root / stage04_root / "rounds" / config.round_id / "samples" / candidate_id,
                    config.repo_root / stage04_root / "rounds" / config.round_id / "sampled_structures" / candidate_id,
                    config.repo_root / stage04_root / "rounds" / config.round_id / candidate_id,
                ]
            )
    for stage04_root in (single_stage04_dir(config.output_root), zscheme_stage04_dir(config.output_root)):
        dirs.extend(
            [
                config.repo_root / stage04_root / "samples" / candidate_id,
                config.repo_root / stage04_root / "sampled_structures" / candidate_id,
                config.repo_root / stage04_root / candidate_id,
            ]
        )
    return dirs


def write_round_aliases(output_dir: Path, round_id: str | None, artifact_names: list[str]) -> None:
    if not round_id:
        return
    for name in artifact_names:
        source = output_dir / name
        if not source.exists():
            continue
        alias = output_dir / f"{source.stem}_{round_id}{source.suffix}"
        if not alias.exists():
            alias.write_bytes(source.read_bytes())


def write_round_manifest(
    config: EvaluationConfig,
    status: str,
    artifact_names: list[str],
    candidate_count: int,
    combined_structure_count: int,
) -> None:
    artifacts = {
        name: display_ows_path(config.output_dir / name, config.repo_root)
        for name in artifact_names
        if (config.output_dir / name).exists()
    }
    manifest = {
        "round_id": config.round_id,
        "round_dir": display_ows_path(config.output_dir, config.repo_root),
        "artifact_layout": "stage/rounds/round_id" if config.round_id else "explicit_output_dir",
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
        "sampling_plan_path": display_ows_path(config.sampling_plan_path, config.repo_root),
        "samples_root": display_ows_path(config.samples_root, config.repo_root),
        "candidate_count": candidate_count,
        "combined_structure_count": combined_structure_count,
        "artifacts": artifacts,
    }
    (config.output_dir / "ROUND_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if config.stage_root is None or config.round_id is None:
        return
    config.stage_root.mkdir(parents=True, exist_ok=True)
    for pointer_name in ROUND_POINTER_NAMES:
        (config.stage_root / pointer_name).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def find_structures_path(config: EvaluationConfig, row: dict[str, str], candidate_id: str) -> Path:
    explicit = first_present(
        row,
        [
            "structures_path",
            "generated_structures_path",
            "sample_path",
            "samples_path",
            "output_structures_path",
        ],
    )
    if explicit:
        return resolve_under_root(config.repo_root, explicit)

    for candidate_dir in candidate_search_dirs(config, row, candidate_id):
        if candidate_dir.is_file():
            return candidate_dir
        for pattern in (
            "generated_crystals.extxyz",
            "generated_structures.extxyz",
            "generated_crystals_cif.zip",
            "*.extxyz",
            "*.xyz",
            "*.cif",
            "*.zip",
        ):
            matches = sorted(candidate_dir.glob(pattern))
            if matches:
                return matches[0]
    return config.samples_root / candidate_id / "generated_structures.extxyz"


def read_extxyz_blocks(path: Path) -> list[str]:
    blocks: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        while True:
            first_line = handle.readline()
            if not first_line:
                break
            stripped = first_line.strip()
            if not stripped:
                continue
            try:
                atom_count = int(stripped)
            except ValueError as error:
                raise ValueError(f"Invalid extxyz atom count in {path}: {stripped}") from error
            lines = [first_line]
            comment = handle.readline()
            if not comment:
                raise ValueError(f"Unexpected end of file after atom count in {path}.")
            lines.append(comment)
            for _ in range(atom_count):
                atom_line = handle.readline()
                if not atom_line:
                    raise ValueError(f"Unexpected end of file while reading atoms in {path}.")
                lines.append(atom_line)
            blocks.append("".join(lines))
    return blocks


def parse_lattice_from_extxyz_comment(comment: str) -> list[list[float]]:
    lattice_text = ""
    for token in shlex.split(comment):
        if token.startswith("Lattice="):
            lattice_text = token.split("=", 1)[1]
            break
    values = [float(value) for value in lattice_text.split()]
    if len(values) != 9:
        raise ValueError("missing_or_invalid_lattice")
    return [values[0:3], values[3:6], values[6:9]]


def parse_extxyz_block_for_structure(block: str) -> tuple[list[list[float]], list[str], list[list[float]]]:
    lines = block.splitlines()
    if len(lines) < 2:
        raise ValueError("invalid_extxyz_block")
    atom_count = int(lines[0].strip())
    lattice = parse_lattice_from_extxyz_comment(lines[1])
    species: list[str] = []
    coords: list[list[float]] = []
    atom_lines = lines[2 : 2 + atom_count]
    if len(atom_lines) != atom_count:
        raise ValueError("atom_count_mismatch")
    for atom_line in atom_lines:
        parts = atom_line.split()
        if len(parts) < 4:
            raise ValueError("invalid_atom_line")
        species.append(parts[0])
        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
    return lattice, species, coords


def relaxed_space_group_from_block(block: str) -> dict[str, str]:
    try:
        structure_module = importlib.import_module("pymatgen.core.structure")
        analyzer_module = importlib.import_module("pymatgen.symmetry.analyzer")
    except ModuleNotFoundError:
        return {
            "relaxed_space_group_symbol": "",
            "relaxed_space_group_number": "",
            "relaxed_crystal_system": "",
            "space_group_analysis_note": "pymatgen_missing",
        }

    try:
        lattice, species, coords = parse_extxyz_block_for_structure(block)
        structure = structure_module.Structure(
            lattice,
            species,
            coords,
            coords_are_cartesian=True,
        )
        analyzer = analyzer_module.SpacegroupAnalyzer(
            structure,
            symprec=SPACE_GROUP_SYMPREC,
            angle_tolerance=SPACE_GROUP_ANGLE_TOLERANCE,
        )
        return {
            "relaxed_space_group_symbol": analyzer.get_space_group_symbol(),
            "relaxed_space_group_number": str(analyzer.get_space_group_number()),
            "relaxed_crystal_system": analyzer.get_crystal_system(),
            "space_group_analysis_note": (
                f"relaxed_structure;symprec={SPACE_GROUP_SYMPREC};angle_tolerance={SPACE_GROUP_ANGLE_TOLERANCE}"
            ),
        }
    except TypeError:
        return {
            "relaxed_space_group_symbol": "P1",
            "relaxed_space_group_number": "1",
            "relaxed_crystal_system": "triclinic",
            "space_group_analysis_note": (
                "relaxed_structure;fallback=P1;"
                f"symprec={SPACE_GROUP_SYMPREC};angle_tolerance={SPACE_GROUP_ANGLE_TOLERANCE}"
            ),
        }
    except Exception as error:
        return {
            "relaxed_space_group_symbol": "",
            "relaxed_space_group_number": "",
            "relaxed_crystal_system": "",
            "space_group_analysis_note": f"space_group_analysis_failed:{type(error).__name__}:{error}",
        }


def relaxed_space_group_from_blocks(blocks: list[str], index: int) -> dict[str, str]:
    if index < 0 or index >= len(blocks):
        return {
            "relaxed_space_group_symbol": "",
            "relaxed_space_group_number": "",
            "relaxed_crystal_system": "",
            "space_group_analysis_note": "missing_relaxed_structure_frame",
        }
    return relaxed_space_group_from_block(blocks[index])


def write_text_blocks_if_changed(path: Path, blocks: list[str]) -> bool:
    text_parts: list[str] = []
    for block in blocks:
        text_parts.append(block)
        if not block.endswith("\n"):
            text_parts.append("\n")
    text = "".join(text_parts)
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def select_reference_dataset(config: EvaluationConfig) -> tuple[str, Path | None]:
    if config.energy_correction_scheme not in {"auto", "TRI2024"}:
        raise ValueError("energy_correction_scheme must be `auto` or `TRI2024`.")
    if config.energy_correction_scheme == "TRI2024":
        if config.reference_dataset_path is not None:
            return "TRI2024", config.reference_dataset_path
        return "TRI2024", config.tri_reference_path
    if config.reference_dataset_path is not None:
        return "TRI2024", config.reference_dataset_path
    if config.tri_reference_path.exists():
        return "TRI2024", config.tri_reference_path
    return "TRI2024", config.tri_reference_path


def command_for_combined(config: EvaluationConfig, combined: CombinedEvaluation) -> str:
    gpu_id = normalize_gpu_ids(config.gpu_id)
    parts = [
        THREAD_LIMIT_ENV,
        f"CUDA_VISIBLE_DEVICES={gpu_id}",
        relpath(config.evaluator_path, config.repo_root),
        "--structures_path",
        relpath(combined.structures_path, config.repo_root),
        "--relax",
        "True",
        "--structure_matcher",
        config.structure_matcher,
        "--device",
        config.device,
        "--potential_load_path",
        relpath(config.potential_load_path, config.repo_root),
        "--energy_correction_scheme",
        combined.effective_reference_dataset,
        "--save_as",
        relpath(combined.metrics_path, config.repo_root),
        "--save_detailed_as",
        relpath(combined.detailed_metrics_path, config.repo_root),
        "--structures_output_path",
        relpath(combined.relaxed_structures_path, config.repo_root),
    ]
    if combined.reference_dataset_path is not None:
        parts.extend(["--reference_dataset_path", relpath(combined.reference_dataset_path, config.repo_root)])
    return " ".join(parts)


def build_candidate_plans(config: EvaluationConfig, sampling_rows: list[dict[str, str]]) -> list[CandidatePlan]:
    effective_reference_dataset, reference_dataset_path = select_reference_dataset(config)
    plans = []
    for index, row in enumerate(sampling_rows, start=1):
        candidate_id = (
            first_present(row, ["candidate_id", "id", "candidate_name", "formula"]) or f"candidate-{index:03d}"
        )
        structures_path = find_structures_path(config, row, candidate_id)
        evaluation_dir = config.output_dir / "evaluations" / candidate_id
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        legacy_metrics_path = evaluation_dir / "metrics.json"
        if legacy_metrics_path.exists():
            legacy_metrics_path.unlink()
        metrics_path = config.output_dir / "combined" / "metrics.json"
        detailed_metrics_path = evaluation_dir / "detailed_metrics.json"
        relaxed_structures_path = evaluation_dir / "relaxed_structures.extxyz"
        sampling_artifact_status = "present" if structures_path.exists() else "missing"
        metrics_status = "missing"
        plans.append(
            CandidatePlan(
                candidate_id=candidate_id,
                sampling_status=first_present(row, ["sampling_status"]) or "unknown",
                upstream_return_step=first_present(row, ["recommended_return_step"]),
                structures_path=structures_path,
                evaluation_dir=evaluation_dir,
                metrics_path=metrics_path,
                detailed_metrics_path=detailed_metrics_path,
                relaxed_structures_path=relaxed_structures_path,
                effective_reference_dataset=effective_reference_dataset,
                reference_dataset_path=reference_dataset_path,
                sampling_artifact_status=sampling_artifact_status,
                metrics_status=metrics_status,
                evaluation_command="",
            )
        )
    return plans


def build_combined_evaluation(config: EvaluationConfig) -> CombinedEvaluation:
    effective_reference_dataset, reference_dataset_path = select_reference_dataset(config)
    combined_dir = config.output_dir / "combined"
    combined = CombinedEvaluation(
        evaluation_dir=combined_dir,
        structures_path=combined_dir / "generated_crystals.extxyz",
        manifest_path=combined_dir / "COMBINED_STRUCTURE_MANIFEST.csv",
        metrics_path=combined_dir / "metrics.json",
        detailed_metrics_path=combined_dir / "detailed_metrics.json",
        relaxed_structures_path=combined_dir / "relaxed_structures.extxyz",
        effective_reference_dataset=effective_reference_dataset,
        reference_dataset_path=reference_dataset_path,
        evaluation_command="",
    )
    return replace(combined, evaluation_command=command_for_combined(config, combined))


def prepare_combined_inputs(
    config: EvaluationConfig, plans: list[CandidatePlan], combined: CombinedEvaluation
) -> tuple[list[dict[str, Any]], int, list[str], bool]:
    blocks: list[str] = []
    manifest_rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    global_index = 0

    for plan in plans:
        if plan.sampling_artifact_status != "present":
            continue
        if plan.structures_path.suffix not in {".extxyz", ".xyz"}:
            blockers.append(f"合并评估只支持 extxyz/xyz 采样产物: `{plan.structures_path}`。")
            continue
        try:
            candidate_blocks = read_extxyz_blocks(plan.structures_path)
        except ValueError as error:
            blockers.append(str(error))
            continue
        for local_index, block in enumerate(candidate_blocks):
            blocks.append(block)
            manifest_rows.append(
                {
                    "global_structure_index": global_index,
                    "candidate_id": plan.candidate_id,
                    "local_structure_index": local_index,
                    "source_structures_path": relpath(plan.structures_path, config.repo_root),
                    "candidate_detailed_metrics_path": relpath(plan.detailed_metrics_path, config.repo_root),
                    "candidate_relaxed_structures_path": relpath(plan.relaxed_structures_path, config.repo_root),
                }
            )
            global_index += 1

    inputs_changed = False
    if blocks:
        inputs_changed = write_text_blocks_if_changed(combined.structures_path, blocks)
    else:
        blockers.append("没有可合并的 generated_crystals.extxyz / generated_structures.extxyz 结构。")
        combined.structures_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            not combined.structures_path.exists()
            or combined.structures_path.read_text(encoding="utf-8", errors="replace") != ""
        ):
            combined.structures_path.write_text("", encoding="utf-8")
            inputs_changed = True
    manifest_changed = write_csv_if_changed(combined.manifest_path, MANIFEST_COLUMNS, manifest_rows)
    return manifest_rows, len(blocks), blockers, inputs_changed or manifest_changed


def combined_plan_to_row(
    config: EvaluationConfig,
    combined: CombinedEvaluation,
    combined_structure_count: int,
    candidate_count: int,
) -> dict[str, Any]:
    return {
        "combined_structures_path": relpath(combined.structures_path, config.repo_root),
        "combined_manifest_path": relpath(combined.manifest_path, config.repo_root),
        "combined_metrics_path": relpath(combined.metrics_path, config.repo_root),
        "combined_detailed_metrics_path": relpath(combined.detailed_metrics_path, config.repo_root),
        "combined_relaxed_structures_path": relpath(combined.relaxed_structures_path, config.repo_root),
        "effective_reference_dataset": combined.effective_reference_dataset,
        "reference_dataset_path": (
            relpath(combined.reference_dataset_path, config.repo_root) if combined.reference_dataset_path else ""
        ),
        "combined_structure_count": combined_structure_count,
        "candidate_count": candidate_count,
        "evaluation_command": combined.evaluation_command,
    }


def update_plans_for_combined_metrics(
    plans: list[CandidatePlan],
    combined: CombinedEvaluation,
    detailed_status: str,
) -> list[CandidatePlan]:
    metrics_status = "present" if detailed_status == "present" else detailed_status
    updated = []
    for plan in plans:
        updated.append(
            replace(
                plan,
                metrics_path=combined.metrics_path,
                metrics_status=metrics_status,
                evaluation_command=combined.evaluation_command,
            )
        )
    return updated


def manifest_rows_by_candidate(manifest_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in manifest_rows:
        rows_by_candidate.setdefault(str(row["candidate_id"]), []).append(row)
    return rows_by_candidate


def write_split_candidate_outputs(
    config: EvaluationConfig,
    plans: list[CandidatePlan],
    manifest_rows: list[dict[str, Any]],
    detailed: dict[str, Any],
    combined: CombinedEvaluation,
) -> None:
    rows_by_candidate = manifest_rows_by_candidate(manifest_rows)
    relaxed_blocks: list[str] = []
    if combined.relaxed_structures_path.exists():
        relaxed_blocks = read_extxyz_blocks(combined.relaxed_structures_path)

    for plan in plans:
        candidate_rows = rows_by_candidate.get(plan.candidate_id, [])
        if not candidate_rows:
            continue

        split_detailed: dict[str, Any] = {}
        indices = [int(row["global_structure_index"]) for row in candidate_rows]
        for key, value in detailed.items():
            if isinstance(value, list):
                split_detailed[key] = [value[index] for index in indices if index < len(value)]

        plan.evaluation_dir.mkdir(parents=True, exist_ok=True)
        split_payload = {
            "source": {
                "evaluation_scope": "combined_global_context",
                "combined_detailed_metrics_path": relpath(combined.detailed_metrics_path, config.repo_root),
                "combined_metrics_path": relpath(combined.metrics_path, config.repo_root),
                "combined_manifest_path": relpath(combined.manifest_path, config.repo_root),
            },
            **split_detailed,
        }
        plan.evaluation_dir.joinpath("detailed_metrics.json").write_text(
            json.dumps(split_payload, indent=2), encoding="utf-8"
        )

        if relaxed_blocks:
            candidate_blocks = [relaxed_blocks[index] for index in indices if index < len(relaxed_blocks)]
            write_text_blocks_if_changed(plan.relaxed_structures_path, candidate_blocks)


def metric_value_from_global(
    detailed: dict[str, Any], aggregate: dict[str, Any], keys: list[str], global_index: int
) -> Any:
    return metric_value(detailed, aggregate, keys, global_index)


def summarize_plan_from_combined(
    plan: CandidatePlan,
    manifest_rows: list[dict[str, Any]],
    detailed: dict[str, Any],
    aggregate: dict[str, Any],
    relaxed_blocks: list[str],
) -> list[dict[str, Any]]:
    candidate_rows = manifest_rows_by_candidate(manifest_rows).get(plan.candidate_id, [])
    if not candidate_rows:
        verdict, route, reason = route_structure(plan, "", "", "")
        return [
            summary_row(
                plan,
                f"{plan.candidate_id}::pending",
                "",
                "",
                "",
                "",
                verdict,
                route,
                reason,
            )
        ]

    rows = []
    for row in candidate_rows:
        global_index = int(row["global_structure_index"])
        local_index = int(row["local_structure_index"])
        space_group_info = relaxed_space_group_from_blocks(relaxed_blocks, global_index)
        energy_above_hull = metric_value_from_global(
            detailed,
            aggregate,
            ["energy_above_hull", "energy_above_hull_per_atom", "avg_energy_above_hull_per_atom"],
            global_index,
        )
        stability = metric_value_from_global(
            detailed,
            aggregate,
            ["stability", "is_stable", "stable", "frac_stable_structures"],
            global_index,
        )
        novelty = metric_value_from_global(
            detailed,
            aggregate,
            ["novelty", "is_novel", "frac_novel_structures", "frac_novel_unique_structures"],
            global_index,
        )
        uniqueness = metric_value_from_global(
            detailed,
            aggregate,
            ["uniqueness", "is_unique", "frac_unique_structures"],
            global_index,
        )
        verdict, route, reason = route_structure(plan, stability, novelty, uniqueness)
        rows.append(
            summary_row(
                plan,
                f"{plan.candidate_id}::{local_index}",
                energy_above_hull,
                stability,
                novelty,
                uniqueness,
                verdict,
                route,
                reason,
                space_group_info,
            )
        )
    return rows


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def unwrap_aggregate_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def metric_value(detailed: dict[str, Any], aggregate: dict[str, Any], keys: list[str], index: int) -> Any:
    for key in keys:
        values = as_list(detailed.get(key))
        if index < len(values):
            return values[index]
    for key in keys:
        if key in aggregate:
            return unwrap_aggregate_value(aggregate[key])
    return None


def metric_count(detailed: dict[str, Any], aggregate: dict[str, Any]) -> int:
    lengths = [len(value) for value in detailed.values() if isinstance(value, list)]
    if lengths:
        return max(lengths)
    if aggregate:
        return 1
    return 0


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value >= 0.5
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "stable", "pass", "passed"}:
            return True
        if normalized in {"false", "no", "n", "0", "unstable", "fail", "failed"}:
            return False
    return None


def parse_float(value: Any) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return float("inf")


def route_structure(
    plan: CandidatePlan,
    stability: Any,
    novelty: Any,
    uniqueness: Any,
) -> tuple[str, str, str]:
    if plan.upstream_return_step:
        return (
            "blocked_by_sampling_prerequisite",
            plan.upstream_return_step,
            ("上游采样阶段已在 MatterSim 评估前对该候选给出返回路线。"),
        )
    if plan.sampling_status == "blocked":
        return (
            "blocked_by_sampling_prerequisite",
            ROUTE_SAMPLING,
            ("采样阶段已在 MatterSim 评估前将该候选标记为 blocked。"),
        )
    if plan.sampling_artifact_status == "missing" or plan.metrics_status == "missing":
        return "pending_evaluation", ROUTE_SAMPLING, "缺少采样产物或 MatterSim 指标。"

    stable = boolish(stability)
    novel = boolish(novelty)
    if stable is False:
        return (
            "fails_mlff_stability_screen",
            ROUTE_MECHANISM_GATE_REVIEW,
            "MatterSim MLFF 筛选显示该结构不稳定。",
        )
    if stable is True and novel is False:
        return "fails_novelty_screen", ROUTE_NOVELTY_AUDIT, ("MatterGen/MatterSim 筛选显示结构稳定, 但新颖性不足。")
    if stable is True and novel is True:
        return (
            "passes_mlff_screen",
            ROUTE_EXPERIMENTAL_VALIDATION,
            ("基于 MLFF 的筛选显示该结构稳定且新颖; 仍需独立验证。"),
        )
    return "needs_metric_review", ROUTE_SAMPLING, "MatterSim 指标不完整或含义不明确。"


def summarize_plan(plan: CandidatePlan) -> tuple[list[dict[str, Any]], str]:
    aggregate_payload, aggregate_status = load_json(plan.metrics_path)
    detailed_payload, detailed_status = load_json(plan.detailed_metrics_path)
    if aggregate_status == "malformed" or detailed_status == "malformed":
        row = summary_row(
            plan,
            "candidate",
            "",
            "",
            "",
            "",
            "blocked_malformed_metrics",
            ROUTE_SAMPLING,
            "MatterSim 指标 JSON 格式异常; 解释结果前需要重新评估。",
        )
        return [row], "malformed"

    aggregate = aggregate_payload if isinstance(aggregate_payload, dict) else {}
    detailed = detailed_payload if isinstance(detailed_payload, dict) else {}
    count = metric_count(detailed, aggregate)
    if count == 0:
        verdict, route, reason = route_structure(plan, "", "", "")
        row = summary_row(
            plan,
            f"{plan.candidate_id}::pending",
            "",
            "",
            "",
            "",
            verdict,
            route,
            reason,
        )
        return [row], "missing"

    rows = []
    relaxed_blocks = read_extxyz_blocks(plan.relaxed_structures_path) if plan.relaxed_structures_path.exists() else []
    for index in range(count):
        space_group_info = relaxed_space_group_from_blocks(relaxed_blocks, index)
        energy_above_hull = metric_value(
            detailed,
            aggregate,
            ["energy_above_hull", "energy_above_hull_per_atom", "avg_energy_above_hull_per_atom"],
            index,
        )
        stability = metric_value(
            detailed, aggregate, ["stability", "is_stable", "stable", "frac_stable_structures"], index
        )
        novelty = metric_value(
            detailed,
            aggregate,
            ["novelty", "is_novel", "frac_novel_structures", "frac_novel_unique_structures"],
            index,
        )
        uniqueness = metric_value(detailed, aggregate, ["uniqueness", "is_unique", "frac_unique_structures"], index)
        verdict, route, reason = route_structure(plan, stability, novelty, uniqueness)
        rows.append(
            summary_row(
                plan,
                f"{plan.candidate_id}::{index}",
                energy_above_hull,
                stability,
                novelty,
                uniqueness,
                verdict,
                route,
                reason,
                space_group_info,
            )
        )
    return rows, "present"


def summary_row(
    plan: CandidatePlan,
    structure_id: str,
    energy_above_hull: Any,
    stability: Any,
    novelty: Any,
    uniqueness: Any,
    verdict: str,
    route: str,
    reason: str,
    space_group_info: dict[str, str] | None = None,
) -> dict[str, Any]:
    space_group_info = space_group_info or {
        "relaxed_space_group_symbol": "",
        "relaxed_space_group_number": "",
        "relaxed_crystal_system": "",
        "space_group_analysis_note": "",
    }
    return {
        "candidate_id": plan.candidate_id,
        "structure_id": structure_id,
        "metrics_path": plan.metrics_path,
        "detailed_metrics_path": plan.detailed_metrics_path,
        "relaxed_structures_path": plan.relaxed_structures_path,
        "relaxed_space_group_symbol": space_group_info.get("relaxed_space_group_symbol", ""),
        "relaxed_space_group_number": space_group_info.get("relaxed_space_group_number", ""),
        "relaxed_crystal_system": space_group_info.get("relaxed_crystal_system", ""),
        "space_group_analysis_note": space_group_info.get("space_group_analysis_note", ""),
        "energy_above_hull": energy_above_hull,
        "stability": stability,
        "novelty": novelty,
        "uniqueness": uniqueness,
        "evaluation_verdict": verdict,
        "recommended_return_step": route,
        "routing_reason": reason,
    }


def plan_to_row(plan: CandidatePlan, repo_root: Path) -> dict[str, Any]:
    return {
        "candidate_id": plan.candidate_id,
        "sampling_status": plan.sampling_status,
        "upstream_return_step": plan.upstream_return_step,
        "structures_path": relpath(plan.structures_path, repo_root),
        "evaluation_dir": relpath(plan.evaluation_dir, repo_root),
        "metrics_path": relpath(plan.metrics_path, repo_root),
        "detailed_metrics_path": relpath(plan.detailed_metrics_path, repo_root),
        "relaxed_structures_path": relpath(plan.relaxed_structures_path, repo_root),
        "effective_reference_dataset": plan.effective_reference_dataset,
        "reference_dataset_path": (
            relpath(plan.reference_dataset_path, repo_root) if plan.reference_dataset_path else ""
        ),
        "sampling_artifact_status": plan.sampling_artifact_status,
        "metrics_status": plan.metrics_status,
        "evaluation_command": plan.evaluation_command,
    }


def summary_to_csv_row(row: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    converted = dict(row)
    for key in ["metrics_path", "detailed_metrics_path", "relaxed_structures_path"]:
        converted[key] = relpath(Path(converted[key]), repo_root) if converted.get(key) else ""
    return converted


def write_final_structure_report(path: Path, rows: list[dict[str, Any]], repo_root: Path) -> None:
    csv_rows = [summary_to_csv_row(row, repo_root) for row in rows]
    passed = [
        row
        for row in csv_rows
        if row.get("recommended_return_step") == ROUTE_EXPERIMENTAL_VALIDATION
        and boolish(row.get("stability")) is True
        and boolish(row.get("novelty")) is True
    ]
    lines = ["# 单光催化剂最终推荐", ""]
    if not passed:
        lines.extend(
            [
                "当前没有单光催化剂结构同时满足稳定、新颖并进入实验验证路线。",
                "",
                "## 返回路线",
                "",
            ]
        )
        for row in csv_rows:
            lines.append(
                f"- `{row.get('candidate_id', '')}` / `{row.get('structure_id', '')}`: "
                f"`{row.get('recommended_return_step', '')}`; {row.get('routing_reason', '')}"
            )
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return

    lines.extend(["以下单光催化剂结构通过 MatterSim MLFF 结构筛选。", ""])
    for row in passed:
        space_group = row.get("relaxed_space_group_symbol") or "未能计算"
        space_group_number = row.get("relaxed_space_group_number") or "unknown"
        crystal_system = row.get("relaxed_crystal_system") or "unknown"
        lines.extend(
            [
                f"## {row.get('candidate_id', '')} / {row.get('structure_id', '')}",
                "",
                f"- energy_above_hull: `{row.get('energy_above_hull', '')}`",
                f"- stability: `{row.get('stability', '')}`",
                f"- novelty: `{row.get('novelty', '')}`",
                f"- uniqueness: `{row.get('uniqueness', '')}`",
                f"- relaxed space group: `{space_group}` / No. `{space_group_number}` / `{crystal_system}`",
                f"- space group analysis: `{row.get('space_group_analysis_note', '')}`",
                f"- metrics: `{row.get('metrics_path', '')}`",
                f"- detailed metrics: `{row.get('detailed_metrics_path', '')}`",
                f"- relaxed structures: `{row.get('relaxed_structures_path', '')}`",
                f"- 判断: {row.get('routing_reason', '')}",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def is_zscheme_branch(config: EvaluationConfig) -> bool:
    return any("zscheme" in part.lower() for part in config.output_dir.parts) or any(
        "zscheme" in part.lower() for part in config.sampling_plan_path.parts
    )


def slugify(text: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in text)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "catalyst"


def stage08_round_dir(config: EvaluationConfig) -> Path | None:
    if config.round_id is None:
        return None
    return resolve_under_root(
        config.repo_root,
        config.output_root / STAGE08_DIRNAME / "rounds" / config.round_id,
    )


def stage02_recommendation_context_path(config: EvaluationConfig) -> Path | None:
    if config.round_id is None:
        return None
    return resolve_under_root(
        config.repo_root,
        config.output_root / STAGE02_DIRNAME / "rounds" / config.round_id / "RECOMMENDATION_CONTEXT.json",
    )


def stage02_laboratory_limitations_handoff(config: EvaluationConfig) -> dict[str, Any]:
    context_path = stage02_recommendation_context_path(config)
    if context_path is None:
        return {
            "laboratory_limitations": [],
            "laboratory_limitations_policy": {},
            "laboratory_limitations_source": "",
            "stage02_recommendation_context": "",
            "laboratory_limitations_handoff_status": "missing_round_id",
        }

    context = read_json_if_present(context_path)
    input_paths = context.get("input_artifact_paths", {}) if context else {}
    if not isinstance(input_paths, dict):
        input_paths = {}
    limitations = context.get("laboratory_limitations_records", []) if context else []
    if not isinstance(limitations, list):
        limitations = []
    policy = context.get("laboratory_limitations_policy", {}) if context else {}
    if not isinstance(policy, dict):
        policy = {}
    return {
        "laboratory_limitations": limitations,
        "laboratory_limitations_policy": policy,
        "laboratory_limitations_source": str(input_paths.get("laboratory_limitations_path") or ""),
        "stage02_recommendation_context": display_ows_path(context_path, config.repo_root),
        "laboratory_limitations_handoff_status": "available" if context else "missing_stage02_context",
    }


def synthesis_route_output_path(stage08_dir: Path, identifier: str, name: str) -> Path:
    label = slugify(identifier or name)
    if not label or label == "catalyst":
        label = slugify(name)
    return stage08_dir / "synthesis-routes" / f"{label}_synthesis_route.md"


def rows_by_id(path: Path, key: str) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return {
        (row.get(key) or "").strip(): {field: value or "" for field, value in row.items()}
        for row in read_csv_rows(path)
        if (row.get(key) or "").strip()
    }


def normalize_formula(value: Any) -> str:
    return "".join(str(value or "").strip().split())


def single_candidate_formula(candidate_id: str, candidates: dict[str, dict[str, str]]) -> str:
    candidate = candidates.get(candidate_id, {})
    return normalize_formula(candidate.get("main_photocatalyst")) or candidate_id


def best_structure_sort_key(row: dict[str, Any]) -> tuple[float, str, str]:
    return (
        parse_float(row.get("energy_above_hull")),
        str(row.get("structure_id", "")),
        str(row.get("candidate_id", "")),
    )


def apply_single_best_structure_per_formula_filter(
    config: EvaluationConfig,
    summary_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if is_zscheme_branch(config) or config.round_id is None:
        return summary_rows

    candidates_path = resolve_under_root(
        config.repo_root,
        config.output_root / STAGE02_DIRNAME / "rounds" / config.round_id / "RECOMMENDED_CANDIDATES.csv",
    )
    candidates = rows_by_id(candidates_path, "candidate_id")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in summary_rows:
        if (row.get("recommended_return_step") or "").strip() != ROUTE_EXPERIMENTAL_VALIDATION:
            continue
        candidate_id = str(row.get("candidate_id", "")).strip()
        formula = single_candidate_formula(candidate_id, candidates)
        if formula:
            grouped.setdefault(formula, []).append(row)

    for formula, rows in grouped.items():
        if len(rows) <= 1:
            continue
        best = min(rows, key=best_structure_sort_key)
        best_structure_id = str(best.get("structure_id", ""))
        best_energy = best.get("energy_above_hull", "")
        for row in rows:
            if row is best:
                continue
            row["evaluation_verdict"] = FILTER_DUPLICATE_STRUCTURE
            row["recommended_return_step"] = FILTER_DUPLICATE_STRUCTURE
            row["routing_reason"] = (
                f"同一化学式 {formula} 有多个结构通过 MatterSim; 仅保留 "
                f"energy_above_hull 最低的结构 `{best_structure_id}` "
                f"(energy_above_hull={best_energy}) 作为可靠推荐。"
            )
    return summary_rows


def write_single_synthesis_input_summary(
    config: EvaluationConfig,
    summary_rows: list[dict[str, Any]],
) -> str:
    if is_zscheme_branch(config):
        return ""
    round_id = config.round_id
    stage08_dir = stage08_round_dir(config)
    if stage08_dir is None or round_id is None:
        return ""

    input_json = stage08_dir / SYNTHESIS_INPUT_SUMMARY_NAME
    passed_rows = [
        row
        for row in summary_rows
        if (row.get("recommended_return_step") or "").strip() == ROUTE_EXPERIMENTAL_VALIDATION
    ]
    if not passed_rows:
        if input_json.exists():
            try:
                existing_payload = json.loads(input_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing_payload = {}
            if not isinstance(existing_payload, dict):
                existing_payload = {}
            other_records = [
                record
                for record in existing_payload.get("records", [])
                if isinstance(record, dict) and record.get("system") != "single"
            ]
            if other_records:
                payload = {
                    "records": other_records,
                    **stage02_laboratory_limitations_handoff(config),
                    "manifest_output": display_ows_path(stage08_dir / SYNTHESIS_ROUTE_INDEX_NAME, config.repo_root),
                    "output_dir": display_ows_path(stage08_dir / "synthesis-routes", config.repo_root),
                }
                input_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            else:
                input_json.unlink()
        return ""

    candidates_path = resolve_under_root(
        config.repo_root,
        config.output_root / STAGE02_DIRNAME / "rounds" / round_id / "RECOMMENDED_CANDIDATES.csv",
    )
    candidates = rows_by_id(candidates_path, "candidate_id")
    records: list[dict[str, str]] = []
    for index, passed in enumerate(passed_rows, start=1):
        candidate_id = str(passed.get("candidate_id", "")).strip()
        structure_id = str(passed.get("structure_id", "")).strip()
        record_id = structure_id or candidate_id or f"{round_id}_single_{index}"
        candidate = candidates.get(candidate_id, {})
        catalyst_name = candidate.get("candidate_name", candidate_id or f"{round_id}_single_{index}")
        records.append(
            {
                "record_id": record_id,
                "system": "single",
                "catalyst_name": catalyst_name,
                "reduced_formula": candidate.get("main_photocatalyst", ""),
                "space_group": str(passed.get("relaxed_space_group_symbol", "")).strip(),
            }
        )

    existing_records: list[dict[str, str]] = []
    if input_json.exists():
        try:
            existing_payload = json.loads(input_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_payload = {}
        if not isinstance(existing_payload, dict):
            existing_payload = {}
        existing_records = [
            record
            for record in existing_payload.get("records", [])
            if isinstance(record, dict) and record.get("system") != "single"
        ]
    merged_records_by_key = {
        f"{record.get('system', '')}:{record.get('record_id', '')}": record for record in [*existing_records, *records]
    }
    payload = {
        "records": list(merged_records_by_key.values()),
        **stage02_laboratory_limitations_handoff(config),
        "manifest_output": display_ows_path(stage08_dir / SYNTHESIS_ROUTE_INDEX_NAME, config.repo_root),
        "output_dir": display_ows_path(stage08_dir / "synthesis-routes", config.repo_root),
    }
    stage08_dir.mkdir(parents=True, exist_ok=True)
    input_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return display_ows_path(input_json, config.repo_root)


def prerequisite_blockers(config: EvaluationConfig) -> list[str]:
    _, reference_dataset_path = select_reference_dataset(config)
    checks = [
        (config.sampling_plan_path, f"缺少采样计划: `{config.sampling_plan_path}`。"),
        (config.evaluator_path, f"缺少 MatterGen evaluator CLI: `{config.evaluator_path}`。"),
        (config.potential_load_path, f"缺少 MatterSim 模型: `{config.potential_load_path}`。"),
        (
            reference_dataset_path,
            f"缺少 MatterGen 参考数据集: `{reference_dataset_path}`。",
        ),
    ]
    blockers = [message for path, message in checks if path is None or not path.exists()]
    if not gpu_id_is_safe(config.gpu_id):
        blockers.append("缺少已确认的 CUDA GPU ID, 或 GPU ID 格式无效。")
    return blockers


def prepare_mattersim_evaluation(config: EvaluationConfig) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    remove_obsolete_outputs(config.output_dir)
    remove_legacy_candidate_metrics(config.output_dir)
    blocked_reasons = prerequisite_blockers(config)
    sampling_rows = read_csv_rows(config.sampling_plan_path) if config.sampling_plan_path.exists() else []
    plans = build_candidate_plans(config, sampling_rows)
    combined = build_combined_evaluation(config)
    manifest_rows, combined_structure_count, merge_blockers, combined_inputs_changed = prepare_combined_inputs(
        config, plans, combined
    )
    blocked_reasons.extend(merge_blockers)

    aggregate_payload, aggregate_status = load_json(combined.metrics_path)
    detailed_payload, detailed_status = load_json(combined.detailed_metrics_path)
    plans = update_plans_for_combined_metrics(plans, combined, detailed_status)
    aggregate = aggregate_payload if isinstance(aggregate_payload, dict) else {}
    detailed = detailed_payload if isinstance(detailed_payload, dict) else {}
    combined_metrics_count = metric_count(detailed, aggregate)
    metrics_count_mismatch = detailed_status == "present" and combined_metrics_count != combined_structure_count
    metrics_are_stale = (
        detailed_status == "present"
        and combined_inputs_changed
        and combined.detailed_metrics_path.stat().st_mtime < combined.structures_path.stat().st_mtime
    )

    summary_rows: list[dict[str, Any]] = []
    malformed_metrics = []
    if aggregate_status == "malformed" or detailed_status == "malformed":
        malformed_metrics.append("combined")
        for plan in plans:
            summary_rows.append(
                summary_row(
                    plan,
                    "candidate",
                    "",
                    "",
                    "",
                    "",
                    "blocked_malformed_metrics",
                    ROUTE_SAMPLING,
                    "Combined MatterSim 指标 JSON 格式异常; 解释结果前需要重新评估。",
                )
            )
    elif metrics_count_mismatch:
        malformed_metrics.append("combined_count_mismatch")
        for plan in plans:
            summary_rows.append(
                summary_row(
                    plan,
                    "candidate",
                    "",
                    "",
                    "",
                    "",
                    "blocked_stale_combined_metrics",
                    ROUTE_SAMPLING,
                    "Combined MatterSim 指标数量与当前合并 manifest 不一致; 需要重新评估。",
                )
            )
    elif metrics_are_stale:
        malformed_metrics.append("combined_stale_metrics")
        for plan in plans:
            summary_rows.append(
                summary_row(
                    plan,
                    "candidate",
                    "",
                    "",
                    "",
                    "",
                    "blocked_stale_combined_metrics",
                    ROUTE_SAMPLING,
                    "Combined MatterSim 输入已更新, 现有指标早于合并结构文件; 需要重新评估。",
                )
            )
    elif detailed_status == "present":
        write_split_candidate_outputs(config, plans, manifest_rows, detailed, combined)
        relaxed_blocks = (
            read_extxyz_blocks(combined.relaxed_structures_path) if combined.relaxed_structures_path.exists() else []
        )
        for plan in plans:
            summary_rows.extend(summarize_plan_from_combined(plan, manifest_rows, detailed, aggregate, relaxed_blocks))
    else:
        for plan in plans:
            verdict, route, reason = route_structure(plan, "", "", "")
            summary_rows.append(
                summary_row(
                    plan,
                    f"{plan.candidate_id}::pending",
                    "",
                    "",
                    "",
                    "",
                    verdict,
                    route,
                    reason,
                )
            )

    if not is_zscheme_branch(config):
        summary_rows = apply_single_best_structure_per_formula_filter(config, summary_rows)

    plan_rows = [plan_to_row(plan, config.repo_root) for plan in plans]
    write_csv(config.output_dir / "EVALUATION_PLAN.csv", PLAN_COLUMNS, plan_rows)
    write_csv(
        config.output_dir / "COMBINED_EVALUATION_PLAN.csv",
        COMBINED_PLAN_COLUMNS,
        [combined_plan_to_row(config, combined, combined_structure_count, len(plans))],
    )
    summary_csv_rows = [summary_to_csv_row(row, config.repo_root) for row in summary_rows]
    write_csv(
        config.output_dir / "STRUCTURE_EVALUATION_SUMMARY.csv",
        SUMMARY_COLUMNS,
        summary_csv_rows,
    )
    synthesis_input_summary_path = ""
    if not is_zscheme_branch(config):
        write_final_structure_report(
            config.output_dir / "FINAL_STRUCTURE_RECOMMENDATIONS.md",
            summary_rows,
            config.repo_root,
        )
        synthesis_input_summary_path = write_single_synthesis_input_summary(config, summary_csv_rows)
    artifact_names = [
        "EVALUATION_PLAN.csv",
        "COMBINED_EVALUATION_PLAN.csv",
        "STRUCTURE_EVALUATION_SUMMARY.csv",
    ]
    if (config.output_dir / "FINAL_STRUCTURE_RECOMMENDATIONS.md").exists():
        artifact_names.append("FINAL_STRUCTURE_RECOMMENDATIONS.md")
    write_round_aliases(config.output_dir, config.round_id, artifact_names)
    status = "blocked" if blocked_reasons or malformed_metrics else "ready"
    write_round_manifest(config, status, artifact_names, len(plans), combined_structure_count)
    return {
        "status": status,
        "output_dir": str(config.output_dir),
        "round_id": config.round_id,
        "candidate_count": len(plans),
        "combined_structure_count": combined_structure_count,
        "combined_inputs_changed": combined_inputs_changed,
        "summarized_structure_count": len(summary_rows),
        "combined_evaluation_command": combined.evaluation_command,
        "blocked_reasons": blocked_reasons,
        "malformed_metrics": malformed_metrics,
        "synthesis_input_summary_path": synthesis_input_summary_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare MatterSim evaluation plans and metric summaries under output_root."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--sampling-plan-path", default=None)
    parser.add_argument("--samples-root", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--round-id",
        default=None,
        help="Round identifier used under <stage-dir>/rounds/<round-id>.",
    )
    parser.add_argument("--evaluator-path", default=str(default_evaluator_path()))
    parser.add_argument(
        "--potential-load-path",
        default=str(default_mattersim_model_path()),
    )
    parser.add_argument(
        "--tri-reference-path",
        default=str(default_tri_reference_path()),
    )
    parser.add_argument("--reference-dataset-path", default="")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--gpu-id", required=True, help='Confirmed CUDA GPU ID(s), for example 0, 0,1, or "0 1".')
    parser.add_argument("--structure-matcher", default="disordered")
    parser.add_argument("--energy-correction-scheme", default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_root = Path(args.output_root)
    output_dir, stage_root, round_id, sampling_plan_path, samples_root = resolve_round_paths(
        repo_root,
        output_root,
        args.output_dir,
        args.sampling_plan_path,
        args.samples_root,
        args.round_id,
    )
    reference_dataset_path = (
        resolve_under_root(repo_root, args.reference_dataset_path) if args.reference_dataset_path else None
    )
    config = EvaluationConfig(
        repo_root=repo_root,
        output_root=output_root,
        sampling_plan_path=sampling_plan_path,
        samples_root=samples_root,
        output_dir=output_dir,
        stage_root=stage_root,
        round_id=round_id,
        evaluator_path=resolve_under_root(repo_root, args.evaluator_path),
        potential_load_path=resolve_under_root(repo_root, args.potential_load_path),
        tri_reference_path=resolve_under_root(repo_root, args.tri_reference_path),
        reference_dataset_path=reference_dataset_path,
        device=args.device,
        gpu_id=normalize_gpu_ids(args.gpu_id),
        structure_matcher=args.structure_matcher,
        energy_correction_scheme=args.energy_correction_scheme,
    )
    sys.stdout.write(json.dumps(prepare_mattersim_evaluation(config), indent=2) + "\n")


if __name__ == "__main__":
    main()
