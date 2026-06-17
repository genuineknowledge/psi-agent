"""PDF tool — extract text content from PDF files."""

from __future__ import annotations


async def pdf(file_path: str, pages: str = "") -> str:
    """Extract text content from a PDF file.

    Tries pdfminer.six first, then pypdf as fallback.

    Args:
        file_path: Path to the PDF file.
        pages: Optional page range, e.g. "1-3" or "2". Defaults to all pages.

    Returns:
        Extracted text content, or an error with install instructions.
    """
    page_range = _parse_page_range(pages)

    # Try pdfminer.six
    try:
        from pdfminer.high_level import extract_text  # type: ignore[import-untyped]

        if page_range:
            start, end = page_range
            text = extract_text(file_path, page_numbers=list(range(start - 1, end)))
        else:
            text = extract_text(file_path)
        return text.strip() or "(empty PDF)"
    except ImportError:
        pass
    except Exception as e:
        return f"[Error] pdfminer failed: {e}"

    # Try pypdf
    try:
        from pypdf import PdfReader  # type: ignore[import-untyped]

        reader = PdfReader(file_path)
        total = len(reader.pages)
        if page_range:
            start, end = page_range
            page_indices = range(start - 1, min(end, total))
        else:
            page_indices = range(total)
        parts = [reader.pages[i].extract_text() or "" for i in page_indices]
        text = "\n".join(parts).strip()
        return text or "(empty PDF)"
    except ImportError:
        pass
    except Exception as e:
        return f"[Error] pypdf failed: {e}"

    return (
        "[Error] No PDF library found. Install one:\n"
        "  uv add pdfminer.six   # recommended\n"
        "  uv add pypdf          # alternative"
    )


def _parse_page_range(pages: str) -> tuple[int, int] | None:
    """Parse a page range string like '1-3' or '2' into (start, end) tuple."""
    pages = pages.strip()
    if not pages:
        return None
    if "-" in pages:
        parts = pages.split("-", 1)
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            return None
    try:
        n = int(pages)
        return (n, n)
    except ValueError:
        return None
