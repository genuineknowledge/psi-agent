---
name: powerpoint
description: "Create, read, edit, and inspect PowerPoint decks (.pptx) with python-pptx: build slides from scratch or from a user-supplied template, add/read/edit titles, bullets, tables, images, and native charts, read and write speaker notes, and delete/duplicate/reorder slides. Use whenever the user wants to make, open, modify, extract text from, or restructure a .pptx deck — not a markdown outline in chat. For a one-off simple report that also spans Word/Excel, see document-report-authoring."
category: output
---

# PowerPoint (.pptx) authoring & editing

Use this skill whenever a real `.pptx` file is the deliverable, or when the user
wants to **read**, **edit**, or **restructure** an existing deck: extract slide
text, change titles/bullets, add or remove slides, add tables/images/charts, or
read and write speaker notes.

Reply in Chinese unless the user clearly uses another language.

`python-pptx` is installed in this environment (`import pptx`, version 1.x).
`matplotlib` is **not** installed — use python-pptx's **native** chart API (see Charts).

## Workflow

1. **Clarify** the deliverable only if ambiguous (new deck vs. edit existing, which slides,
   what content). If the user already gave the data/outline, go straight to authoring.
2. **Write a small Python script** using `python-pptx`, run it with the tool runner, and save
   to a clear path (e.g. `deck.pptx`). Never hand-write the OOXML.
3. **Verify** before claiming done: reopen the saved file and read back a slide count / a
   title / a note. Report the absolute output path.
4. If the deck should be delivered over a channel (Telegram/Feishu), emit the `[SEND:path]`
   marker so the file is sent, not just described.

## Create a deck from scratch

```python
from pptx import Presentation
from pptx.util import Inches, Pt

prs = Presentation()                         # blank deck, 4:3 by default

# title slide (layout 0)
s = prs.slides.add_slide(prs.slide_layouts[0])
s.shapes.title.text = "2026 Q2 运营报告"
s.placeholders[1].text = "数据团队 · 2026-07"   # subtitle placeholder

# bullet slide (layout 1 = Title and Content)
s = prs.slides.add_slide(prs.slide_layouts[1])
s.shapes.title.text = "本季度要点"
tf = s.placeholders[1].text_frame
tf.text = "收入同比 +18%"                       # first bullet
for line in ["新客占比提升", "退款率下降至 2.1%"]:
    p = tf.add_paragraph()
    p.text = line
    p.level = 1                              # indent level (0-based)

prs.save("deck.pptx")
```

- **One idea per slide.** Title → agenda → content slides → summary.
- Standard layout indices: `0` title, `1` title+content, `5` title only, `6` blank.
- `slide.shapes.title` is the title placeholder; other placeholders are reached via
  `slide.placeholders[idx]`. The body/content placeholder is usually index `1`.

### 16:9 vs 4:3

The default template is 4:3. For widescreen set the slide size **before** adding slides:

```python
from pptx.util import Inches
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
```

Slide size is in EMU; `Inches(...)` / `Pt(...)` from `pptx.util` convert for you. Read the
current size with `prs.slide_width`, `prs.slide_height`.

## Read / extract from an existing deck

```python
from pptx import Presentation

prs = Presentation("deck.pptx")
print("slides:", len(prs.slides))
for i, slide in enumerate(prs.slides):
    title = slide.shapes.title.text if slide.shapes.title else ""
    print(f"--- slide {i}: {title!r} ---")
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs)
                if text:
                    print("  " * (para.level + 1), text)
        if shape.has_table:
            for row in shape.table.rows:
                print("   |", " | ".join(c.text for c in row.cells))
    if slide.has_notes_slide:
        print("  [notes]", slide.notes_slide.notes_text_frame.text)
```

- Guard `slide.shapes.title` — it can be `None` on layouts without a title.
- Text lives in **runs** inside **paragraphs**; `shape.text_frame.text` joins them with `\n`,
  but reading run-by-run preserves indent (`para.level`) and lets you inspect formatting.
- Check `shape.has_text_frame`, `shape.has_table`, `shape.has_chart` before accessing each.

## Edit an existing deck

Open, mutate, save (usually back to the same path or a new one):

```python
prs = Presentation("deck.pptx")

# change a title
prs.slides[0].shapes.title.text = "新标题"

# replace body bullets on slide 1
tf = prs.slides[1].placeholders[1].text_frame
tf.clear()                                   # drops all but keeps one empty paragraph
tf.text = "第一条"
tf.add_paragraph().text = "第二条"

# tweak a run's formatting
run = prs.slides[0].shapes.title.text_frame.paragraphs[0].runs[0]
run.font.bold = True
run.font.size = Pt(40)

prs.save("deck.pptx")
```

- `text_frame.clear()` leaves one empty paragraph — set `.text` on it or via `.paragraphs[0]`,
  don't expect zero paragraphs.
- Setting `shape.text_frame.text = "..."` replaces **all** paragraphs with a single one; use
  `add_paragraph()` to keep multiple.

## Speaker notes

