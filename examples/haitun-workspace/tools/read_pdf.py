"""Read a PDF (including scanned / image-only PDFs) into text."""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _pdf_ocr as _p


async def read_pdf(
    pdf_path: str,
    pages: str = "",
    max_pages: int = 20,
    force_ocr: bool = False,
) -> str:
    """Read a PDF into text — handles both digital and scanned/image-only PDFs.

    Use this instead of ``read`` for PDF files. ``read`` returns raw bytes and
    cannot see text inside scanned pages or images.

    Per page it first extracts the embedded **text layer** (instant, free — works
    for digital-born PDFs). Pages with little/no text layer (scans, photos of
    documents) are rendered to images and OCR'd via the **MiniMax-M3** vision API
    (BYOK, same config as ``describe_image``). Set ``force_ocr=true`` to OCR every
    page even when a text layer exists (useful when the text layer is garbled).

    Vision OCR needs credentials in the workspace ``.env.multimodal``
    (``MINIMAX_API_KEY``; optional ``MINIMAX_API_HOST``, ``VISION_MODEL``). If they
    are unset, digital pages still return text and image pages report the gap.

    Args:
        pdf_path: Absolute path to a ``.pdf`` file (max 100 MB).
        pages: 1-indexed page selection like ``"1-3,5,8"``; empty = all pages.
        max_pages: Cap on pages processed (default 20, hard max 100) to bound cost.
        force_ocr: OCR every selected page via vision even if a text layer exists.

    Returns:
        JSON with ok, text (concatenated, page-delimited), pages_processed,
        total_pages, backend, message, pdf_path, and a per-page list
        (page, source ["text-layer"|"vision-ocr"|"error"], chars, message).
    """
    result = await _p.read_pdf_impl(
        pdf_path=pdf_path,
        pages=pages,
        max_pages=max_pages,
        force_ocr=force_ocr,
    )
    return _p.dumps_result(result)
