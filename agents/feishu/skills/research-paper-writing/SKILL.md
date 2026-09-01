---
name: research-paper-writing
description: "Write a machine-learning research paper for NeurIPS / ICML / ICLR end to end — from framing the contribution through a submission-ready PDF: title/abstract, problem & contributions, related work, method, theory, experiments & analysis, limitations, the LaTeX build (official style files, page/format limits, anonymization), reproducibility, and the rebuttal. Use when the user wants to draft, structure, tighten, or format an ML paper (or a section of one) for a top-tier venue, prepare a camera-ready, or answer reviewers. Composes the existing read/write/edit/bash tools plus arxiv for related work and subagent-orchestration for parallel section drafting — no dedicated tool, no extra deps. LaTeX (texlive/tectonic) is invoked through bash when producing the actual PDF."
category: research
---

# ML research-paper writing (NeurIPS / ICML / ICLR)

Draft, structure, and format a machine-learning paper for a top-tier venue,
carrying it from an idea to a submission-ready PDF. This is a **process recipe**,
not a tool: it composes the existing `read` / `write` / `edit` / `find_files` /
`search_content` / `bash` tools, the `arxiv` skill for related work, and
`subagent-orchestration` for drafting sections in parallel. **No dedicated tool
and no extra Python dependencies** — LaTeX is external and driven through `bash`.

Reply in Chinese unless the user clearly uses another language. (The **paper
itself** is written in academic English regardless of the chat language.)

## Never fabricate results, citations, or numbers (hard rule)

A paper's credibility is its numbers and its references. **Every experimental
number, table cell, plot, and citation must come from something you actually
have** — a run the user gave you, a log/CSV in the workspace, or a real paper you
looked up (use the `arxiv` skill; copy ids/titles/authors verbatim, never invent
an arXiv id or a bibkey). If a result does not exist yet, write the table with a
clearly-marked `TODO`/placeholder and tell the user what to run — **do not** fill
it with a plausible-looking number. Fabricated results or references are academic
misconduct and are worse than an unfinished draft. If you cannot verify a claim,
hedge it or drop it.

## Pick the stage first

Ask (or infer) where the user is; the work differs sharply per stage:

1. **Design / framing** — no draft yet. Nail the *contribution* before prose.
2. **Drafting** — turn a framed contribution + results into full sections.
3. **Revision / tightening** — a draft exists; improve clarity, rigor, framing.
4. **Formatting / build** — assemble the official-template LaTeX into a PDF that
   meets the venue's page and format rules, anonymized for review.
5. **Rebuttal / camera-ready** — respond to reviewers or prepare the final version.

If the user only asked for one section (e.g. "write the related work"), do that
section well rather than restructuring the whole paper.

## Venue facts to confirm, never assume (they change yearly)

Deadlines, page limits, and template versions change **every year** — do not
trust memory. Before formatting, confirm the current cycle's rules from the
official call for papers (`fetch` the venue site if network is available):

- **NeurIPS** — `neurips.cc`. ~9-page main text (recent years), references and
  appendix unlimited/separate; **double-blind**; mandatory checklists
  (reproducibility / broader-impact); supplementary allowed. Uses the
  `neurips_2xxx` style.
- **ICML** — `icml.cc`. ~8-page main text + references; **double-blind**; uses
  the `icml2xxx` style; often an author-response (rebuttal) phase.
- **ICLR** — `iclr.cc`, reviewed on **OpenReview**. ~9/10-page soft limit;
  **double-blind**; uses the `iclr2xxx_conference` style; public reviews +
  rebuttal threads.

All three are **double-blind**: no author names, no de-anonymizing acknowledgments
or funding, no "in our prior work [12] we…" phrasing, and cite your own prior work
in the third person. Anonymize repo/data links (use an anonymized mirror).

## Stage 1 — Design the contribution (do this before prose)

Write these down (a scratch `outline.md`) and get them tight *first*; a paper
lives or dies on the framing:

- **One-sentence contribution.** "We show that X, by Y, achieving Z." If you
  can't say it in one sentence, the paper isn't ready to write.
- **The delta vs prior work.** What is new *relative to* the closest 3–5 papers
  (find them with `arxiv`)? Novelty is a *difference*, not just a result.
- **The central claim → the experiment that would falsify it.** Every claim in
  the abstract must map to a specific table/figure that supports it.
- **Contribution bullets** (3–4): typically (i) a problem/insight, (ii) a
  method, (iii) empirical results, (iv) analysis/theory.

Reviewers reject on *framing and evidence* far more than on writing polish. Spend
effort here.

## Stage 2 — Structure & draft

