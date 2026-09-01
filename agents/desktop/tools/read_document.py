"""Read local Office documents into grounded plain text."""

from __future__ import annotations

from pathlib import Path

import _runtime_paths as _paths
from anyio import to_thread
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


def _escape_cell(value: str) -> str:
    return " ".join(value.split()).replace("|", r"\|")


def _table_text(table: Table) -> str:
    rows = [[_escape_cell(cell.text) for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def _read_docx(path: Path) -> str:
    document = Document(str(path))
    blocks: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
        elif isinstance(item, Table):
            text = _table_text(item)
        else:  # pragma: no cover
            continue
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


async def read_document(file_path: str, max_chars: int = 50000) -> str:
    """Read a local Word ``.docx`` as plain text, preserving table order.

    Use this instead of ``read`` for local ``.docx`` files. The generic
    ``read`` tool treats Office containers as UTF-8 text and returns ZIP binary
    noise. This tool extracts paragraphs and tables without modifying the
    source document.

    Args:
        file_path: Absolute path, or a path relative to the Session workspace.
        max_chars: Maximum returned characters (1,000 to 200,000).

    Returns:
        Extracted text prefixed with the exact resolved source path. Errors are
        explicit and never return guessed document content.
    """
    path = _paths.resolve_user_path(file_path)
    if not await path.exists():
        return f"[Error] File not found: {path}"
    if not await path.is_file():
        return f"[Error] Not a file: {path}"
    if path.suffix.lower() != ".docx":
        return f"[Error] Unsupported document type {path.suffix!r}; read_document currently supports .docx"

    limit = max(1000, min(int(max_chars), 200000))
    try:
        text = await to_thread.run_sync(_read_docx, Path(str(path)))
    except Exception as exc:
        return f"[Error] Could not parse DOCX {path}: {type(exc).__name__}: {exc}"

    if not text.strip():
        return f"[Error] No extractable text found in DOCX: {path}"
    if len(text) > limit:
        text = text[:limit] + f"\n\n[Truncated at {limit} characters]"
    note = (
        "[Extraction: paragraphs and tables in document order; embedded images, "
        "drawings, and text boxes are not interpreted]"
    )
    return f"[Source: {path}]\n{note}\n\n{text}"
