from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from fusion_flow.artifact_store import (
    ArtifactStore,
    _artifact_filename,
    _render_markdown,
)


def test_artifact_filename_keeps_common_ids_readable_and_avoids_windows_collisions() -> None:
    assert _artifact_filename("draft") == "draft.md"
    assert _artifact_filename("final_report") == "final_report.md"
    assert _artifact_filename("con").startswith("artifact--")
    assert _artifact_filename("draftV2").startswith("draftv2--")
    assert _artifact_filename("../escape").startswith("escape--")
    assert _artifact_filename("审阅结果").startswith("artifact--")
    assert "/" not in _artifact_filename("../escape")
    filenames = [_artifact_filename(artifact_id) for artifact_id in ("foo", "Foo", "CON", "a?b", "é", "e\u0301")]
    assert len({filename.casefold() for filename in filenames}) == len(filenames)

    with pytest.raises(ValueError, match="non-empty string"):
        _artifact_filename("")


def test_structured_artifacts_render_as_json_fenced_markdown() -> None:
    assert _render_markdown("# Review\n\nShip it.") == "# Review\n\nShip it."
    assert _render_markdown({"score": 5, "approved": True}) == (
        '```json\n{\n  "approved": true,\n  "score": 5\n}\n```\n'
    )


@pytest.mark.anyio
async def test_artifact_store_persists_each_materialized_value_atomically(tmp_path: Path) -> None:
    bundle = anyio.Path(tmp_path / "flows" / "review")
    await bundle.mkdir(parents=True)
    store = await ArtifactStore.open(
        bundle,
        "a" * 32,
        reuse_existing=False,
    )

    await store.persist(
        {
            "request": "# Request\n\nReview this change.",
            "draft": {"score": 5, "approved": True},
        }
    )
    await store.persist(
        {
            "request": "# Request\n\nThis changed value must not overwrite the first assignment.",
            "draft": {"score": 5, "approved": True},
            "result": "Ship it.",
        }
    )

    assert await (store.artifacts_dir / "request.md").read_text(encoding="utf-8") == (
        "# Request\n\nReview this change."
    )
    assert await (store.artifacts_dir / "draft.md").read_text(encoding="utf-8") == (
        '```json\n{\n  "approved": true,\n  "score": 5\n}\n```\n'
    )
    assert await (store.artifacts_dir / "result.md").read_text(encoding="utf-8") == "Ship it."
    assert not [entry async for entry in store.artifacts_dir.iterdir() if entry.name.endswith(".tmp")]


@pytest.mark.anyio
async def test_artifact_store_preserves_text_bytes_verbatim(tmp_path: Path) -> None:
    bundle = anyio.Path(tmp_path / "flows" / "review")
    await bundle.mkdir(parents=True)
    store = await ArtifactStore.open(
        bundle,
        "b" * 32,
        reuse_existing=False,
    )

    await store.persist({"result": "第一行\r\n第二行"})

    assert await (store.artifacts_dir / "result.md").read_bytes() == "第一行\r\n第二行".encode()
