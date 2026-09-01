---
name: gif-search
description: "Search and download animated GIFs (and stickers) from a hosted GIF API using curl + jq through the bash tool. Covers keyword search, trending, translate (phrase->single GIF), random, get-by-id, autocomplete, and downloading a chosen rendition to a local file for delivery via [SEND:]. Uses Giphy's REST API (api.giphy.com) as the default provider after Tenor's shutdown; the recipe is provider-agnostic curl+jq. Needs a GIF API key (GIPHY_API_KEY) and curl + jq installed. Use when the user wants to find, preview, or download a GIF/reaction/sticker."
category: media
---

# GIF search (找 GIF / 表情动图)

Search and download animated GIFs and stickers from a hosted GIF search API,
entirely with `curl` + `jq` through the **bash** tool. No dedicated Python tool and
**no extra Python dependencies** — it is shell only.

Deliver finished GIF files to the user with a `[SEND:<absolute-path>]` line, the same
way `text_to_speech` / `image-generation` deliver their output files.

Reply in Chinese unless the user clearly uses another language.

## Provider (important)

**Google's Tenor API was fully shut down on 2026-06-30** and now returns errors for
every request, so this skill does **not** use Tenor. The default provider here is
**Giphy** (`https://api.giphy.com/v1`), which is still operating and publishes an
official Tenor→Giphy migration guide. The curl+jq recipe is provider-agnostic: if you
later switch to another host (e.g. Klipy), only the base URL, the key param name, and the
JSON field paths change — the shape of the workflow (search → pick a rendition → download
→ `[SEND:]`) stays the same.

## Prerequisites

- **`curl` and `jq` installed.** Check with `command -v curl jq`. On Windows the bundled
  msys64 provides both; if `jq` is missing, install it (`winget install jqlang.jq` /
  `choco install jq` / `apt-get install jq`).
- **A Giphy API key** in the environment as `GIPHY_API_KEY`. Get one free at
  <https://developers.giphy.com/> (create an app → copy the API key). Never hard-code or
  echo the key in chat, logs, or committed files — read it from the env only.

```bash
command -v curl jq >/dev/null || { echo "need curl + jq"; exit 1; }
: "${GIPHY_API_KEY:?set GIPHY_API_KEY in the environment first}"
API=https://api.giphy.com/v1
```

All requests below assume `$API` and `$GIPHY_API_KEY` are set as above. **Always
URL-encode the query** — pass it via `curl --get --data-urlencode` rather than pasting raw
text into the URL, so spaces and non-ASCII (Chinese) terms work.

## Never fabricate URLs or IDs (hard rule)

Every GIF id, `giphy.com/gifs/...` page link, and `media*.giphy.com` file URL you give the
user **must come from an actual API response you just received** — copy it verbatim from the
`curl | jq` output. **Do not guess, invent, or hand-craft a giphy.com link, a slug, or an
id from memory.** A made-up link (e.g. `giphy.com/gifs/cat-typing-<random>`) will 404 with
"Oops! There's nothing here" and is worse than admitting you couldn't search.

If you can't run a real query — `jq`/`curl` missing, no `GIPHY_API_KEY`, or the API returned
an error/empty `.data` — then **say so plainly and stop**. Report the exact blocker (which
dependency or the `meta.msg`/HTTP status) and what the user must do (install jq, set
`GIPHY_API_KEY`, try another query). **Never** substitute a "here's roughly how to find one
yourself" answer with a fabricated link in place of a real search result.

## Search

```bash
# Keyword search. --get + --data-urlencode builds a safe, encoded query string.
curl -sG "$API/gifs/search" \
  --data-urlencode "api_key=$GIPHY_API_KEY" \
  --data-urlencode "q=excited cat" \
  --data-urlencode "limit=10" \
  --data-urlencode "rating=pg-13" \
  --data-urlencode "lang=en" \
| jq -r '.data[] | "\(.id)\t\(.title)\t\(.images.original.url)"'
```

Search parameters (query string):
- `api_key` (required), `q` (required, **max 50 chars**, URL-encoded).
- `limit` (default 25, max 50), `offset` (max 4999) for paging.
- `rating` — one of `g`, `pg`, `pg-13`, `r`; omit for all ratings. **Prefer `g` or `pg`
  by default** so results stay safe-for-work unless the user asks otherwise.
- `lang` — 2-letter language for the query (e.g. `zh`, `en`).
- `bundle` — e.g. `messaging_non_clips` to get a curated set of renditions per result.

Stickers use the parallel path `"$API/stickers/search"` (transparent-background GIFs);
everything else is identical.

## Trending / translate / random / by-id

```bash
# Trending right now
curl -sG "$API/gifs/trending" \
  --data-urlencode "api_key=$GIPHY_API_KEY" --data-urlencode "limit=10" \
| jq -r '.data[] | "\(.id)\t\(.title)"'

# Translate: turn a phrase/word into the single best-matching GIF
curl -sG "$API/gifs/translate" \
  --data-urlencode "api_key=$GIPHY_API_KEY" --data-urlencode "s=hungry" \
| jq -r '.data | "\(.id)\t\(.images.original.url)"'

# Random GIF, optionally constrained by a tag
curl -sG "$API/gifs/random" \
  --data-urlencode "api_key=$GIPHY_API_KEY" --data-urlencode "tag=dog" \
| jq -r '.data | "\(.id)\t\(.images.original.url)"'

# Fetch one GIF by its ID (note: single object under .data, not an array)
curl -sG "$API/gifs/<gif_id>" --data-urlencode "api_key=$GIPHY_API_KEY" \
| jq -r '.data.images.original.url'
```

