---
name: ocr-and-documents
description: "Extract text from PDFs, scans, and images. Two tiers: (1) fast, dependency-free text-LAYER extraction with PyMuPDF (import fitz) — already a core dependency — for born-digital PDFs that already contain selectable text; and (2) high-accuracy OCR + structured layout (Markdown/JSON with headings, tables, math, reading order) via the external marker-pdf CLI (marker_single / marker) for scanned/image-only PDFs or when you need clean Markdown. LOAD whenever the user wants to pull text out of a PDF/scan/image, 'OCR this', convert a PDF to Markdown/text/JSON, extract tables or a specific page range from a document, or read a document that has no copy-pasteable text. Decision rule: try the PyMuPDF text layer FIRST (instant, free, no models); only fall back to marker-pdf OCR when the text layer is empty/garbled (image-only scan) or the user needs layout-faithful Markdown/tables. PyMuPDF needs nothing extra; marker-pdf is a heavy external tool (PyTorch + Surya OCR model weights, optional GPU) installed on demand via pip — it is NOT a bundled dependency. Not for editing PDFs (see nano-pdf/document skills) — this skill only READS/extracts."
category: research
---

# OCR & Documents — extract text from PDFs / scans / images

Pull text out of documents. Two tiers, and **you almost always try tier 1 first**:

| Tier | Tool | Use when | Cost |
|------|------|----------|------|
| 1. Text layer | **PyMuPDF** (`import fitz`) | Born-digital PDF that already has selectable text | Instant, free, already installed |
| 2. OCR + layout | **marker-pdf** CLI (`marker_single` / `marker`) | Scanned / image-only PDF, or you need clean Markdown / tables / reading order | Slow, needs PyTorch + model weights, installed on demand |

Reply in Chinese unless the user clearly uses another language.

## Decision rule (read this first)

1. **Always probe the text layer first** with PyMuPDF. It is instant, needs no models, and
   PyMuPDF (`pymupdf` / `import fitz`) is already a **core dependency** of this workspace — no
   install, no download.
2. **If the extracted text is non-empty and readable**, you are done. Do NOT invoke OCR — running
   marker-pdf on a PDF that already has a text layer just wastes minutes and GPU/CPU.
3. **Only fall back to marker-pdf OCR** when: the text layer is empty or garbled (image-only
   scan), OR the user explicitly wants layout-faithful **Markdown / JSON** (headings, tables, math,
   reading order), OR the input is an image (`.png`/`.jpg`) rather than a text PDF.

This skill only **reads / extracts** text. To *edit* a PDF's content, that is a different skill.

## Tier 1 — PyMuPDF text layer (default, free, no models)

PyMuPDF is imported as `fitz` and is a **core workspace dependency** (`pymupdf>=1.28.0` in
`pyproject.toml`, and bundled by nuitka/pyinstaller). Run it through the **`bash`** tool as a
short Python script — do **not** add any dependency and do **not** shell out to an external
binary for this tier.

**Probe whether a PDF has a usable text layer, and extract it:**

```bash
python - "$PDF" <<'PY'
import sys, fitz  # PyMuPDF
doc = fitz.open(sys.argv[1])
parts = []
for page in doc:
    parts.append(page.get_text("text"))  # "text" = plain reading-order text
doc.close()
full = "\n".join(parts)
stripped = full.strip()
if len(stripped) < 20:
    # Almost no extractable text → image-only scan. Fall back to Tier 2 (marker-pdf OCR).
    print("__NO_TEXT_LAYER__")
else:
    print(full)
PY
```

- If the script prints `__NO_TEXT_LAYER__` (or the output is empty / obviously garbled), the PDF
  is an image-only scan → go to **Tier 2**.
- `page.get_text("text")` gives plain text in reading order. Other modes: `"blocks"` (text
  blocks with bounding boxes — good for columns/tables), `"words"` (word-level with coords),
  `"dict"`/`"rawdict"` (full structure incl. font/size), `"html"`/`"xhtml"` (keeps some layout).
- **Specific pages only:** `for page in doc.pages(0, 5)` (pages 0–4), or index directly
  `doc[2].get_text()` for a single page. Page numbers here are **0-based**.
- **Tables:** `page.find_tables()` returns detected tables; `tab.extract()` gives rows, or
  `tab.to_markdown()` / `tab.to_pandas()` (pandas only if available). Table detection is
  heuristic — verify against the source.