```python
slide = prs.slides[0]
slide.notes_slide.notes_text_frame.text = "开场先讲背景，再进数据"   # write (creates notes if absent)

if slide.has_notes_slide:                                          # read (don't create)
    print(slide.notes_slide.notes_text_frame.text)
```

- Accessing `slide.notes_slide` **creates** a notes slide if one doesn't exist. To only read
  without creating, gate on `slide.has_notes_slide` first.

## Tables, images, charts

```python
from pptx.util import Inches
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE

s = prs.slides.add_slide(prs.slide_layouts[5])   # title only
s.shapes.title.text = "数据"

# table
rows, cols = 3, 2
tbl = s.shapes.add_table(rows, cols, Inches(1), Inches(1.5), Inches(6), Inches(2)).table
tbl.cell(0, 0).text, tbl.cell(0, 1).text = "月份", "收入"

# image
s.shapes.add_picture("chart.png", Inches(1), Inches(4), width=Inches(4))

# native chart (preferred over a matplotlib image)
data = CategoryChartData()
data.categories = ["4月", "5月", "6月"]
data.add_series("收入", (120, 135, 158))
s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                   Inches(1), Inches(1.5), Inches(6), Inches(4), data)
```

Native charts stay editable inside PowerPoint and need no image file — prefer them over
matplotlib (which isn't installed here anyway).

## Delete / duplicate / reorder slides

python-pptx has **no public API** for these; operate on the slide-id list XML. Deleting a
slide's id entry removes it from the deck:

```python
def delete_slide(prs, index):
    """Remove the slide at `index` from the presentation."""
    id_lst = prs.slides._sldIdLst           # <p:sldIdLst> element
    id_lst.remove(list(id_lst)[index])

def move_slide(prs, old_index, new_index):
    """Reorder: move the slide at old_index to new_index."""
    id_lst = prs.slides._sldIdLst
    slides = list(id_lst)
    id_lst.remove(slides[old_index])
    id_lst.insert(new_index, slides[old_index])
```

- `prs.slides._sldIdLst` is a private attribute but the standard, well-known way to do this;
  it's an `lxml` element you can `.remove()` / `.insert()`.
- `delete_slide` orphans the underlying slide part (small file bloat) but produces a valid
  deck. That's acceptable; don't over-engineer a full part cleanup unless the user needs it.
- There's no clean built-in **duplicate**; if asked, the pragmatic path is to rebuild the
  target slide's content on a new `add_slide(...)` rather than deep-copying XML parts.

## Templates

- **Build from scratch by default.** Only reuse a template when the **user supplies one and
  asks** — do not assume any local `.pptx` template exists.
- To use a supplied template, open it as the base and add slides using its layouts:

```python
prs = Presentation("brand-template.pptx")    # inherits its theme, master, layouts
layout = prs.slide_layouts[1]                # template's own layouts
prs.slides.add_slide(layout)
```

- Layout indices vary per template — enumerate them first:
  `for i, l in enumerate(prs.slide_layouts): print(i, l.name)`.
- To save a deck as a reusable starting point, just keep the `.pptx`; a `.potx` template file
  is rarely needed for agent workflows.

## Chinese text

python-pptx uses the theme/master fonts, so CJK usually renders fine without the `w:eastAsia`
gymnastics that Word needs. If the user reports uneven Chinese glyphs, set the East-Asian font
on the run explicitly:

```python
from pptx.oxml.ns import qn

run.font.name = "微软雅黑"                       # sets <a:latin> (Latin font) only
rpr = run._r.get_or_add_rPr()
ea = rpr.find(qn("a:ea"))
if ea is None:
    ea = rpr.makeelement(qn("a:ea"), {})       # add the East-Asian font child element
    rpr.append(ea)
ea.set("typeface", "微软雅黑")                   # <a:ea typeface="..."> alongside <a:latin>
```

PowerPoint uses the DrawingML namespace (`a:`), unlike Word's WordprocessingML (`w:`), and the
East-Asian font is an `<a:ea>` **child element** of the run properties — not an attribute. Do
not use `run.font._rPr.set(qn("a:eastAsia"), ...)`; that sets a meaningless attribute and Word/
PowerPoint ignore it.

## Common pitfalls

- **Don't output a markdown outline when the user asked for a file.** Produce the real `.pptx`.
- **`slide.shapes.title` can be `None`** on title-less layouts — guard before `.text`.
- **`text_frame.text = ...` flattens to one paragraph**; use `add_paragraph()` for multiple.
- **Reading `slide.notes_slide` creates a notes slide** — gate reads on `has_notes_slide`.
- **No public delete/reorder** — go through `prs.slides._sldIdLst` (see above).
- **Layout indices differ by template** — enumerate `slide_layouts` instead of assuming 0/1/5/6.
- **Verify before claiming done**: reopen the saved deck, read back slide count / a title.
  Report the absolute output path.

See also [[document-report-authoring]] for multi-format Word/PPT/Excel reports, and
[[structured-output-tables]] for when tabular output belongs in chat vs a file.
