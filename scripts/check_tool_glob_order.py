"""Measure how far this filesystem's ``glob`` order is from sorted order.

``tool_registry._exec_tool_files`` sorts ``glob("*.py")`` by file name because
load order is a hidden input: files carrying their own ``sys.path`` preamble
have to precede the files relying on it (59 tool files once failed to load
because they did not).  That fix is about a *cross-filesystem* difference, and
it was only ever measured on Windows/NTFS (feishu 16 of 134 names misplaced,
desktop 13 of 65).  This reports the same number for whatever filesystem it
runs on, so CI can state it for ext4 rather than assuming.

Three shapes are measured, because one number cannot answer the question:

* ``repo`` — the real tool dirs as this checkout produced them.  Git writes
  files in index order (sorted), so on a filesystem that returns small
  directories in creation order this shape reads 0 even though the filesystem
  does not guarantee sorted order for anything else.
* ``fresh`` — the same file names created in a deliberately non-sorted order.
  Separates "the filesystem sorts" from "the creation order happened to be
  sorted": only the former keeps this at 0.
* ``churned`` — ``fresh`` after deleting and re-creating a slice of the files,
  the shape a directory takes after edits and renames.  Production tool dirs
  are edited in place, so this is closer to them than a virgin checkout.

Two metrics per shape.  *Misplaced* counts positions where raw and sorted
disagree; it is the metric the NTFS numbers above used.  *Inversions* counts
pairs whose relative order is flipped, which is what load order actually cares
about — "A before B" is the property a preamble file needs.

Usage::

    python scripts/check_tool_glob_order.py

Reports only; the exit code is 0 whatever the skew, because a skew of 0 is a
legitimate finding about a filesystem and not a failure of this repository.
Exits non-zero only if it could not measure (missing tool dirs).
"""

# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The dirs whose NTFS skew is quoted in src/psi_agent/session/AGENTS.md.
TOOL_DIRS = ("agents/feishu/tools", "agents/desktop/tools")


def _glob_names(directory: Path) -> list[str]:
    """Raw ``glob("*.py")`` order, filtered the way the scan filters.

    ``pathlib.Path.glob`` does not sort; it yields in ``os.scandir`` order,
    which is the filesystem's own order.  Underscore-prefixed files are
    dropped because ``_exec_tool_files`` skips them, so they take no position
    in load order.
    """
    return [path.name for path in directory.glob("*.py") if not path.name.startswith("_")]


def _misplaced(raw: list[str]) -> int:
    """Positions where raw order and sorted order hold different names."""
    return sum(1 for got, want in zip(raw, sorted(raw), strict=True) if got != want)


def _inversions(raw: list[str]) -> int:
    """Pairs read in the opposite of their sorted relative order.

    O(n^2) over ~200 names, which is nothing, and the naive form is the one
    that obviously matches the definition.
    """
    return sum(1 for i, first in enumerate(raw) for second in raw[i + 1 :] if first > second)


def _report(shape: str, label: str, raw: list[str]) -> int:
    misplaced = _misplaced(raw)
    print(f"  {shape:<8} {label:<22} files={len(raw):<4} misplaced={misplaced:<4} inversions={_inversions(raw)}")
    if misplaced:
        first = next((got, want) for got, want in zip(raw, sorted(raw), strict=True) if got != want)
        print(f"           first divergence: glob gave {first[0]!r} where sorted wants {first[1]!r}")
    return misplaced


def _write_names(directory: Path, names: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text("x\n", encoding="utf-8")


def _fs_type(path: Path) -> str:
    """Best-effort filesystem name, for the record in the CI log."""
    try:
        out = subprocess.run(
            ["df", "--output=fstype", str(path)], capture_output=True, text=True, timeout=10, check=False
        )
    except OSError, subprocess.SubprocessError:
        return "unknown"
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return lines[-1] if len(lines) > 1 else "unknown"


def main() -> int:
    print(f"platform: {platform.system()} {platform.release()}  python: {sys.version.split()[0]}")
    print(f"filesystem (repo): {_fs_type(REPO_ROOT)}  filesystem (tmp): {_fs_type(Path(tempfile.gettempdir()))}")

    missing = [name for name in TOOL_DIRS if not (REPO_ROOT / name).is_dir()]
    if missing:
        print(f"ERROR: tool dirs not found: {missing}")
        return 1

    total = 0
    for name in TOOL_DIRS:
        directory = REPO_ROOT / name
        names = _glob_names(directory)
        print(f"{name}:")
        total += _report("repo", "as checked out", names)

        with tempfile.TemporaryDirectory() as tmp:
            # Reverse-sorted creation order: any filesystem that returns
            # creation order will visibly report the maximum skew here, which
            # is what distinguishes it from one that sorts or hashes.
            fresh = Path(tmp) / "fresh"
            _write_names(fresh, sorted(names, reverse=True))
            total += _report("fresh", "created reverse-sorted", _glob_names(fresh))

            # Churn: drop every third name and re-append it, so the directory
            # carries holes and late entries the way an edited dir does.
            churned = Path(tmp) / "churned"
            _write_names(churned, sorted(names))
            recycled = sorted(names)[::3]
            for entry in recycled:
                (churned / entry).unlink()
            for entry in recycled:
                (churned / entry).write_text("y\n", encoding="utf-8")
            total += _report("churned", f"{len(recycled)} names recreated", _glob_names(churned))

    print(f"total misplaced positions across all shapes: {total}")
    print(
        "A total of 0 would mean sorted() changes nothing on this filesystem; "
        "any non-zero shape means glob order is a live input here."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
