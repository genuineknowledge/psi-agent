"""Tests for the Haitun workspace ``llm_wiki`` toolset.

No network and no extra dependencies: pages are written to a ``tmp_path``
workspace and read back, exercising slugging, frontmatter round-trips,
``[[wikilink]]`` extraction, search ranking, and the link graph.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_llm_wiki_impl")
wiki: Any = importlib.import_module("llm_wiki")


async def _write(tmp_path: Path, title: str, content: str, **kw: Any) -> dict[str, Any]:
    return await impl.wiki_write_impl(title=title, content=content, workspace_raw=str(tmp_path), **kw)


def test_tool_metadata_is_loadable() -> None:
    for fn, expected in (
        (wiki.wiki_write, {"title", "content", "tags", "aliases", "overwrite"}),
        (wiki.wiki_read, {"title_or_slug"}),
        (wiki.wiki_search, {"query", "limit"}),
        (wiki.wiki_list, {"tag"}),
        (wiki.wiki_links, {"title_or_slug"}),
        (wiki.wiki_delete, {"title_or_slug"}),
    ):
        meta = ToolFunction.from_callable(fn)
        assert meta.name == fn.__name__
        assert set(meta.parameters["properties"]) >= expected


def test_slugify() -> None:
    assert impl.slugify("Rotary Positional Embeddings") == "rotary-positional-embeddings"
    assert impl.slugify("  KV-Cache!! ") == "kv-cache"
    assert impl.slugify("###") == "untitled"


def test_extract_links_dedupes_and_handles_labels() -> None:
    body = "See [[Attention]] and [[Attention|self-attention]] plus [[KV Cache]]."
    assert impl.extract_links(body) == ["attention", "kv-cache"]


async def test_write_creates_page_with_frontmatter(tmp_path: Path) -> None:
    result = await _write(tmp_path, "Attention", "Core of [[Transformers]].", tags="core, nlp")
    assert result["ok"] is True
    assert result["slug"] == "attention"
    assert result["created"] is True
    assert result["links"] == ["transformers"]

    page_file = tmp_path / "wiki" / "attention.md"
    text = page_file.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    meta, body = impl._parse_page(text)
    assert meta["title"] == "Attention"
    assert meta["tags"] == ["core", "nlp"]
    assert "[[Transformers]]" in body


async def test_write_no_overwrite_refuses_existing(tmp_path: Path) -> None:
    await _write(tmp_path, "Attention", "v1")
    result = await _write(tmp_path, "Attention", "v2", overwrite=False)
    assert result["ok"] is False
    assert "already exists" in result["message"]


async def test_update_preserves_created_time(tmp_path: Path) -> None:
    first = await impl.wiki_read_impl("Attention", workspace_raw=str(tmp_path))
    await _write(tmp_path, "Attention", "v1")
    created = (await impl.wiki_read_impl("Attention", workspace_raw=str(tmp_path)))["created"]
    await _write(tmp_path, "Attention", "v2 updated")
    after = await impl.wiki_read_impl("Attention", workspace_raw=str(tmp_path))
    assert first["ok"] is False  # did not exist before the first write
    assert after["created"] == created
    assert after["content"] == "v2 updated"


async def test_read_missing_page(tmp_path: Path) -> None:
    result = await impl.wiki_read_impl("Nope", workspace_raw=str(tmp_path))
    assert result["ok"] is False
    assert result["slug"] == "nope"


async def test_write_rejects_blank_title(tmp_path: Path) -> None:
    result = await _write(tmp_path, "   ", "body")
    assert result["ok"] is False


async def test_list_and_tag_filter(tmp_path: Path) -> None:
    await _write(tmp_path, "Attention", "a", tags="core")
    await _write(tmp_path, "Tokenizer", "t", tags="preprocessing")
    all_pages = await impl.wiki_list_impl(workspace_raw=str(tmp_path))
    assert all_pages["count"] == 2
    assert {p["slug"] for p in all_pages["pages"]} == {"attention", "tokenizer"}

    filtered = await impl.wiki_list_impl(tag="core", workspace_raw=str(tmp_path))
    assert filtered["count"] == 1
    assert filtered["pages"][0]["slug"] == "attention"


async def test_list_empty_when_no_wiki(tmp_path: Path) -> None:
    result = await impl.wiki_list_impl(workspace_raw=str(tmp_path))
    assert result["ok"] is True
    assert result["count"] == 0


async def test_search_ranks_title_above_body(tmp_path: Path) -> None:
    await _write(tmp_path, "Attention", "mentions attention twice: attention.")
    await _write(tmp_path, "Transformer", "built on the attention mechanism.")
    result = await impl.wiki_search_impl("attention", workspace_raw=str(tmp_path))
    assert result["ok"] is True
    assert result["results"][0]["slug"] == "attention"
    assert result["count"] == 2
    assert result["results"][0]["snippet"]


async def test_search_matches_aliases(tmp_path: Path) -> None:
    await _write(tmp_path, "Rotary Positional Embeddings", "positional info", aliases="RoPE")
    result = await impl.wiki_search_impl("rope", workspace_raw=str(tmp_path))
    assert result["count"] == 1
    assert result["results"][0]["slug"] == "rotary-positional-embeddings"


async def test_search_requires_query(tmp_path: Path) -> None:
    result = await impl.wiki_search_impl("  ", workspace_raw=str(tmp_path))
    assert result["ok"] is False


async def test_links_graph_backlinks_and_broken(tmp_path: Path) -> None:
    await _write(tmp_path, "Transformer", "uses [[Attention]] and [[Missing Page]].")
    await _write(tmp_path, "Attention", "the core mechanism.")
    graph = await impl.wiki_links_impl("Transformer", workspace_raw=str(tmp_path))
    assert graph["ok"] is True
    assert graph["outgoing"] == ["attention", "missing-page"]
    assert graph["broken"] == ["missing-page"]

    back = await impl.wiki_links_impl("Attention", workspace_raw=str(tmp_path))
    assert back["backlinks"] == ["transformer"]


async def test_delete_reports_orphaned_backlinks(tmp_path: Path) -> None:
    await _write(tmp_path, "Transformer", "uses [[Attention]].")
    await _write(tmp_path, "Attention", "core.")
    result = await impl.wiki_delete_impl("Attention", workspace_raw=str(tmp_path))
    assert result["ok"] is True
    assert result["deleted"] is True
    assert result["orphaned_backlinks"] == ["transformer"]
    assert not (tmp_path / "wiki" / "attention.md").exists()

    missing = await impl.wiki_delete_impl("Attention", workspace_raw=str(tmp_path))
    assert missing["ok"] is False


async def test_tool_shell_returns_json(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    raw = await wiki.wiki_write("Scaling Laws", "Loss scales with [[Compute]].", tags="theory")
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["slug"] == "scaling-laws"

    read_raw = await wiki.wiki_read("scaling-laws")
    assert json.loads(read_raw)["content"] == "Loss scales with [[Compute]]."
