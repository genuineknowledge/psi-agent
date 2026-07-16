---
name: llm-wiki
description: "Build and maintain a self-growing, interlinked Markdown knowledge base (Andrej Karpathy's \"LLM wiki\" pattern): compile what you learn into durable, cross-referenced pages under <workspace>/wiki/ instead of re-searching raw sources every time. Each page is Markdown with YAML frontmatter (title/tags/aliases/timestamps) and a body that links other pages with [[wikilink]] syntax. Use when asked to record, organize, or look up domain knowledge (LLM concepts, papers, techniques, glossary terms) as a browsable second brain, or to answer from / extend that wiki. Drives the dedicated wiki_* tools (wiki_write/read/search/list/links/delete) — this skill is the discipline for when and how to use them."
category: coding
---

# LLM Wiki

Maintain a persistent, interlinked Markdown knowledge base following Andrej
Karpathy's "LLM wiki" idea: instead of re-searching raw documents on every
question, **compile** knowledge into durable, cross-referenced pages that live
under `<workspace>/wiki/` and compound over time. Prefer answering from the wiki;
when you learn something new or the user shares a source, sink it into a page so
the next question is cheaper.

This skill is the **discipline layer**: it decides *when* to record knowledge and
*how* to organize, name, and link pages. All read/write/search operations go
through the dedicated **`wiki_*` tools**, which own the storage format (slug
derivation, YAML frontmatter, timestamps, link indexing). Do not hand-craft wiki
files with the generic `write`/`edit`/`bash` tools — let the tools keep the
format and link graph consistent.

Reply in Chinese unless the user clearly uses another language.

## The tools

| Tool | Purpose |
|------|---------|
| `wiki_write(title, content, tags="", aliases="", overwrite=True)` | Create or update a page. Slug, frontmatter, and `created`/`updated` timestamps are handled for you; re-writing the same title updates that page and preserves its original `created`. |
| `wiki_read(title_or_slug)` | Read one page's body + metadata (tags, aliases, timestamps, outgoing links). |
| `wiki_search(query, limit=20)` | Full-text search across titles, tags, aliases, and bodies; title/tag hits rank above body hits, each result carries a snippet. |
| `wiki_list(tag="")` | Table of contents: every page's slug/title/tags/updated/links, optionally filtered to one tag. |
| `wiki_links(title_or_slug)` | A page's link graph: `outgoing`, `backlinks`, and `broken` (links to pages that don't exist yet). |
| `wiki_delete(title_or_slug)` | Delete a page; reports the `orphaned_backlinks` whose `[[links]]` are now broken. |

## Where the wiki lives

- Root: `wiki/` under the workspace — created automatically on first `wiki_write`.
- One page per concept, filename `wiki/<slug>.md`. The slug is derived by the
  tool from the title (lowercased, non-alphanumerics collapsed to single dashes),
  e.g. "Rotary Positional Embeddings" → `rotary-positional-embeddings`. You pass
  the human title; you never compute the slug yourself.

## Page format (owned by the tools)

Each page is Markdown with YAML frontmatter (`title`, `slug`, `tags`, `aliases`,
`created`, `updated`) followed by the body. You supply `title`, `content`,
`tags`, and `aliases` to `wiki_write`; the tool writes the frontmatter and
timestamps. Body content follows these conventions:

- **Body links** use `[[Page Title]]` or `[[Page Title|display text]]`. The link
  target resolves by slugifying the text before the `|`. Link liberally — even to
  pages that don't exist yet; they surface as `broken` in `wiki_links` and become
  "wanted pages" to write next.
- Keep pages **atomic**: one concept per page, a few paragraphs. When a page
  starts covering two things, split it and link the halves instead.
- Add a `## Sources` section with the URLs/refs the page was compiled from.
- **tags** are kebab-case topic labels; **aliases** are alternate names people
  search by (e.g. `RoPE` for "Rotary Positional Embeddings") — `wiki_search`
  matches them too.

Example body passed as `content`:

```markdown
RoPE injects absolute position by rotating the query/key vectors, so the dot
product in [[Self-Attention]] depends only on the *relative* offset. Contrast
with [[Absolute Positional Embeddings]]. Widely used in [[LLaMA]] and others.

## Sources
- <https://arxiv.org/abs/2104.09864>
```

## Workflow

### 1. Before writing — always search first

Avoid duplicate pages. Check whether the concept already exists with
`wiki_search` (matches titles, aliases, and bodies), or scan the table of
contents with `wiki_list`:

```
wiki_search  query="rotary RoPE"
wiki_list                          # or wiki_list tag="attention"
```

If a matching page exists, **update it** rather than creating a near-duplicate:
`wiki_read` it, merge the new facts, and `wiki_write` the same title again (the
tool preserves `created` and bumps `updated`).

### 2. Writing / updating a page

- New or updated page: call `wiki_write(title, content, tags, aliases)`. Writing
  an existing title updates it in place — read first if you want to preserve
  existing body text and merge rather than replace.
- Whenever you state a fact that has (or should have) its own page, link it with
  `[[…]]` in the `content`, even if that page doesn't exist yet.

### 3. Answering from the wiki

When the user asks something the wiki might cover: `wiki_search` for the term,
`wiki_read` the top page(s), answer from them, and cite the page slug. If the
wiki is missing or thin on the topic, answer normally **and** offer to compile a
new page with `wiki_write` so it's captured for next time.

### 4. Traversing links (backlinks & broken links)

The wiki is a graph; keep it navigable with `wiki_links(title_or_slug)`:

- `outgoing` — the pages this page links to.
- `backlinks` — the pages that link *to* this one (check these before renaming or
  deleting).
- `broken` — `[[links]]` pointing at pages that don't exist yet. These are the
  best candidates to write next.

### 5. Housekeeping

- Renaming a concept: `wiki_write` under the new title (creates the new slug),
  then use `wiki_links` on the old page to find inbound `[[old title]]` links,
  repoint them, and `wiki_delete` the old page.
- Deleting a page: `wiki_delete` reports `orphaned_backlinks`; go fix or drop
  those now-broken `[[…]]` links so the graph stays consistent.
- Optionally keep a `wiki/index.md` hub page (via `wiki_write` title "index")
  that links the top-level topics.

## Notes

- Never store secrets in the wiki. It's plain Markdown the user reads and edits.
- The value compounds: the more you compile into linked pages, the less you
  re-search — that's the whole point of the pattern.
