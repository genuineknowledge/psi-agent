---
name: document-report-authoring
description: "Generating polished Office report files — Word (.docx), PowerPoint (.pptx), and Excel (.xlsx): layout, headings, a table of contents, tables, and native charts. Use whenever the user asks to produce, export, or 'make me' a Word document, slide deck / PPT, or spreadsheet report, or to turn analysis/data into a formatted report file (not a markdown table in chat). Prefers python-docx / python-pptx / openpyxl, and each library's built-in chart API over matplotlib."
category: output
---

# Document & Report Authoring (Word / PPT / Excel)

Use this skill when the user wants a real Office file as the deliverable — a Word
report, a slide deck, or a spreadsheet — with proper layout (headings, table of
contents, tables, charts), not a markdown table in the chat.

Reply in Chinese unless the user clearly uses another language.

## Pick the right output first

- **Excel (.xlsx)** — tabular data, multi-sheet reports, anything the user will sort/filter
  or that has charts driven by cells. For a **plain table**, use the existing `write_excel`
  tool (it handles rows, a bold header, and column-width fitting). Only drop to raw `openpyxl`
  when you need multiple sheets, styling, formulas, or charts.
- **Word (.docx)** — prose reports: sections, headings, a table of contents, embedded
  tables. For headings + paragraphs + tables, use the existing `write_word` tool — it
  sets the East-Asian font automatically, so Chinese never comes out "字体不齐". Only
  drop to raw `python-docx` for what the tool can't express (images, native charts,
  per-run styling).
- **PowerPoint (.pptx)** — slide decks: title slide, agenda, one idea per slide, charts.
  Use `python-pptx`. Build slides programmatically from scratch (do not depend on a
  specific local template file).

All three libraries are available in this environment: `openpyxl`, `python-docx` (`import docx`),
`python-pptx` (`import pptx`). `matplotlib` is NOT installed — use each library's **native**
chart API instead (see Charts below).

## Workflow

1. **Clarify the deliverable** if ambiguous: which format, and what sections/data go in it.
   If the user already gave the data or an analysis, don't re-ask — go straight to authoring.
2. **Write a small Python script** using the right library, run it with the tool runner, and
   save to a clear path (e.g. `report.docx`). Do not hand-write the OOXML.
3. **Verify** the file was created (check it exists and re-open it to read back a value / slide
   count / sheet name) before telling the user it's done. Report the absolute output path.
4. If the user wants it delivered over a channel (Telegram/Feishu), emit the `[SEND:path]`
   marker so the file is sent, not just described.

## Word (.docx) — `write_word` tool or python-docx

- **Headings / paragraphs / tables → `write_word` tool.** Pass `blocks_json` (an ordered
  array of `{"type": "heading"|"paragraph"|"table"|"page_break", ...}`) and an optional
  `title`. It sets `w:eastAsia` on every base style for you, so Chinese text is consistent
  — no "字体不齐". Example:

  ```json
  [{"type": "heading", "level": 1, "text": "概述"},
   {"type": "paragraph", "text": "本季度……"},
   {"type": "table", "rows": [["月份", "收入"], ["1月", "100"]]}]
  ```

- **Images / native charts / per-run styling → raw python-docx.** When you build the
  document by hand, you own the CJK-font fix below.

```python
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
doc.add_heading("2026 Q2 运营报告", level=0)          # title
doc.add_heading("1. 概述", level=1)                    # H1 -> feeds the TOC
doc.add_paragraph("本季度……")                          # body text

# a data table
t = doc.add_table(rows=1, cols=3)
t.style = "Light Grid Accent 1"
hdr = t.rows[0].cells
hdr[0].text, hdr[1].text, hdr[2].text = "月份", "收入", "环比"
for month, rev, mom in data:
    c = t.add_row().cells
    c[0].text, c[1].text, c[2].text = month, f"{rev:,}", mom

doc.add_page_break()
doc.save("report.docx")
```

- **Headings drive the table of contents.** Use `add_heading(text, level=1/2/3)` consistently.
- A real, auto-updating TOC needs a Word field. python-docx has no direct API, so inject the
  field XML once:

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_toc(doc):
    p = doc.add_paragraph()
    run = p.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), r'TOC \o "1-3" \h \z \u')
    run._r.addnext(fld)
```

  Note the TOC shows as empty until the user opens the doc and updates fields (Word prompts, or
  press F9 / Ctrl+A then F9). State this to the user — it is expected, not a bug.
- Embed an image with `doc.add_picture("chart.png", width=Inches(6))`.
- Set fonts / sizes via `run.font` when the user cares about styling; keep defaults otherwise.

### Chinese fonts — set `w:eastAsia` or the doc will look "字体不齐"

python-docx's `run.font.name` (and `add_heading`) only sets the **Latin** font
(`w:ascii` / `w:hAnsi`); it does NOT set the **East Asian** font (`w:eastAsia`). Word then
renders CJK characters in its default East Asian font, so some Chinese glyphs (e.g. 增长、报、
维、结、跃、稳) fall back to a different typeface and the text looks uneven — this is a font
bug, not a content bug. **For any document containing Chinese, set `w:eastAsia`.**

The robust fix is to set the East Asian font on the base styles once, so every paragraph,
heading, and table cell inherits it. (The `write_word` tool does exactly this; the code
below is for when you build the document by hand.)

```python
from docx.oxml.ns import qn