- **Images / rasterize a page** (e.g. to hand a single page to OCR): `pix = page.get_pixmap(dpi=300); pix.save("page.png")`.
- **Metadata:** `doc.metadata` (title/author/…), `doc.page_count`, `doc.get_toc()` for bookmarks.
- Always `doc.close()` when done.

## Tier 2 — marker-pdf OCR + layout (scans, or when you need Markdown)

[marker-pdf](https://github.com/datalab-to/marker) (datalab-to/marker) converts PDFs, images,
and Office files to clean **Markdown / JSON / HTML**, running a deep-learning pipeline (layout,
OCR via Surya, table & math recognition, reading order). Use it for **image-only scans** or when
the user wants layout-faithful output that a raw text dump can't give.

**This is an external CLI, NOT a workspace Python dependency.** marker-pdf pulls in **PyTorch +
several GB of Surya OCR model weights** — far too heavy to bundle. So this skill does **not** add
it to `pyproject.toml` and does **not** touch nuitka / pyinstaller. The agent runs it through the
**`bash`** tool, installing it **on demand** (same pattern as the `comfyui` / `codex` /
`nano-pdf`-style CLI-wrapper skills).

### Install on demand

```bash
# Fast check — skip the slow install if it's already there.
command -v marker_single >/dev/null 2>&1 || pip install marker-pdf
# For non-PDF inputs (docx, pptx, xlsx, html, epub), install the extras instead:
#   pip install "marker-pdf[full]"
```

- Needs **Python 3.10+ and PyTorch**. First real run also **downloads the Surya model weights**
  (hundreds of MB → a few GB) — expect a slow first invocation; cached afterwards.
- On a bare Linux base you may need `pip install --break-system-packages` (PEP 668).
- Device auto-detects (GPU / CPU / Apple MPS). Force it with `TORCH_DEVICE=cuda` /
  `TORCH_DEVICE=cpu`. CPU works but is slow. On OOM, lower `--workers` (each worker peaks ~5 GB
  VRAM).

### Single file → Markdown

```bash
marker_single "$PDF" --output_format markdown --output_dir ./ocr_out
```

- `--output_format [markdown|json|html|chunks]` — default `markdown`. Use `json` when you need
  structured blocks (headings/tables/positions) programmatically.
- `--output_dir PATH` — where results are written (one subfolder per input, containing the
  `.md`/`.json` plus any extracted images).
- `--page_range "0,5-10,20"` — restrict to pages 0, 5–10, 20 (**0-based**). Great for large PDFs.
- `--force_ocr` — OCR the whole doc even if it has a (bad) text layer; also formats inline math.
  Only use when the existing text layer is wrong — it's much slower.
- `--disable_image_extraction` — skip pulling images out (smaller output).
- `--use_llm` — higher accuracy (merges cross-page tables, inline math, better tables) by calling
  an LLM. With Gemini it needs `GOOGLE_API_KEY`. Optional; costs API calls.
- There is **no `--languages` flag** in current marker; Surya OCR auto-handles languages. If you
  don't need OCR at all, tier 1 already covered you.

### Batch a whole folder

```bash
marker ./pdf_in --output_format markdown --output_dir ./md_out --workers 4
```

Same flags as `marker_single`, plus `--workers N`. Process a **whole directory in one command**
rather than looping file-by-file across turns (serial per-file OCR burns the turn budget).

### Python API (when you need the rendered object in-process)

```python
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered

converter = PdfConverter(artifact_dict=create_model_dict())
rendered = converter("FILEPATH")
text, _, images = text_from_rendered(rendered)
```

### Licensing note

marker's code is GPL and its model weights use a modified license; commercial self-hosting may
require a license per the project's README. Not legal advice — just flag it to the user if they're
using it commercially.

## Delivering results

- For a **short extraction**, print the text back in the reply.
- For a **long document or the generated Markdown/JSON file**, write it to a file and hand it to
  the user with a `[SEND:/abs/path/to/output.md]` marker so the channel delivers the file.
- **Never fabricate document content.** Every line you report must come from the actual PyMuPDF
  output or marker-pdf result. If OCR is uncertain or a page failed, say so — don't guess at what
  a blurry scan said.

## Related skills

- **`data-text-processing`** — broader ETL / parsing once you have the text (CSV/JSON, dates,
  dataset merging, regex extraction). This skill is the *ingestion* front-end for scanned/PDF
  inputs feeding into that.
- **`document-report-authoring`** — *generating* polished Word/PPT/Excel reports (the reverse
  direction: data → document).
- **`image-understanding`** — when you want to *understand* an image's content (describe/answer
  questions), not transcribe its text verbatim.
