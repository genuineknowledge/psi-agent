"""User-visible Markdown persistence for materialized G4 artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

import anyio
from loguru import logger

from ._atomic_io import atomic_write_text

_PORTABLE_FILENAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_FILENAME_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_WINDOWS_RESERVED_NAME = re.compile(
    r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])",
    re.IGNORECASE,
)


class ArtifactStore:
    """Persist each materialized Artifact as one Markdown file."""

    def __init__(self, run_dir: anyio.Path, artifacts_dir: anyio.Path) -> None:
        self.run_dir = run_dir
        self.artifacts_dir = artifacts_dir
        self._persisted_ids: set[str] = set()

    @classmethod
    async def open(
        cls,
        bundle_dir: anyio.Path,
        run_id: str,
        *,
        reuse_existing: bool,
    ) -> ArtifactStore:
        """Create or reopen one workflow-local run directory."""

        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ValueError("run_id must be 32 lowercase hexadecimal characters")

        bundle = await bundle_dir.resolve()
        runs_dir = await _regular_directory(
            bundle / "runs",
            boundary=bundle,
            exist_ok=True,
        )
        run_dir = await _regular_directory(
            runs_dir / run_id,
            boundary=runs_dir,
            exist_ok=reuse_existing,
        )
        artifacts_dir = await _regular_directory(
            run_dir / "artifacts",
            boundary=run_dir,
            exist_ok=True,
        )
        logger.debug(f"FusionFlow Artifact store ready: {artifacts_dir!r}")
        return cls(run_dir, artifacts_dir)

    async def persist(self, values: Mapping[str, object]) -> None:
        """Atomically write every newly materialized Artifact value."""

        written = 0
        for artifact_id in sorted(values):
            if artifact_id in self._persisted_ids:
                continue
            target = self.artifacts_dir / _artifact_filename(artifact_id)
            await atomic_write_text(target, _render_markdown(values[artifact_id]))
            self._persisted_ids.add(artifact_id)
            written += 1
        if written:
            logger.debug(f"Persisted {written} FusionFlow Artifact file(s) under {self.artifacts_dir!r}")


def _artifact_filename(artifact_id: str) -> str:
    """Return a readable, collision-safe filename for one G4 Artifact ID."""

    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("FusionFlow Artifact ID for persistence must be a non-empty string")
    if _PORTABLE_FILENAME_PATTERN.fullmatch(artifact_id) is not None and (
        _WINDOWS_RESERVED_NAME.fullmatch(artifact_id) is None
    ):
        return f"{artifact_id}.md"

    digest = hashlib.sha256(artifact_id.encode()).hexdigest()[:16]
    slug = _FILENAME_SLUG_PATTERN.sub("-", artifact_id.casefold()).strip("-")[:48].rstrip("-")
    if not slug or _WINDOWS_RESERVED_NAME.fullmatch(slug) is not None:
        slug = "artifact"
    return f"{slug}--{digest}.md"


def _render_markdown(value: object) -> str:
    """Keep textual Markdown verbatim and fence structured JSON values."""

    if isinstance(value, str):
        return value
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    return f"```json\n{payload}\n```\n"


async def _regular_directory(
    path: anyio.Path,
    *,
    boundary: anyio.Path,
    exist_ok: bool,
) -> anyio.Path:
    """Create one regular directory and reject symlink escapes."""

    await path.mkdir(parents=True, exist_ok=exist_ok)
    if await path.is_symlink() or not await path.is_dir():
        raise ValueError(f"FusionFlow Artifact path is not a regular directory: {path!r}")
    resolved = await path.resolve()
    boundary_resolved = await boundary.resolve()
    if not Path(str(resolved)).is_relative_to(Path(str(boundary_resolved))):
        raise ValueError(f"FusionFlow Artifact path escapes its workflow directory: {path!r}")
    return resolved
