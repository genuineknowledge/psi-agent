---
name: nano-pdf
description: "Edit an existing PDF's text — fix typos, rewrite titles/headings, correct captions or footers, tweak dates and labels — by describing the change in natural language, through the official nano-pdf CLI run via the bash tool. nano-pdf renders a target page, applies the instruction with Google's Gemini 3 Pro Image (\"Nano Banana\") model, and writes a new PDF, so it works on scanned/flattened decks where there is no editable text layer. Covers the `edit` subcommand (one or more page/instruction pairs), the `add` subcommand (insert an AI-generated slide), style references, resolution, and output naming. No dedicated Python tool and no extra Python dependencies — shell only, same shape as xurl/notion/goplaces. Needs the nano-pdf binary on PATH (pip/uvx), a paid GEMINI_API_KEY, and system deps poppler + tesseract. Use when the user wants to edit text/titles/typos inside a .pdf in place, not to author a new document from scratch (see powerpoint/document-report-authoring for that)."
category: output
---

# nano-pdf (natural-language PDF editing)

Edit an existing **`.pdf`** by describing the change in plain language, driven entirely
through the official **`nano-pdf`** CLI from the **bash** tool. `nano-pdf` rasterizes the
target page, sends it plus your instruction to Google's **Gemini 3 Pro Image** ("Nano
Banana") model, and writes a **new** PDF with the edit applied. Because it edits the
*rendered* page, it works even on scanned or flattened PDFs that have no editable text
layer. There is no dedicated Python tool and **no extra Python dependencies** — it is shell
only, the same shape as `xurl`, `notion`, and `goplaces`.

Reply in Chinese unless the user clearly uses another language.

Use this skill when the user wants to **change text inside an existing PDF** — fix a typo,
reword a title/heading, correct a caption/footer, update a date or label — or **insert a new
slide**. To author a brand-new deck or report from scratch, prefer `powerpoint` or
`document-report-authoring` instead.

## Prerequisites

- **`nano-pdf` on PATH.** Check with `command -v nano-pdf`. Install as a Python package:
  `pip install nano-pdf`. You can also run it without installing via `uvx nano-pdf ...`.
- **A paid `GEMINI_API_KEY`** in the environment. Image generation is **not** available on
  free-tier keys, so a billing-enabled key is required. Reference it by name only — never
  echo, log, or commit the key value.
- **System dependencies:** `poppler` (PDF rendering) and `tesseract` (OCR). Install per
  platform — macOS `brew install poppler tesseract`, Windows `choco install poppler tesseract`,
  Linux `sudo apt-get install poppler-utils tesseract-ocr`.

```bash
command -v nano-pdf >/dev/null || { echo "need nano-pdf on PATH — pip install nano-pdf (or use uvx nano-pdf)"; exit 1; }
: "${GEMINI_API_KEY:?set a paid GEMINI_API_KEY in the environment first (free-tier keys can't generate images)}"
```

If the key is missing or free-tier, stop and tell the user — do **not** invent a key or
claim an edit succeeded without running the tool.

## Edit text on a page (the core pattern)

`nano-pdf edit` takes a **file**, a **1-based page number**, and a natural-language
**instruction**. Be specific about *what* text and *what* it should become — quote the exact
strings so the model changes only that text and nothing else.

```bash
# Fix a title on page 2.
nano-pdf edit deck.pdf 2 "Change the title to 'Q3 Results'"

# Correct a typo — name the wrong word and the fix so it doesn't touch anything else.
nano-pdf edit report.pdf 5 "Fix the typo 'Recieve' in the body text, it should read 'Receive'"

# Rewrite a footer / update a date.
nano-pdf edit deck.pdf 1 "Update the date in the footer to 'October 2025'"
```

