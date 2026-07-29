from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_COLUMNS = ("candidate_id", "candidate_name", "main_photocatalyst")
OPTIONAL_COLUMNS = ("main_photocatalyst_formula_note",)
PLAN_COLUMNS = (
    "candidate_id",
    "candidate_name",
    "main_photocatalyst",
    "main_photocatalyst_formula_note",
    "target_composition_json",
    "sampling_output_dir",
    "sampling_command",
    "sampling_status",
    "blocked_reason",
    "recommended_return_step",
)
RETURN_TO_02 = "return_to_02_candidate_concretization"
READY_TO_SAMPLE = "ready_to_sample"
DEFAULT_OUTPUT_ROOT = Path("ows")
STAGE02_DIRNAME = "02-ows-catalyst-recommender"
DEFAULT_STAGE04_DIRNAME = "04-mattergen-structure-sampler"
ZSCHEME_STAGE04_DIRNAME = "04-zscheme-mattergen-structure-sampler"
DEFAULT_MATTERGEN_HOME = "mattergen"
MATTERGEN_HOME_ENV = "MATTERGEN_HOME"
MATTERGEN_GENERATE_BIN_ENV = "MATTERGEN_GENERATE_BIN"
MATTERGEN_MODEL_PATH_ENV = "MATTERGEN_MODEL_PATH"
FORMULA_PATTERN = re.compile(r"([A-Z][a-z]?)(\d*)")
CANDIDATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GPU_ID_PATTERN = re.compile(r"^[0-9]+(?:(?:\s*,\s*|\s+)[0-9]+)*$")
GPU_ID_TOKEN_PATTERN = re.compile(r"[0-9]+")
OBSOLETE_OUTPUT_FILES = ["SAMPLING_READINESS.md", "SAMPLING_MANIFEST.md"]
VALID_ELEMENTS = frozenset(
    {
        "H",
        "He",
        "Li",
        "Be",
        "B",
        "C",
        "N",
        "O",
        "F",
        "Ne",
        "Na",
        "Mg",
        "Al",
        "Si",
        "P",
        "S",
        "Cl",
        "Ar",
        "K",
        "Ca",
        "Sc",
        "Ti",
        "V",
        "Cr",
        "Mn",
        "Fe",
        "Co",
        "Ni",
        "Cu",
        "Zn",
        "Ga",
        "Ge",
        "As",
        "Se",
        "Br",
        "Kr",
        "Rb",
        "Sr",
        "Y",
        "Zr",
        "Nb",
        "Mo",
        "Tc",
        "Ru",
        "Rh",
        "Pd",
        "Ag",
        "Cd",
        "In",
        "Sn",
        "Sb",
        "Te",
        "I",
        "Xe",
        "Cs",
        "Ba",
        "La",
        "Ce",
        "Pr",
        "Nd",
        "Pm",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "Lu",
        "Hf",
        "Ta",
        "W",
        "Re",
        "Os",
        "Ir",
        "Pt",
        "Au",
        "Hg",
        "Tl",
        "Pb",
        "Bi",
        "Po",
        "At",
        "Rn",
        "Fr",
        "Ra",
        "Ac",
        "Th",
        "Pa",
        "U",
        "Np",
        "Pu",
        "Am",
        "Cm",
        "Bk",
        "Cf",
        "Es",
        "Fm",
        "Md",
        "No",
        "Lr",
        "Rf",
        "Db",
        "Sg",
        "Bh",
        "Hs",
        "Mt",
        "Ds",
        "Rg",
        "Cn",
        "Nh",
        "Fl",
        "Mc",
        "Lv",
        "Ts",
        "Og",
    }
)


@dataclass(frozen=True)
class SamplingConfig:
    recommendations_path: Path
    output_dir: Path
    stage_root: Path | None
    round_id: str | None
    generator_bin: Path
    model_path: Path
    batch_size: int
    num_batches: int
    record_trajectories: bool
    gpu_id: str


def default_mattergen_home() -> Path:
    return Path(os.environ.get(MATTERGEN_HOME_ENV, DEFAULT_MATTERGEN_HOME))


def default_generator_bin() -> Path:
    configured = os.environ.get(MATTERGEN_GENERATE_BIN_ENV)
    if configured:
        return Path(configured)
    return default_mattergen_home() / ".venv" / "bin" / "mattergen-generate"


def default_model_path() -> Path:
    configured = os.environ.get(MATTERGEN_MODEL_PATH_ENV)
    if configured:
        return Path(configured)
    return default_mattergen_home() / "checkpoints" / "crystal_structure_prediction"


