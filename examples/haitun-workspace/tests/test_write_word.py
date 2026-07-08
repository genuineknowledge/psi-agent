"""Tests for the Haitun workspace ``write_word`` tool.

The tool's whole reason to exist is the "字体不齐" font bug: a .docx must set the
East-Asian font (``w:eastAsia``), not just the Latin font, or Word renders CJK
glyphs in a fallback typeface. These tests build real .docx files (python-docx
is a hard dependency of the tool) and inspect the OOXML to prove ``w:eastAsia``
is set on the base styles and that ``w:rFonts`` is ordered first per the schema.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tool: Any = importlib.import_module("write_word")


def _styles_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/styles.xml").decode("utf-8")


def _document_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


def _style_rpr(styles_xml: str, style_id: str) -> str | None:
    """Return the ``<w:rPr>…</w:rPr>`` block for a given styleId, or None."""
    m = re.search(rf'w:styleId="{style_id}".*?</w:style>', styles_xml, re.S)
    if not m:
        return None
    rp = re.search(r"<w:rPr>.*?</w:rPr>", m.group(0), re.S)
    return rp.group(0) if rp else None


async def test_sets_eastasia_font_on_base_styles(tmp_path: Path) -> None:
    out = tmp_path / "report.docx"
    blocks = [
        {"type": "heading", "level": 1, "text": "概述"},
        {"type": "paragraph", "text": "PyTorch 将在未来五年继续稳坐深度学习框架的王座"},
    ]
    result = await tool.write_word(str(out), json.dumps(blocks), title="2026 报告", cjk_font="微软雅黑")

    assert result.startswith("[OK]")
    assert out.exists()

    styles = _styles_xml(out)
    # Normal, Heading 1, and Title must all carry the East-Asian font.
    for style_id in ("Normal", "Heading1", "Title"):
        rpr = _style_rpr(styles, style_id)
        assert rpr is not None, f"missing rPr for {style_id}"
        assert 'w:eastAsia="微软雅黑"' in rpr, f"{style_id} missing w:eastAsia (would cause 字体不齐)"
        # w:rFonts must be the first child of w:rPr (OOXML schema order).
        first = re.search(r"<w:rPr>\s*<([\w:]+)", rpr)
        assert first is not None and first.group(1) == "w:rFonts", f"{style_id} rFonts not first"


async def test_renders_blocks_and_table(tmp_path: Path) -> None:
    out = tmp_path / "r.docx"
    blocks = [
        {"type": "heading", "level": 2, "text": "生态系统"},
        {"type": "paragraph", "text": "护城河"},
        {"type": "table", "rows": [["月份", "收入"], ["1月", "100"]]},
        {"type": "page_break"},
    ]
    result = await tool.write_word(str(out), json.dumps(blocks))

    assert result == f"[OK] Wrote 4 block(s) to {out}"
    doc_xml = _document_xml(out)
    assert "生态系统" in doc_xml
    assert "护城河" in doc_xml
    assert "月份" in doc_xml and "收入" in doc_xml


async def test_appends_docx_extension(tmp_path: Path) -> None:
    out = tmp_path / "noext"
    result = await tool.write_word(str(out), json.dumps([{"type": "paragraph", "text": "hi"}]))
    assert result.startswith("[OK]")
    assert (tmp_path / "noext.docx").exists()


async def test_rejects_bad_json(tmp_path: Path) -> None:
    result = await tool.write_word(str(tmp_path / "x.docx"), "{not json")
    assert result.startswith("[Error]")
    assert "valid JSON" in result


async def test_rejects_non_array_json(tmp_path: Path) -> None:
    result = await tool.write_word(str(tmp_path / "x.docx"), '{"type": "paragraph"}')
    assert result.startswith("[Error]")


async def test_rejects_empty_without_title(tmp_path: Path) -> None:
    result = await tool.write_word(str(tmp_path / "x.docx"), "[]")
    assert result.startswith("[Error]")


async def test_unknown_block_type_is_reported(tmp_path: Path) -> None:
    result = await tool.write_word(str(tmp_path / "x.docx"), json.dumps([{"type": "bogus"}]))
    assert result.startswith("[Error]")
    assert "bogus" in result