By default `edit` runs **without** full-document text context (`--no-use-context`), which
keeps the model focused on the single page. The output goes to a new file (see
[Output & options](#output--options)); the source PDF is left untouched.

### Multiple edits in one run

Pass **repeated page/instruction pairs** to fix several places in one invocation. Line
continuations keep it readable:

```bash
nano-pdf edit deck.pdf \
  1 "Update date to Oct 2025" \
  5 "Add company logo top-right" \
  10 "Fix typo in footer: 'teh' -> 'the'"
```

Each pair is `<page> "<instruction>"`. Pages are independent — a bad instruction on one page
does not roll back the others, so re-run just the page you need to redo.

## Add a new slide/page

`nano-pdf add` inserts a **new AI-generated page**. The number is an **insertion point**:
`0` inserts at the very beginning; `N` inserts *after* existing page `N`.

```bash
nano-pdf add deck.pdf 0 "Title slide with 'Q3 2025 Review' and a subtle dark background"
nano-pdf add deck.pdf 3 "A summary slide listing the three key takeaways as bullets"
```

`add` defaults to `--use-context` (it reads the whole PDF's text) so the new page matches the
document's content and tone.

## Output & options

- `--output "new.pdf"` — set the output filename. Without it, nano-pdf writes to its own new
  file rather than overwriting the source; capture the path it prints and report it.
- `--style-refs "1,5"` — use pages 1 and 5 as **style references** so the edit/new page keeps
  the deck's look (fonts, colors, layout). Great for making an edit blend in.
- `--resolution "4K"` — render resolution, one of `4K`, `2K`, `1K`. Higher = sharper output
  but slower and more expensive; `2K` is a good default for text edits.
- `--use-context` / `--no-use-context` — include the full PDF text as context. Off by default
  for `edit`, on for `add`. Turn it on for `edit` when the change depends on other pages.
- `--disable-google-search` — Google Search grounding is **on** by default; disable it for
  edits that must not pull in outside facts (e.g. a pure typo fix).

```bash
nano-pdf edit deck.pdf 2 "Change the title to 'Q3 Results'" \
  --style-refs "1" --resolution "2K" --output "deck-fixed.pdf" --disable-google-search
```

## Verify before claiming done

This is an **AI image** edit, not a text-layer patch — always confirm the result rather than
assuming success:

1. Check the command exited `0` and note the **output path** it printed.
2. Re-render or read back the edited page to confirm the text changed as asked and nothing
   else was altered. A quick text extract of the target page:
   ```bash
   pdftotext -f 2 -l 2 deck-fixed.pdf - | head    # poppler's pdftotext; -f/-l = page range
   ```
   (For scanned output with no text layer, open/preview the page image instead.)
3. Report the absolute output path. If the deck should be delivered over a channel
   (Telegram/Feishu), emit the `[SEND:path]` marker so the file is actually sent.

## Cost, correctness & safety

- **Every edit calls a paid image model.** Batch related fixes into one `edit` run (multiple
  page/instruction pairs) instead of many separate calls, and prefer `2K` over `4K` unless the
  output must be print-sharp.
- **Be surgical.** Name the exact text to change and its replacement; vague instructions
  ("clean up this slide") let the model rewrite more than intended. Edit the source file's
  page, keep the original, and diff visually.
- **Text fidelity is not guaranteed.** The model regenerates the page image, so verify
  spelling/numbers after the run — especially for names, figures, and dates.
- **Never fabricate a result.** If the key is missing/free-tier, the binary isn't installed,
  or the command errors, report the actual failure — do not claim the PDF was edited.
- **Never echo, log, or commit `GEMINI_API_KEY`.** Reference it by name only.

## Handling errors

- **`command not found: nano-pdf`** — not installed / not on PATH. `pip install nano-pdf` or
  run via `uvx nano-pdf ...`.
- **Auth / quota error from Gemini** — key missing, free-tier (no image generation), or over
  quota. Report it and ask the user to set a paid `GEMINI_API_KEY`; don't retry blindly.
- **poppler / tesseract missing** — page render or OCR fails. Install the system deps (see
  Prerequisites) for the platform.
- **Wrong page edited / nothing changed** — page numbers are 1-based for `edit`; for `add`
  the number is an insertion point (0 = start). Re-check the number and re-run just that page.