`autocomplete` (`$API/gifs/search/tags?q=...`) and search suggestions
(`$API/tags/related/<term>`) help refine a vague query before searching.

## Pick a rendition

Each result carries an `images` object with many **renditions** (size/format variants).
Common ones:

- `original` — full size; has `url` (GIF), plus `mp4`/`mp4_size` and `webp`/`webp_size`
  and `frames`.
- `downsized` (<2MB), `downsized_medium` (<5MB), `downsized_large` (<8MB),
  `downsized_small` (mp4, <200KB) — size-capped variants; good when you must keep files small.
- `fixed_width` / `fixed_height` (and `_small`, `_still`, `_downsampled` variants) — fixed
  one dimension; `_still` is a single-frame preview image.
- `preview_gif`, `preview` — tiny previews for listing.

Rendition fields: `url`, `width`, `height`, `size` (bytes, string), and for some also
`mp4`/`mp4_size`, `webp`/`webp_size`. **Sizes are strings** in the JSON — cast with
`tonumber` in jq if you compare them.

```bash
# List each result with a size-capped GIF URL (fall back to original if downsized missing)
curl -sG "$API/gifs/search" \
  --data-urlencode "api_key=$GIPHY_API_KEY" --data-urlencode "q=$QUERY" \
  --data-urlencode "limit=10" --data-urlencode "rating=pg" \
| jq -r '.data[] | [.id, .title, (.images.downsized.url // .images.original.url)] | @tsv'
```

## Download a chosen GIF and deliver it

The media URLs (`media*.giphy.com`) are public CDN links and need **no** API key — download
them directly. Save into the workspace and hand the file to the user with `[SEND:]`.

```bash
mkdir -p generated/gifs
ID=<gif_id>
# Resolve the download URL for a specific GIF id, preferring a size-capped rendition
URL=$(curl -sG "$API/gifs/$ID" --data-urlencode "api_key=$GIPHY_API_KEY" \
  | jq -r '.data.images.downsized.url // .data.images.original.url')
OUT="$(pwd)/generated/gifs/$ID.gif"
curl -fsSL "$URL" -o "$OUT" && echo "saved: $OUT"
```

Then, in your reply: one or two sentences describing the GIF, and on its **own line** the
delivery marker with the **absolute** path:

```text
[SEND:C:/Users/.../generated/gifs/<id>.gif]
```

To download several at once, loop over the ids from a search and `curl -fsSL` each media
URL. Keep total size reasonable — prefer `downsized`/`downsized_medium` over `original`
when sending multiple, and don't dump raw bytes into the chat.

## Attribution

Giphy's terms require **"Powered By GIPHY" attribution** and, in product UIs, showing the
Giphy logo. When you surface GIFs to an end user in a product context, include the
attribution. For a one-off download in chat this is less critical, but mention the source
(Giphy) rather than implying the GIF is original.

## Pitfalls

- **Tenor is dead.** Do not point this skill at `tenor.googleapis.com` — every request has
  returned an error since 2026-06-30. Use Giphy (or another live provider).
- **Missing / invalid key.** No `GIPHY_API_KEY`, or a bad key, returns HTTP `401`/`403`
  with a `meta.msg`. Check `command -v jq` and the env var first; never paste the key into
  the URL in a way that gets logged.
- **`translate` and `random` return a single object under `.data`, not an array.** Use
  `.data.images...`, not `.data[]`. Search/trending return arrays (`.data[]`).
- **Sizes are strings.** `.images.original.size` is `"32381"`, not a number — `tonumber`
  before comparing, or size filters silently do nothing.
- **`q` max 50 chars; must be URL-encoded.** Use `--data-urlencode`; raw spaces/Chinese in
  the URL break the request. Over-long queries get truncated or rejected.
- **Rate limits.** Free (beta) keys are rate-limited (per-hour/per-day); a burst of
  requests returns `429`. Batch with `limit=` and page with `offset=` instead of many
  tiny calls. Production volume needs a Giphy production key.
- **Rating default.** Without `rating` you get all ratings including `r`. Pass `rating=g`
  or `pg` unless the user explicitly wants otherwise, especially for shared/chat contexts.
- **`curl -f`.** Use `-f` (fail on HTTP error) when downloading so a 404/403 doesn't write
  an HTML error page into your `.gif` file.

## After any operation

- On **success**: say what you found / downloaded (a sentence), and for a delivered file
  put the absolute path on its own `[SEND:...]` line. Report the id(s) so the user can ask
  for a different rendition.
- On **failure**: don't just say "失败了" — map the error to the cause (jq/curl missing →
  install; no `GIPHY_API_KEY` → set it; `401`/`403` → bad/missing key; `429` → rate limited,
  back off; empty `.data` → no matches, suggest a different `q` or use
  `autocomplete`/suggestions) and quote the actual `meta.msg` or HTTP status. Never ask for
  or echo the API key in chat. **Never paper over a failure with a fabricated giphy.com link
  or a "go search it yourself" workaround** — see the hard rule above.

