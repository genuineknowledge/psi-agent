---
name: arxiv
description: "Search arXiv scholarly papers by keyword, title, author, abstract, category, or arXiv ID using curl + xmllint through the bash tool. Wraps the official arXiv API (export.arxiv.org/api/query), which returns Atom XML — parsed with xmllint local-name() XPath, no jq and no extra Python dependencies. Covers fielded search (all:/ti:/au:/abs:/cat:), boolean queries (AND/OR/ANDNOT), lookup by id_list, sorting by relevance/date, and paging. Needs curl + xmllint installed; no API key required. Use when the user wants to find, look up, or list academic papers / preprints on arXiv."
category: research
---

# arXiv paper search (搜 arXiv 论文)

Search and look up scholarly papers on **arXiv** entirely with `curl` + `xmllint`
through the **bash** tool. No dedicated Python tool and **no extra Python
dependencies** — it is shell only. **No API key is required.**

The arXiv API returns **Atom XML** (not JSON), so this skill parses with `xmllint`
using `local-name()` XPath. `jq` is *not* used.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites

- **`curl` and `xmllint` installed.** Check with `command -v curl xmllint`. On Windows
  the bundled msys64 / an Anaconda install provides both; `xmllint` ships with libxml2
  (`apt-get install libxml2-utils` / `brew install libxml2` if missing).

```bash
command -v curl xmllint >/dev/null || { echo "need curl + xmllint"; exit 1; }
API=https://export.arxiv.org/api/query
```

**Always use `https://`** — plain `http://export.arxiv.org` returns a 301 redirect with
an empty body and no results.

## Never fabricate arXiv IDs, titles, or URLs (hard rule)

Every arXiv id (e.g. `2101.00001`), `arxiv.org/abs/...` page link, `arxiv.org/pdf/...`
file URL, paper title, and author name you give the user **must come from an actual API
response you just received** — copy it verbatim from the `curl | xmllint` output. **Do
not guess, invent, or hand-craft an arXiv id, a title, or a link from memory.** A made-up
id will resolve to the wrong paper or 404 and is worse than admitting you couldn't search.

If you can't run a real query — `curl`/`xmllint` missing, network blocked, or the feed
returned zero `entry` elements — then **say so plainly and stop**. Report the exact
blocker (which dependency, the HTTP status, or that `totalResults`/entry count was 0) and
what the user should try (install the tool, refine the query). **Never** substitute a
fabricated citation in place of a real search result.

## Search

Build every request with `curl -sG` + `--data-urlencode` so spaces and non-ASCII terms
are encoded safely.

```bash
# Keyword search across all fields, newest first.
curl -sG "$API" \
  --data-urlencode "search_query=all:large language models" \
  --data-urlencode "start=0" \
  --data-urlencode "max_results=10" \
  --data-urlencode "sortBy=submittedDate" \
  --data-urlencode "sortOrder=descending" \
  -o /tmp/arxiv.xml
```

### Query fields (prefix inside `search_query`)

- `all:` — anywhere (title, abstract, authors, comments…)
- `ti:` — title, `abs:` — abstract, `au:` — author, `co:` — comment
- `cat:` — arXiv category, e.g. `cat:cs.AI`, `cat:hep-th`, `cat:stat.ML`
- `jr:` — journal ref, `rn:` — report number

### Boolean queries (use spaces, never a literal `+`)

Combine terms with `AND`, `OR`, `ANDNOT`, separated by **spaces** — `--data-urlencode`
turns spaces into `%20`, which arXiv accepts. **Do not** type a literal `+` between
terms: it gets encoded to `%2B` and silently returns **zero** results.

```bash
# Papers in cs.AI by an author named Bengio:
--data-urlencode "search_query=cat:cs.AI AND au:Bengio"

# Group with parentheses and quote exact phrases with %22 ... %22:
--data-urlencode 'search_query=ti:%22retrieval augmented%22 ANDNOT cat:cs.CL'
```

### Parameters (query string)

- `search_query` — the fielded query above. **Either** `search_query` **or** `id_list`
  (or both) must be present.
- `start` (default 0) and `max_results` (default 10) — paging. Keep `max_results` modest
  (≤ 100 for interactive use; the API allows larger with `start` paging but throttles).
- `sortBy` — `relevance` (default), `lastUpdatedDate`, or `submittedDate`.
- `sortOrder` — `ascending` or `descending`.

## Look up specific papers by ID

```bash
# One or more arXiv ids (comma-separated), no search_query needed:
curl -sG "$API" --data-urlencode "id_list=2101.00001,1706.03762" -o /tmp/arxiv.xml
```

Accepts both new-style ids (`2101.00001`, optionally `v2`) and old-style
(`cond-mat/0011267`). Combine `id_list` with `search_query` to filter within a set.

## Parse the Atom XML (xmllint local-name recipe)

The feed uses a default namespace, so bind nothing and match by `local-name()`. Pull
`opensearch:totalResults` first, then iterate entries **by index** so fields from
different entries never get flattened together.

```bash
X=/tmp/arxiv.xml
total=$(xmllint --xpath 'string(//*[local-name()="totalResults"])' "$X" 2>/dev/null)
n=$(xmllint --xpath 'count(//*[local-name()="entry"])' "$X" 2>/dev/null)
echo "total matches on arXiv: ${total:-0}; entries in this page: ${n:-0}"

for i in $(seq 1 "${n:-0}"); do
  e="(//*[local-name()=\"entry\"])[$i]"
  id=$(xmllint --xpath "string($e/*[local-name()=\"id\"])" "$X")
  title=$(xmllint --xpath "string($e/*[local-name()=\"title\"])" "$X" | tr -s ' \n' ' ')
  cat=$(xmllint --xpath "string($e/*[local-name()=\"primary_category\"]/@term)" "$X")
  pub=$(xmllint --xpath "string($e/*[local-name()=\"published\"])" "$X")
  pdf=$(xmllint --xpath "string($e/*[local-name()=\"link\"][@title=\"pdf\"]/@href)" "$X")
  authors=$(xmllint --xpath "$e/*[local-name()=\"author\"]/*[local-name()=\"name\"]/text()" "$X" 2>/dev/null | paste -sd'; ')
  printf '%s\n  %s\n  %s | %s | %s\n  pdf: %s\n\n' "$title" "$authors" "$cat" "${pub%%T*}" "$id" "$pdf"
done
```

Useful per-entry fields: `id` (abs URL — the trailing token is the arXiv id), `title`,
`summary` (abstract), `published` / `updated`, `author/name`, `primary_category/@term`,
every `category/@term`, and links (`@title="pdf"` for the PDF, `rel="alternate"` for the
HTML page). Trim whitespace in `title`/`summary` with `tr -s ' \n' ' '` — arXiv wraps
those fields across lines.

## Rate limits & etiquette (important)

arXiv asks callers to **wait at least 3 seconds between requests** and avoid bursts. When
paging or issuing several queries, `sleep 3` between calls. A single search is fine; do
not hammer the endpoint in a loop.

## Present results

Give the user the **title, authors, primary category, date, arXiv id, and links** for each
hit — all copied verbatim from the parsed output. Link the abstract page
(`https://arxiv.org/abs/<id>`) and the PDF. If the user wants the file delivered, download
the PDF and hand it off with a `[SEND:<absolute-path>]` line, the same way other skills
deliver output files:

```bash
curl -sL "https://arxiv.org/pdf/<id>" -o "/tmp/<id>.pdf"
```

If `totalResults` is 0 or the query looks too narrow, say so and suggest broadening the
terms or dropping a field prefix — never invent a paper to fill the gap.
