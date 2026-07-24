"""Image-PDF reading helper — text-layer extraction + MiniMax-M3 vision OCR (BYOK).

Strategy per page:
  1. Extract the embedded text layer via PyMuPDF (``page.get_text``). Digital-born
     PDFs return usable text here — free and instant.
  2. If a page has little/no text (scanned / image-only PDF), render it to a PNG
     via ``page.get_pixmap`` and OCR it through the MiniMax vision API.

Reuses the vision credentials / host resolution from ``_multimodal_env`` and the
URL / header / thinking-strip helpers from ``_minimax`` (same BYOK config as
``describe_image``: workspace ``.env.multimodal``, ``MINIMAX_API_KEY``).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import _minimax as _mx
import _multimodal_env as _env
import aiohttp
import anyio
import pymupdf
from loguru import logger

_MAX_PDF_BYTES = 100 * 1024 * 1024
_DEFAULT_MAX_PAGES = 20
_HARD_MAX_PAGES = 100
# Pages with fewer than this many non-whitespace characters in their text layer
# are treated as image-only and sent to vision OCR.
_TEXT_LAYER_MIN_CHARS = 20
# Render scale: 2.0 ≈ 144 DPI, a good balance of legibility vs. payload size.
_RENDER_ZOOM = 2.0
_MAX_VISION_CONCURRENCY = 3
_DEFAULT_OCR_PROMPT = (
    "Transcribe all text in this page image exactly, preserving reading order, "
    "line breaks, and tables as best you can. Output only the transcribed text."
)
_BACKEND = _mx.PROVIDER_NAME


@dataclass
class PageResult:
    page_number: int  # 1-indexed
    source: str  # "text-layer" | "vision-ocr" | "error"
    text: str
    message: str = "OK"


@dataclass
class PdfResult:
    ok: bool
    text: str
    pages_processed: int
    total_pages: int
    backend: str
    message: str
    pdf_path: str
    pages: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "text": self.text,
            "pages_processed": self.pages_processed,
            "total_pages": self.total_pages,
            "backend": self.backend,
            "message": self.message,
            "pdf_path": self.pdf_path,
            "pages": self.pages,
        }


def dumps_result(result: PdfResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)


def _parse_page_range(pages: str, total: int) -> list[int]:
    """Parse a 1-indexed page spec like ``"1-3,5,8"`` into sorted 0-indexed ints.

    Empty string means "all pages". Out-of-range values are clamped away.
    """
    spec = pages.strip()
    if not spec:
        return list(range(total))
    wanted: set[int] = set()
    for chunk in spec.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            try:
                lo = int(lo_s)
                hi = int(hi_s)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            for p in range(lo, hi + 1):
                if 1 <= p <= total:
                    wanted.add(p - 1)
        else:
            try:
                p = int(part)
            except ValueError:
                continue
            if 1 <= p <= total:
                wanted.add(p - 1)
    return sorted(wanted)


async def _ocr_page_image(
    *,
    png_bytes: bytes,
    prompt: str,
    cfg: _env.VisionApiConfig,
    page_number: int,
) -> PageResult:
    data_url = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
    payload = {
        "model": cfg.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "default"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 4096,
    }
    url = _mx.chat_completions_url(cfg.api_host)
    headers = _mx.auth_headers(cfg.api_key)

    try:
        timeout = aiohttp.ClientTimeout(total=180)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, headers=headers, json=payload) as resp,
        ):
            body = await resp.text()
            if resp.status >= 400:
                logger.error(f"MiniMax OCR HTTP {resp.status} p{page_number}: {body[:300]}")
                return PageResult(
                    page_number=page_number,
                    source="error",
                    text="",
                    message=f"vision API HTTP {resp.status}: {body[:200]}",
                )
            data = json.loads(body)
    except json.JSONDecodeError as e:
        return PageResult(page_number, "error", "", f"vision API invalid JSON: {e}")
    except Exception as e:
        logger.error(f"MiniMax OCR request failed p{page_number}: {e!r}")
        return PageResult(page_number, "error", "", f"vision API request failed: {e!r}")

    try:
        content = data["choices"][0]["message"]["content"]
    except KeyError, IndexError, TypeError:
        return PageResult(page_number, "error", "", "vision API response shape invalid")
    if not isinstance(content, str) or not content.strip():
        return PageResult(page_number, "error", "", "vision API returned empty content")

    text = _mx.strip_thinking(content)
    if not text:
        return PageResult(page_number, "error", "", "vision API returned only thinking content")
    return PageResult(page_number, "vision-ocr", text)


def _render_page_png(doc: pymupdf.Document, page_index: int) -> bytes:
    """Render a single page to PNG bytes at ``_RENDER_ZOOM``. Blocking (CPU)."""
    page = doc[page_index]
    matrix = pymupdf.Matrix(_RENDER_ZOOM, _RENDER_ZOOM)
    pixmap = page.get_pixmap(matrix=matrix)
    return pixmap.tobytes("png")


def _extract_text_layer(doc: pymupdf.Document, page_index: int) -> str:
    """Extract the embedded text layer for a page. Blocking."""
    return doc[page_index].get_text("text").strip()


async def read_pdf_impl(
    pdf_path: str,
    pages: str = "",
    max_pages: int = _DEFAULT_MAX_PAGES,
    force_ocr: bool = False,
    workspace_raw: str = "",
) -> PdfResult:
    _env.apply_workspace_env_file(workspace_raw)

    raw_path = pdf_path.strip()
    if not raw_path:
        return PdfResult(False, "", 0, 0, "", "pdf_path must be non-empty", "", [])
    path = anyio.Path(raw_path)
    if not await path.exists():
        return PdfResult(False, "", 0, 0, "", f"pdf not found: {raw_path}", raw_path, [])
    if not await path.is_file():
        return PdfResult(False, "", 0, 0, "", f"not a file: {raw_path}", raw_path, [])
    if path.suffix.lower() != ".pdf":
        return PdfResult(False, "", 0, 0, "", "only .pdf files are supported", raw_path, [])

    stat = await path.stat()
    if stat.st_size > _MAX_PDF_BYTES:
        return PdfResult(
            False,
            "",
            0,
            0,
            "",
            f"pdf too large ({stat.st_size} bytes; max {_MAX_PDF_BYTES})",
            raw_path,
            [],
        )

    resolved = str(await path.resolve())

    try:
        doc = await anyio.to_thread.run_sync(pymupdf.open, resolved)  # ty: ignore
    except Exception as e:
        logger.error(f"read_pdf: failed to open {resolved}: {e!r}")
        return PdfResult(False, "", 0, 0, "", f"failed to open pdf: {e!r}", resolved, [])

    try:
        total_pages = doc.page_count
        if total_pages == 0:
            return PdfResult(False, "", 0, 0, "", "pdf has no pages", resolved, [])

        selected = _parse_page_range(pages, total_pages)
        if not selected:
            return PdfResult(
                False,
                "",
                0,
                total_pages,
                "",
                f"no valid pages in range {pages!r} (pdf has {total_pages} pages)",
                resolved,
                [],
            )

        cap = max(1, min(max_pages if max_pages > 0 else _DEFAULT_MAX_PAGES, _HARD_MAX_PAGES))
        requested_count = len(selected)
        truncated = requested_count > cap
        selected = selected[:cap]

        # Decide per page whether the text layer suffices or vision OCR is needed.
        need_ocr: list[int] = []
        page_texts: dict[int, str] = {}
        page_meta_by_idx: dict[int, PageResult] = {}
        for idx in selected:
            if force_ocr:
                need_ocr.append(idx)
                continue
            layer = await anyio.to_thread.run_sync(_extract_text_layer, doc, idx)  # ty: ignore
            if len(layer) >= _TEXT_LAYER_MIN_CHARS:
                page_texts[idx] = layer
            else:
                need_ocr.append(idx)

        vision_used = False
        if need_ocr:
            cfg = _env.read_vision_api_config()
            if not cfg.ready:
                # Text-layer pages still succeed; image pages report the config gap.
                if not page_texts:
                    return PdfResult(
                        False,
                        "",
                        0,
                        total_pages,
                        "",
                        cfg.not_ready_message(),
                        resolved,
                        [],
                    )
                for idx in need_ocr:
                    page_meta_by_idx[idx] = PageResult(idx + 1, "error", "", cfg.not_ready_message())
            else:
                vision_used = True
                results = await _ocr_pages(doc, need_ocr, cfg)
                for res in results:
                    if res.source == "error":
                        logger.warning(f"read_pdf OCR page {res.page_number}: {res.message}")
                page_meta_by_idx = {r.page_number - 1: r for r in results}
    finally:
        await anyio.to_thread.run_sync(doc.close)  # ty: ignore

    # Assemble ordered output.
    page_dicts: list[dict[str, Any]] = []
    parts: list[str] = []
    for idx in selected:
        pnum = idx + 1
        if idx in page_meta_by_idx:
            meta = page_meta_by_idx[idx]
            src, msg = meta.source, meta.message
            txt = meta.text if src == "vision-ocr" else ""
        else:
            src, msg, txt = "text-layer", "OK", page_texts.get(idx, "")
        page_dicts.append({"page": pnum, "source": src, "chars": len(txt), "message": msg})
        parts.append(f"--- page {pnum} ({src}) ---\n{txt}".rstrip())

    combined = "\n\n".join(parts).strip()
    backend = _BACKEND if vision_used else "text-layer"
    msg = "OK"
    if truncated:
        msg = f"OK (truncated to first {cap} of {requested_count} requested pages)"
    return PdfResult(
        ok=True,
        text=combined,
        pages_processed=len(selected),
        total_pages=total_pages,
        backend=backend,
        message=msg,
        pdf_path=resolved,
        pages=page_dicts,
    )


async def _ocr_pages(
    doc: pymupdf.Document,
    page_indices: list[int],
    cfg: _env.VisionApiConfig,
) -> list[PageResult]:
    """Render + OCR the given pages, bounded concurrency, preserving order."""
    limiter = anyio.CapacityLimiter(_MAX_VISION_CONCURRENCY)
    results: dict[int, PageResult] = {}

    async def _one(idx: int) -> None:
        async with limiter:
            try:
                png = await anyio.to_thread.run_sync(_render_page_png, doc, idx)  # ty: ignore
            except Exception as e:
                logger.error(f"read_pdf: render page {idx + 1} failed: {e!r}")
                results[idx] = PageResult(idx + 1, "error", "", f"render failed: {e!r}")
                return
            results[idx] = await _ocr_page_image(
                png_bytes=png, prompt=_DEFAULT_OCR_PROMPT, cfg=cfg, page_number=idx + 1
            )

    async with anyio.create_task_group() as tg:
        for idx in page_indices:
            tg.start_soon(_one, idx)

    return [results[idx] for idx in page_indices if idx in results]