def ensure_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def read_json_if_present(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def pointer_round_id(stage_root: Path) -> str | None:
    for pointer_name in ("CURRENT_ROUND.json", "LATEST_SUCCESSFUL.json"):
        data = read_json_if_present(stage_root / pointer_name)
        round_id = data.get("round_id")
        if isinstance(round_id, str) and round_id:
            return round_id
    return None


def next_timestamp_round_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")


def paths_equal(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def resolve_round_output_dir(
    output_dir: Path,
    output_root: Path,
    round_id: str | None,
) -> tuple[Path, Path | None, str | None]:
    stage_output_dirs = {
        output_root / DEFAULT_STAGE04_DIRNAME,
        output_root / ZSCHEME_STAGE04_DIRNAME,
    }
    if any(paths_equal(output_dir, stage_dir) for stage_dir in stage_output_dirs):
        stage_root = next(stage_dir for stage_dir in stage_output_dirs if paths_equal(output_dir, stage_dir))
        stage02_dir = output_root / STAGE02_DIRNAME
        effective_round_id = round_id or pointer_round_id(stage02_dir) or next_timestamp_round_id()
        return stage_root / "rounds" / effective_round_id, stage_root, effective_round_id
    return output_dir, None, round_id


def resolve_pointed_path(output_root: Path, value: str) -> Path:
    pointed = Path(value)
    if pointed.is_absolute():
        return pointed
    output_candidate = output_root / pointed
    if output_candidate.exists():
        return output_candidate
    return Path(value)


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
    for pointer_name in ("CURRENT_ROUND.json", "LATEST_SUCCESSFUL.json", "CURRENT_RUN.json"):
        pointer = read_json_if_present(stage_root / pointer_name)
        artifacts = pointer.get("artifacts", {})
        if isinstance(artifacts, dict):
            pointed = artifacts.get(artifact_name)
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


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        pass
    return str(resolved)


def write_round_manifest(config: SamplingConfig, output_dir: Path, status: str, artifact_names: list[str]) -> None:
    artifacts = {name: display_path(output_dir / name) for name in artifact_names if (output_dir / name).exists()}
    manifest = {
        "round_id": config.round_id,
        "round_dir": display_path(output_dir),
        "artifact_layout": "stage/rounds/round_id" if config.round_id else "explicit_output_dir",
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
        "recommendations_path": display_path(config.recommendations_path),
        "artifacts": artifacts,
    }
    (output_dir / "ROUND_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if config.stage_root is None or config.round_id is None:
        return
    config.stage_root.mkdir(parents=True, exist_ok=True)
    for pointer_name in ("CURRENT_ROUND.json", "LATEST_SUCCESSFUL.json"):
        (config.stage_root / pointer_name).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def read_csv_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        rows = [{key: value or "" for key, value in row.items()} for row in reader]
        return rows, list(reader.fieldnames or [])


def parse_formula_to_composition(formula: str) -> dict[str, int] | None:
    compact = formula.strip()
    if not compact:
        return None

    position = 0
    composition: dict[str, int] = {}
    for match in FORMULA_PATTERN.finditer(compact):
        if match.start() != position:
            return None
        element, count_text = match.groups()
        if element not in VALID_ELEMENTS or element in composition:
            return None
        count = int(count_text) if count_text else 1
        if count <= 0:
            return None
        composition[element] = count
        position = match.end()

    if position != len(compact) or not composition:
        return None
    return dict(sorted(composition.items()))


def candidate_id_is_safe(candidate_id: str) -> bool:
    return bool(CANDIDATE_ID_PATTERN.fullmatch(candidate_id))


def normalize_gpu_ids(gpu_id: str) -> str:
    candidate = gpu_id.strip()
    if not GPU_ID_PATTERN.fullmatch(candidate):
        return ""
    return ",".join(GPU_ID_TOKEN_PATTERN.findall(candidate))


def gpu_id_is_safe(gpu_id: str) -> bool:
    return bool(normalize_gpu_ids(gpu_id))


def build_blocked_row(
    candidate_id: str,
    candidate_name: str,
    main_photocatalyst: str,
    formula_note: str,
    sampling_output_dir: str,
    blocked_reasons: list[str],
) -> dict[str, str]:
    recommended_return_step = (
        RETURN_TO_02
        if {"unparseable_formula", "missing_candidate_id", "unsafe_candidate_id"}.intersection(blocked_reasons)
        else ""
    )
    return {
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "main_photocatalyst": main_photocatalyst,
        "main_photocatalyst_formula_note": formula_note,
        "target_composition_json": "",
        "sampling_output_dir": sampling_output_dir,
        "sampling_command": "",
        "sampling_status": "blocked",
        "blocked_reason": ";".join(blocked_reasons),
        "recommended_return_step": recommended_return_step,
    }


def write_plan_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PLAN_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in PLAN_COLUMNS})


def remove_obsolete_outputs(output_dir: Path) -> None:
    for file_name in OBSOLETE_OUTPUT_FILES:
        path = output_dir / file_name
        if path.exists():
            path.unlink()


def render_command(
    sampling_output_dir: str,
    target_composition_json: str,
    config: SamplingConfig,
) -> str:
    gpu_id = normalize_gpu_ids(config.gpu_id)
    return (
        "OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        f"CUDA_VISIBLE_DEVICES={gpu_id} "
        f"{config.generator_bin} {sampling_output_dir} "
        f"--model_path {config.model_path} "
        "--sampling_config_name csp "
        f"--target_compositions='{target_composition_json}' "
        f"--batch_size {config.batch_size} "
        f"--num_batches {config.num_batches} "
        f"--record_trajectories {'True' if config.record_trajectories else 'False'}"
    )


def prepare_sampling_artifacts(
    config: SamplingConfig,
) -> dict[str, int | str | list[str] | None]:
    output_dir = ensure_output_dir(config.output_dir)
    remove_obsolete_outputs(output_dir)
    generator_exists = config.generator_bin.exists()
    model_exists = config.model_path.exists()
    recommendations_exists = config.recommendations_path.exists()
    gpu_id = normalize_gpu_ids(config.gpu_id)

    global_blockers: list[str] = []
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []

    if recommendations_exists:
        rows, fieldnames = read_csv_rows(config.recommendations_path)
    else:
        global_blockers.append(f"missing_recommendations_path:{config.recommendations_path}")

    missing_required_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if recommendations_exists and missing_required_columns:
        global_blockers.append("missing_required_columns:" + ",".join(missing_required_columns))
    if not generator_exists:
        global_blockers.append(f"missing_generator_bin:{config.generator_bin}")
    if not model_exists:
        global_blockers.append(f"missing_model_path:{config.model_path}")
    if not gpu_id:
        global_blockers.append("missing_or_invalid_confirmed_gpu_id")

    sample_root = output_dir / "samples"
    sample_root.mkdir(parents=True, exist_ok=True)

    plan_rows: list[dict[str, str]] = []
    ready_candidate_count = 0
    blocked_candidate_count = 0

    if not global_blockers or rows:
        for raw_row in rows:
            candidate_id = raw_row.get("candidate_id", "").strip()
            candidate_name = raw_row.get("candidate_name", "").strip()
            main_photocatalyst = raw_row.get("main_photocatalyst", "").strip()
            formula_note = raw_row.get("main_photocatalyst_formula_note", "").strip()
            sample_dir = sample_root / candidate_id if candidate_id_is_safe(candidate_id) else sample_root
            if candidate_id_is_safe(candidate_id):
                sample_dir.mkdir(parents=True, exist_ok=True)
            sampling_output_dir = display_path(sample_dir)

            row_blockers = list(global_blockers)
            if missing_required_columns:
                row_blockers = list(global_blockers)
            else:
                if not candidate_id:
                    row_blockers.append("missing_candidate_id")
                elif not candidate_id_is_safe(candidate_id):
                    row_blockers.append("unsafe_candidate_id")
                if not candidate_name:
                    row_blockers.append("missing_candidate_name")
                if not main_photocatalyst:
                    row_blockers.append("missing_main_photocatalyst")

            composition = None
            if not missing_required_columns and main_photocatalyst:
                composition = parse_formula_to_composition(main_photocatalyst)
                if composition is None:
                    row_blockers.append("unparseable_formula")

            if row_blockers:
                blocked_candidate_count += 1
                plan_rows.append(
                    build_blocked_row(
                        candidate_id=candidate_id,
                        candidate_name=candidate_name,
                        main_photocatalyst=main_photocatalyst,
                        formula_note=formula_note,
                        sampling_output_dir=sampling_output_dir,
                        blocked_reasons=row_blockers,
                    )
                )
                continue

            target_composition_json = json.dumps([composition], sort_keys=True)
            sampling_command = render_command(
                sampling_output_dir=sampling_output_dir,
                target_composition_json=target_composition_json,
                config=config,
            )
            ready_candidate_count += 1
            plan_rows.append(
                {
                    "candidate_id": candidate_id,
                    "candidate_name": candidate_name,
                    "main_photocatalyst": main_photocatalyst,
                    "main_photocatalyst_formula_note": formula_note,
                    "target_composition_json": json.dumps(composition, sort_keys=True),
                    "sampling_output_dir": sampling_output_dir,
                    "sampling_command": sampling_command,
                    "sampling_status": READY_TO_SAMPLE,
                    "blocked_reason": "",
                    "recommended_return_step": "",
                }
            )

    status = "ready" if ready_candidate_count > 0 else "blocked"

    write_plan_csv(output_dir / "STRUCTURE_SAMPLING_PLAN.csv", plan_rows)

    parameters = {
        "status": status,
        "round_id": config.round_id,
        "artifact_layout": "stage/rounds/round_id" if config.round_id else "explicit_output_dir",
        "recommendations_path": str(config.recommendations_path),
        "output_dir": str(output_dir),
        "generator_bin": str(config.generator_bin),
        "model_path": str(config.model_path),
        "command_generator_bin": str(config.generator_bin),
        "command_model_path": str(config.model_path),
        "gpu_id": gpu_id,
        "command_gpu_binding": f"CUDA_VISIBLE_DEVICES={gpu_id}",
        "sampling_config_name": "csp",
        "batch_size": config.batch_size,
        "num_batches": config.num_batches,
        "record_trajectories": config.record_trajectories,
        "global_blockers": global_blockers,
        "ready_candidate_count": ready_candidate_count,
        "blocked_candidate_count": blocked_candidate_count,
        "sampling_summary": {
            "status": status,
            "ready_candidate_count": ready_candidate_count,
            "blocked_candidate_count": blocked_candidate_count,
            "global_blockers": global_blockers,
        },
    }
    (output_dir / "SAMPLING_PARAMETERS.json").write_text(json.dumps(parameters, indent=2), encoding="utf-8")

    command_lines = [
        "# 采样命令",
        "",
        "仅对 `ready_to_sample` 的行使用 MatterGen CSP 模式。",
        "",
    ]
    if not plan_rows:
        command_lines.append("没有可用的候选行。")
    for row in plan_rows:
        command_lines.append(f"## {row['candidate_id'] or 'missing-candidate-id'}")
        command_lines.append("")
        if row["sampling_status"] != READY_TO_SAMPLE:
            command_lines.append("- 状态: blocked")
            command_lines.append(f"- 阻塞原因: `{row['blocked_reason']}`")
            if row["recommended_return_step"]:
                command_lines.append(f"- 建议返回步骤: `{row['recommended_return_step']}`")
            command_lines.append("")
            continue
        command_lines.append("```bash")
        command_lines.append(row["sampling_command"])
        command_lines.append("```")
        command_lines.append("")
    (output_dir / "SAMPLING_COMMANDS.md").write_text("\n".join(command_lines), encoding="utf-8")
    artifact_names = ["STRUCTURE_SAMPLING_PLAN.csv", "SAMPLING_PARAMETERS.json", "SAMPLING_COMMANDS.md"]
    write_round_manifest(config, output_dir, status, artifact_names)

    return {
        "status": status,
        "output_dir": str(output_dir),
        "round_id": config.round_id,
        "ready_candidate_count": ready_candidate_count,
        "blocked_candidate_count": blocked_candidate_count,
        "global_blockers": global_blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare MatterGen CSP sampling artifacts under output_root.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--recommendations-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--round-id",
        default=None,
        help="Round identifier used under <stage-dir>/rounds/<round-id>.",
    )
    parser.add_argument("--generator-bin", default=str(default_generator_bin()))
    parser.add_argument("--model-path", default=str(default_model_path()))
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--num-batches", type=int, default=1)
    parser.add_argument("--record-trajectories", action="store_true")
    parser.add_argument("--gpu-id", required=True, help='Confirmed CUDA GPU ID(s), for example 0, 0,1, or "0 1".')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_root = Path(args.output_root)
    recommendations_path = (
        Path(args.recommendations_path)
        if args.recommendations_path
        else output_root / STAGE02_DIRNAME / "RECOMMENDED_CANDIDATES.csv"
    )
    requested_output_dir = Path(args.output_dir) if args.output_dir else output_root / DEFAULT_STAGE04_DIRNAME
    output_dir, stage_root, round_id = resolve_round_output_dir(requested_output_dir, output_root, args.round_id)
    config = SamplingConfig(
        recommendations_path=resolve_artifact_path(recommendations_path, output_root),
        output_dir=output_dir,
        stage_root=stage_root,
        round_id=round_id,
        generator_bin=Path(args.generator_bin),
        model_path=Path(args.model_path),
        batch_size=args.batch_size,
        num_batches=args.num_batches,
        record_trajectories=args.record_trajectories,
        gpu_id=args.gpu_id,
    )
    result = prepare_sampling_artifacts(config)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
