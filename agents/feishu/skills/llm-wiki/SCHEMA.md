# Wiki Schema — the agent's operating contract

This is the **schema layer** of the LLM-wiki pattern: the third layer above the
raw sources and the wiki pages themselves. It defines the structure, naming,
workflow, and long-term maintenance habits the agent follows so the wiki stays
consistent and compounds over time instead of drifting.

When starting a wiki, **seed a copy of this into the wiki as `wiki/_schema.md`**
(via `wiki_write` with title `_schema`) and adapt the "Domain conventions"
section to the topic. Re-read it before a large ingest or a lint pass.

## Three layers (know which one you're touching)

1. **Raw material** — articles, papers, notes, transcripts, pasted sources. Read
   these; never rewrite them. They live outside the wiki (attachments, links,
   or quoted in a page's `## Sources`).
2. **Wiki layer** — the interlinked `wiki/*.md` pages you create, revise, and
   link with `wiki_write` / `wiki_read` / `wiki_search` / `wiki_list` /
   `wiki_links`. This is the durable synthesis layer.
3. **Schema layer** — this file. Rules for the above.

The whole point: **preserve synthesis, don't regenerate it.** Cross-source
summaries, comparisons, and conclusions get written into a page once, then
revised on that page — not recomputed on every question. This is what separates
an LLM wiki from RAG (which retrieves raw slices at query time).

## Page anatomy

Every page is one atomic concept. Structure the body as:

- **Lead** — 1-3 sentences defining the concept in plain terms.
- **Body** — the synthesis: what matters, how it relates to other concepts,
  contrasts and trade-offs. Link every concept that has (or deserves) its own
  page with `[[Title]]`.
- **`## Open questions`** (optional) — unresolved points; these drive the next
  ingest.
- **`## Sources`** — the raw material this page was compiled from (URLs/refs).

Frontmatter (`title`, `slug`, `tags`, `aliases`, `created`, `updated`) is owned
by `wiki_write` — you supply `title`, `tags`, `aliases`; never hand-edit it.

## Naming & linking rules

- **One concept per page.** If a page starts covering two things, split it and
  link the halves.
- **Titles are canonical and stable.** Renaming breaks inbound links, so choose
  the durable name up front. Put alternate names in `aliases` (search matches
  them) rather than making duplicate pages.
- **Link liberally**, including to pages that don't exist yet — they surface as
  `broken` in `wiki_links` and become the "wanted pages" queue.
- **A hub page** (title `index`) links the top-level topics; keep it current as
  the wiki grows.
- Slugs are derived by the tool (lowercased, dash-joined). Never compute or type
  a slug yourself; pass the human title.

## The three maintenance loops

These are the engine of the pattern. Run the relevant loop rather than treating
the wiki as a one-shot document generator.

### Ingest (new material arrives)

1. Read the new source. Identify the concepts it covers.
2. For each concept: `wiki_search` to find an existing page.
   - Exists → `wiki_read`, merge the new facts, `wiki_write` the same title
     (preserves `created`, bumps `updated`). Do not append blindly — integrate.
   - Missing → `wiki_write` a new atomic page, linking related concepts.
3. Add the source to each touched page's `## Sources`.
4. Link new `[[concepts]]` even if their pages don't exist yet.

### Query (a question comes in)

1. `wiki_search` the term(s); `wiki_read` the top page(s).
2. Answer **from the wiki**, citing the page slug(s).
3. If the wiki is missing or thin on a high-value topic, answer from general
   knowledge **and backfill**: `wiki_write` a page so the next ask is cheaper.
   Backfilling is not optional busywork — it is how the wiki compounds.

### Lint (periodic health check)

Run when asked to "check/clean/maintain the wiki", or after a big ingest:

1. `wiki_list` to enumerate every page (slug, tags, updated, links).
2. **Broken links / wanted pages**: for each page, `wiki_links` and collect
   `broken`. These are the highest-value pages to write next.
3. **Orphan pages**: pages whose `wiki_links` shows empty `backlinks` and that
   aren't the `index` hub — link them from a relevant page or the hub.
4. **Stale conclusions**: pages with an old `updated` whose sources have moved
   on — flag for re-ingest.
5. **Weak/duplicate pages**: near-duplicate titles should merge (keep one,
   `wiki_delete` the other, repoint its backlinks); stubs should be filled.
6. Report findings as a short punch-list and fix the cheap ones immediately.

## Anti-patterns (what went wrong before)

- **Don't** emit a big Markdown blob in chat and call it a wiki — that's a
  one-shot document, not the pattern. Use `wiki_write` so it persists and links.
- **Don't** export to HTML and delete the `wiki/*.md` sources. The Markdown
  layer is the wiki; HTML is a disposable read-only view you can regenerate.
- **Don't** skip the lint loop. Broken links (like a `[[Home]]` that 404s) are a
  lint failure, not a rendering bug.

## Domain conventions (adapt per wiki)

Fill this in when seeding a wiki for a specific topic — e.g. tag taxonomy,
required sections, citation style, what counts as an atomic concept in this
domain. Keep it short; it's guidance, not bureaucracy.