def set_cjk_font(doc, cjk="微软雅黑", latin="Calibri"):
    """Set East-Asian + Latin fonts on base styles so all text is consistent."""
    for style in doc.styles:
        element = style.element
        if not hasattr(element, "get_or_add_rPr"):
            continue                          # numbering styles have no run props
        rpr = element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()      # keeps w:rFonts first, per OOXML schema
        rfonts.set(qn("w:ascii"), latin)
        rfonts.set(qn("w:hAnsi"), latin)
        rfonts.set(qn("w:eastAsia"), cjk)     # <-- the line that fixes 字体不齐

doc = Document()
set_cjk_font(doc)                            # call right after creating the document
```

Iterating `doc.styles` covers Normal, every Heading, Title, and table styles in one pass,
and `get_or_add_rFonts()` inserts `w:rFonts` as the first child of `w:rPr` (the order the
schema requires) instead of appending it at the end.

If you instead style individual runs, set `w:eastAsia` on each run too — setting only
`run.font.name` is the exact cause of the uneven-font problem:

```python
run.font.name = "微软雅黑"
run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
```

Common CJK fonts safe on most systems: `微软雅黑` (Windows), `宋体`, `黑体`. Do not mix
several CJK fonts in one document unless the user asks.

## PowerPoint (.pptx) — python-pptx

```python
from pptx import Presentation
from pptx.util import Inches, Pt

prs = Presentation()                        # blank 4:3; use pptx.util for 16:9 if asked

# title slide
s = prs.slides.add_slide(prs.slide_layouts[0])
s.shapes.title.text = "2026 Q2 运营报告"
s.placeholders[1].text = "数据团队 · 2026-07"

# bullet slide (layout 1 = Title and Content)
s = prs.slides.add_slide(prs.slide_layouts[1])
s.shapes.title.text = "本季度要点"
tf = s.placeholders[1].text_frame
tf.text = "收入同比 +18%"
for line in ["新客占比提升", "退款率下降至 2.1%"]:
    p = tf.add_paragraph(); p.text = line; p.level = 1

prs.save("deck.pptx")
```

- **One idea per slide.** Title slide → agenda → content slides → summary.
- Standard layouts by index: 0 title, 1 title+content, 5 title only, 6 blank.
- Add a table with `s.shapes.add_table(rows, cols, left, top, width, height).table`.
- Add an image with `s.shapes.add_picture(path, left, top, width=...)`.
- Build from scratch. Only reuse an existing template if the **user supplies one and asks**;
  do not assume any local `.pptx` template exists.

## Excel (.xlsx) — write_excel tool or openpyxl

- **Simple table → `write_excel` tool.** Pass `rows_json` (a 2D array), `sheet_name`, `header`.
- **Multi-sheet / styled / formulas / charts → openpyxl directly:**

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.chart import BarChart, Reference

wb = Workbook()
ws = wb.active; ws.title = "月度"
ws.append(["月份", "收入"])
for cell in ws[1]:                                   # style header
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="4472C4")
for row in data:
    ws.append(row)
ws["B14"] = "=SUM(B2:B13)"                            # formula
wb.save("report.xlsx")
```

## Charts — use native library APIs, not matplotlib

`matplotlib` is not installed. Prefer charts that live inside the Office file (they stay
editable and need no image files):

- **Excel:** `openpyxl.chart` — `BarChart`, `LineChart`, `PieChart` + `Reference` to cell
  ranges, then `ws.add_chart(chart, "E2")`.
- **PowerPoint:** `slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, y, cx, cy, chart_data)`
  with `pptx.chart.data.CategoryChartData`.
- **Word:** python-docx cannot create native charts. Options, in order: (a) build the chart in
  an Excel file and reference/attach it, (b) render an image with a lightweight lib (e.g. PIL)
  and `add_picture`, or (c) tell the user matplotlib would need installing first and ask before
  adding a dependency. Do not silently `pip install`.

## Common pitfalls

- **Don't output a markdown table when the user asked for a file.** Produce the actual .xlsx/.docx/.pptx.
- **Sheet names cap at 31 chars**; titles longer than that raise. Truncate.
- **Word TOC is empty until fields update** — say so instead of "re-generating".
- **Chinese text looks "字体不齐" in Word** — you set `run.font.name` but not `w:eastAsia`,
  so some CJK glyphs fell back to Word's default font. Prefer the `write_word` tool (handles
  this for you); if you build the doc by hand, call `set_cjk_font(doc)` (above) right after
  creating any Chinese document.
- **Number formatting** (thousands separators, %, currency) is the author's job — format before
  writing, or set Excel cell `number_format`.
- **Verify before claiming done**: reopen the saved file and read back a slide count / sheet
  name / cell value. Report the output path.

See also [[structured-output-tables]] for deciding when tabular output belongs in chat vs a file.