Standard ML paper skeleton (adapt, don't pad):

1. **Title** — specific and honest; name the method and the payoff.
2. **Abstract** (~150–200 words) — problem, gap, what you do, headline result
   with a concrete number, one-line why-it-matters. Write it **last**.
3. **Introduction** — motivation → gap → "In this paper we…" → contribution
   bullets → forward pointer to results. End §1 with the punchline number.
4. **Related work** — *group by theme*, and for each say how you **differ**, not
   just what they did. Use `arxiv` to find and verify every reference.
5. **Method / Approach** — precise notation table first; define every symbol
   before use; algorithm box; keep it reproducible from the text.
6. **Theory** (if any) — assumptions stated explicitly, theorem, proof sketch in
   main text with full proof in the appendix.
7. **Experiments** — setup (datasets, baselines, metrics, hardware, seeds),
   then results tables/figures, then **analysis** (why it works: ablations,
   sensitivity, failure cases). Ablations are what separate strong papers.
8. **Limitations** — an honest, specific section (several venues require it).
   Reviewers respect stated limitations; hidden ones get found and punished.
9. **Conclusion** — short; restate the contribution, one line of future work.
10. **Broader impact / checklist** — fill the venue's mandatory checklist truthfully.
11. **References + Appendix** — proofs, extra results, hyperparameters, prompts.

Drafting tips:
- Write the **method and experiments first**, intro and abstract last.
- Every table/figure needs a self-contained caption and must be *referenced and
  interpreted* in the text — never drop a table without saying what to read from it.
- **Bold the best result** per column and state the metric direction (↑/↓).
- Prefer vector figures (PDF/PGF); label axes and units; readable at print size.

For a large draft, split by section and **fan out to subagents** (see the
`subagent-orchestration` skill): give each subagent one section, the shared
notation/outline, and the results it may cite, then merge and unify notation.

## Stage 3 — Revise & self-review

Before claiming a draft is done, run the reviewer's checklist against it:
- Does the abstract's headline claim have a table that proves it?
- Is every symbol defined before use, and notation consistent across sections?
- Are baselines fair (same data/compute) and is the comparison honest?
- Are there ablations isolating each contribution's effect?
- Is there a real limitations section, and does the impact/checklist match?
- Is it anonymized (no names, no de-anonymizing links or acknowledgments)?
- Do all `\cite` keys resolve and do all `\ref`/`\label` match (no `??` in the PDF)?

Tighten prose: cut hedging, kill redundancy, one idea per paragraph, active voice
for your own contributions. Get **under the page limit** by cutting content, not
by shrinking margins/fonts — reviewers notice format hacks and desk-reject for them.

## Stage 4 — LaTeX build (the actual PDF)

Use the **official style files** for the exact year — never approximate the
template. LaTeX runs through `bash`; check what's installed first:

```bash
command -v pdflatex tectonic latexmk bibtex biber 2>/dev/null
```

- **Get the template**: download the venue's `<style>.sty` bundle for the current
  year from the official site and keep it beside `main.tex`. Set the review vs
  final flag correctly, e.g. `\usepackage[preprint]{neurips_2xxx}` for arXiv but
  the plain (anonymous) option for submission — the option toggles author display
  and line numbers.
- **Build** (latexmk is the least-fuss; two passes + bib otherwise):

```bash
# preferred
latexmk -pdf -interaction=nonstopmode main.tex
# or tectonic (self-contained, fetches packages)
tectonic main.tex
# or manual
pdflatex -interaction=nonstopmode main.tex && bibtex main && \
  pdflatex -interaction=nonstopmode main.tex && pdflatex -interaction=nonstopmode main.tex
```

- **Verify the PDF** before declaring done: it compiled without unresolved refs,
  is within the page limit for the **main text**, uses the template's default
  font/margins, and (for submission) shows **no author names**. Check the page
  count, e.g. `pdfinfo main.pdf` or `qpdf --show-npages main.pdf`.
- Keep `.bib` clean: one canonical entry per work, DOIs/arXiv ids from real
  lookups (`arxiv` skill), consistent venue names.
- If LaTeX isn't installed, either use `tectonic` (self-fetching) or say so and
  offer to produce the content in Markdown for the user to compile — **do not**
  claim a PDF was built when it wasn't.

## Stage 5 — Rebuttal & camera-ready

- **Rebuttal**: address every reviewer point; lead with the most damaging concern;
  back claims with *new numbers you actually ran*, not promises; be concise and
  non-defensive; note which changes are already in the revised PDF.
- **Camera-ready**: switch the template to the final (non-anonymous) option, add
  author names/affiliations/acknowledgments/funding, de-anonymize repo/data links,
  fold in promised changes, and re-check the (often larger) camera-ready page limit.

## Deliver

Report the absolute path of the produced `main.tex` / `main.pdf` (and the page
count you verified). If the user wants the file over a channel, emit
`[SEND:<absolute-path>]` so the PDF is sent, the same way other skills deliver files.

## Common pitfalls

- **Framing left implicit** — the contribution isn't stated in one sentence; fix
  the framing (Stage 1) before polishing prose.
- **Fabricated numbers/citations** — the fastest way to a desk-reject or worse;
  every number and reference must trace to a real run or a real paper.
- **De-anonymization** — author names, "our prior work", or a real GitHub link in
  a double-blind submission. Anonymize everything.
- **Format hacks to fit the page limit** — shrinking margins/fonts/spacing gets
  desk-rejected; cut content instead.
- **Tables with no analysis** — a results table nobody interprets, or no ablations
  isolating each contribution.
- **Broken build shipped** — `??` for refs, missing figures, or unresolved `\cite`.
  Always compile and open the PDF before saying it's ready.
- **Claiming a PDF exists when LaTeX never ran** — verify with `pdfinfo`/page count.

See also [[arxiv]] for finding and verifying related work, [[document-report-authoring]]
for non-LaTeX report files, and [[subagent-orchestration]] for parallel section drafting.
