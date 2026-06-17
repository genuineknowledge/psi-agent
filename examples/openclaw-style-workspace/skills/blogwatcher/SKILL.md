---
name: blogwatcher
description: Monitor RSS/Atom feeds and summarise new entries.
---

Use `bash` with `curl` to fetch and parse RSS/Atom feeds.

**Fetch a feed:**
```bash
curl -s "https://example.com/feed.xml"
```

**Parse XML entries** — key fields:
- RSS 2.0: `<item>` → `<title>`, `<link>`, `<description>`, `<pubDate>`
- Atom: `<entry>` → `<title>`, `<link href>`, `<summary>`, `<updated>`

**Quick one-liner to list titles:**
```bash
curl -s "https://example.com/feed.xml" | grep -oP '(?<=<title>)[^<]+'
```

**Workflow:**
1. Fetch the feed URL with `bash` + `curl`.
2. Extract `<title>`, `<link>`, `<pubDate>` / `<updated>` for each item.
3. Filter to items newer than a reference date if tracking updates.
4. Summarise each item: title, date, URL, one-line description.
5. For full article content, use `web_fetch` on the item link.

**Tracking new entries:**
- Store the latest seen `pubDate` or `updated` in `memory.md`.
- On next run, compare dates and report only newer items.

**Tips:**
- Many sites provide both RSS and Atom; prefer Atom for better date support.
- Use `web_search` to find a site's feed URL if not obvious (search: `site:example.com "feed.xml" OR "rss"`).
