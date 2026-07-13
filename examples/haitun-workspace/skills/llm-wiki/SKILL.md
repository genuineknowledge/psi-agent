---
name: llm-wiki
description: "Build and maintain a self-growing, interlinked Markdown knowledge base (Andrej Karpathy's \"LLM wiki\" pattern): compile what you learn into durable, cross-referenced pages under <workspace>/wiki/ instead of re-searching raw sources every time. Each page is Markdown with YAML frontmatter (title/tags/aliases/timestamps) and a body that links other pages with [[wikilink]] syntax. Use when asked to record, organize, or look up domain knowledge (LLM concepts, papers, techniques, glossary terms) as a browsable second brain, or to answer from / extend that wiki. Uses only the existing read/write/edit/find_files/list_dir/search_content/bash tools — no dedicated tool, no extra deps."
category: coding
---

# LLM Wiki

Maintain a persistent, interlinked Markdown knowledge base following Andrej
Karpathy's "LLM wiki" idea: instead of re-searching raw documents on every
question, **compile** knowledge into durable, cross-referenced pages that live
under `<workspace>/wiki/` and compound over time. Prefer answering from the wiki;
when you learn something new or the user shares a source, sink it into a page so
the next question is cheaper.

This skill is pure discipline + conventions — it uses only the workspace's
existing file tools (`read`, `write`, `edit`, `find_files`, `list_dir`,
`search_content`, `bash`). There is no dedicated Python tool and no extra
dependency.

Reply in Chinese unless the user clearly uses another language.

## Where the wiki lives

- Root: `wiki/` under the workspace (create it on first write).
- One page per concept: `wiki/<slug>.md`, where `<slug>` is the title
  lowercased with non-alphanumerics collapsed to single dashes
  (e.g. "Rotary Positional Embeddings" → `rotary-positional-embeddings.md`,
  "KV-Cache!!" → `kv-cache.md`).
- Optionally group by topic with subfolders (`wiki/architectures/…`) once the
  base grows, but keep slugs unique across the whole tree so links resolve.

## Page format

Every page is Markdown with a small YAML frontmatter block, then the body:

```markdown
---
title: Rotary Positional Embeddings
slug: rotary-positional-embeddings
tags: [positional-encoding, attention]
aliases: [RoPE]
created: 2026-07-13T10:00:00Z
updated: 2026-07-13T10:00:00Z
---

RoPE injects absolute position by rotating the query/key vectors, so the dot
product in [[Self-Attention]] depends only on the *relative* offset. Contrast
with [[Absolute Positional Embeddings]]. Widely used in [[LLaMA]] and others.

## Sources
- <https://arxiv.org/abs/2104.09864>
```

Rules:
- **Frontmatter keys**: `title` (human title), `slug` (the filename stem),
  `tags` (list, kebab-case), `aliases` (other names people search by),
  `created` / `updated` (ISO-8601 UTC — get them with `bash: date -u +%Y-%m-%dT%H:%M:%SZ`).
- **Body links** use `[[Page Title]]` or `[[Page Title|display text]]`. The link
  target resolves by slugifying the text before the `|`.
- Keep pages **atomic**: one concept per page, a few paragraphs. Split when a
  page tries to cover two things; link them instead.
- Add a `## Sources` section with the URLs/refs the page was compiled from.

## Workflow

### 1. Before writing — always search first

Avoid duplicate pages. Check whether the concept already exists:

```bash
# list the base
list_dir wiki/                     # or: find_files pattern="wiki/**/*.md"
```

Search titles, aliases, and bodies with `search_content` (falls back to
`bash: grep -rin "<term>" wiki/`):

```
search_content  query="rotary|RoPE"  path="wiki"
```

If a matching page exists, **update it** (read → edit) rather than creating a
near-duplicate. Merge new facts into the existing page and bump `updated`.

### 2. Writing / updating a page

- New page: `write` the file with full frontmatter (`created` == `updated` ==
  now).
- Existing page: `read` it, `edit` the body/frontmatter, and set `updated` to
  now while **preserving the original `created`**.
- Whenever you state a fact that has (or should have) its own page, link it with
  `[[…]]` — even if that page doesn't exist yet. Those become "wanted pages" to
  fill in later (see broken-link check below).

### 3. Answering from the wiki

When the user asks a question the wiki might cover: `search_content` for the
term, `read` the top page(s), answer from them, and cite the page slug. If the
wiki is missing or thin on the topic, answer normally **and** offer to compile a
new page so it's captured for next time.

### 4. Traversing links (backlinks & broken links)

The wiki is a graph; keep it navigable.

- **Outgoing links** of a page: `grep -oE '\[\[[^]]+\]\]'` on that file.
- **Backlinks** (who links *to* page X): search the whole base for its title or
  slug:
  ```bash
  grep -rilE '\[\[[^]|]*(Rotary Positional Embeddings|rotary-positional-embeddings)' wiki/
  ```
- **Broken links / wanted pages**: collect every `[[Target]]` across the base,
  slugify each, and flag those with no matching `wiki/<slug>.md`. These are the
  best candidates to write next. A quick pass:
  ```bash
  # list all link targets, then eyeball against existing filenames
  grep -rhoE '\[\[[^]|]+' wiki/ | sed 's/\[\[//' | sort -u
  ```

### 5. Housekeeping

- Renaming a page: rename the file to the new slug, update its `slug`/`title`,
  then fix inbound `[[old title]]` links (find backlinks first).
- Deleting a page: remove the file, then find backlinks and repoint or drop the
  now-broken `[[…]]` links so the graph stays consistent.
- Optionally keep a `wiki/index.md` hub page that links the top-level topics.

## Notes

- All paths are relative to the workspace; on Windows the bundled msys64
  provides `bash`/`grep`/`sed`.
- Never store secrets in the wiki. It's plain Markdown the user reads and edits.
- The value compounds: the more you compile into linked pages, the less you
  re-search — that's the whole point of the pattern.
