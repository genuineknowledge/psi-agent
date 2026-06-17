---
name: arxiv
description: Search arXiv for papers by topic and return titles, authors, and abstracts.
---

Use the arXiv API to find relevant research papers.

**Search:**
```bash
# Basic search
curl "https://export.arxiv.org/api/query?search_query=all:transformer+attention&max_results=5&sortBy=relevance"

# Search by category
curl "https://export.arxiv.org/api/query?search_query=cat:cs.LG+AND+all:reinforcement+learning&max_results=5"

# Search by author
curl "https://export.arxiv.org/api/query?search_query=au:Vaswani&max_results=5"
```

**Parse the response** — arXiv returns Atom XML. Key fields:
- `<entry><title>` — paper title
- `<author><name>` — authors
- `<summary>` — abstract
- `<id>` — URL (e.g. `https://arxiv.org/abs/2310.12345`)
- `<published>` — publication date

**Workflow:**
1. Search with `bash` + `curl` using relevant keywords.
2. Extract titles and abstracts from the XML response.
3. Present results as a numbered list with: title, authors, date, URL, one-line summary.
4. For a specific paper, use `web_fetch` on its abstract page for full details.

**Tips:**
- Use `+AND+` between terms for narrower results.
- Category codes: `cs.LG` (ML), `cs.AI` (AI), `cs.CL` (NLP), `cs.CV` (vision), `stat.ML` (stats ML).
- Max results per query: 100. Use `start=N` for pagination.
