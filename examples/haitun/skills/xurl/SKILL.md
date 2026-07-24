---
name: xurl
description: "Interact with X (Twitter) through the official xurl CLI via the bash tool — a curl-like client that speaks the X API v2 and mints/refreshes its own OAuth tokens. Covers posting tweets/replies/threads, deleting tweets, reading users/tweets, recent search, liking/retweeting/following, sending & reading Direct Messages, and uploading media (images/GIF/video) to attach to a post. Wraps the v2 REST API at api.x.com with xurl handling OAuth 2.0 user context, OAuth 1.0a, and app-only bearer auth; requests use xurl PATH with -X/-d/-H like curl, plus xurl media upload for chunked media. No dedicated Python tool and no extra Python dependencies — shell only, same shape as trello/gif-search/github-issues. Needs the xurl binary on PATH and an authenticated app (client id/secret in ~/.xurl). Use when the user wants to post to X, search tweets, DM, follow/like/retweet, or upload media to X/Twitter."
category: social
---

# xurl (X / Twitter 命令行)

Drive **X (Twitter)** entirely through the official **`xurl`** CLI from the **bash**
tool. `xurl` is a curl-like client for the X API: you give it an API **path** and it
attaches the right `Authorization` header, refreshing OAuth tokens as needed. There is
no dedicated Python tool and **no extra Python dependencies** — it is shell only, the
same shape as `trello`, `gif-search`, and `github-issues`.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites

- **`xurl` on PATH.** Check with `command -v xurl`. If missing, install it (it is a Go
  binary): `npm install -g @xdevplatform/xurl`, or
  `curl -fsSL https://raw.githubusercontent.com/xdevplatform/xurl/main/install.sh | bash`
  (installs to `~/.local/bin`, no sudo), or `go install github.com/xdevplatform/xurl@latest`.
  On Windows the bundled msys64 shell can run the installed binary once it is on PATH.
- **An authenticated X developer app.** Credentials and tokens live in `~/.xurl` (YAML,
  one entry per app). `xurl` manages this file — **never read, echo, or commit `~/.xurl`,
  client secrets, bearer tokens, or the consumer/access-token secrets** into chat, logs,
  or files. Reference them by name only. Set up auth once (see below), then reuse it.
- **`jq`** is handy for parsing responses. Check with `command -v jq`; install via
  `winget install jqlang.jq` / `choco install jq` / `apt-get install jq` if missing.

```bash
command -v xurl >/dev/null || { echo "need xurl on PATH — see install notes"; exit 1; }
xurl auth status   # shows which apps/users/tokens are configured; ▸ marks the default
```

If `auth status` shows no configured token, stop and tell the user which auth flow to run
(below) — do **not** attempt to invent credentials or bypass auth.

## Authentication (one-time setup)

`xurl` supports three auth modes. Pick per what the task needs, then let `xurl` remember it.

```bash
# Register the app once (client id/secret come from the X developer portal).
xurl auth apps add my-app --client-id "$X_CLIENT_ID" --client-secret "$X_CLIENT_SECRET"

# OAuth 2.0 user context — needed to post, DM, like, follow (acts as a user).
xurl auth oauth2 --app my-app                 # opens a browser to approve
xurl auth oauth2 --app my-app --headless      # remote/headless host: prints a URL to approve elsewhere

# App-only bearer — read-only endpoints (search, lookups) at higher app limits.
cat token.txt | xurl auth app-only -          # read token from stdin, avoids shell history

# OAuth 1.0a — some legacy endpoints (e.g. classic media/v1.1) require it.
xurl auth oauth1 --consumer-key "$K" --consumer-secret "$KS" \
  --access-token "$T" --token-secret "$TS"

# Make one app+user the default so later commands need no --app/--username.
xurl auth default my-app          # default app
xurl auth default my-app alice    # default app + default user
```

Pass credentials from the environment (as above) rather than pasting literals, so secrets
never land in the transcript. In `--headless` mode `xurl` prints an authorization URL, you
approve it out of band, and paste back the redirect URL (or just the `code`) — the failed
page load is expected.

## Making requests (the core pattern)

Give `xurl` an API **path** (it prepends `https://api.x.com`). Method with `-X`, JSON body
with `-d`, headers with `-H`, and choose the auth with `--auth oauth2|oauth1|app` (or
`--username alice` to pick a specific OAuth2 account). Omit `--auth` to use the default.

```bash
xurl /2/users/me                                        # who am I (GET, default auth)
xurl --auth app /2/users/by/username/xdevelopers        # read-only lookup, app-only bearer
xurl -X POST /2/tweets -d '{"text":"Hello from xurl 👋"}'  # post a tweet (needs oauth2 user)
```

Every call returns the raw X API JSON. Pipe through `jq` to read the parts you need.
**Never fabricate ids** (tweet id, user id, media id, dm id): every id you act on must come
from a real response you just received — copy it verbatim. A made-up id fails with
`400`/`404` or touches the wrong object.

## Post, reply, thread, delete

```bash
# Simple post. Capture the new tweet id from the response.
xurl -X POST /2/tweets -d '{"text":"发条推文测试 ✅"}' | jq -r '.data.id'

# Reply to an existing tweet (reply.in_reply_to_tweet_id must be a real id).
xurl -X POST /2/tweets \
  -d '{"text":"回复内容","reply":{"in_reply_to_tweet_id":"<tweet_id>"}}' | jq -r '.data.id'

# Thread: post the head, then chain each next tweet as a reply to the previous id.
id=$(xurl -X POST /2/tweets -d '{"text":"1/ 开头"}' | jq -r '.data.id')
id=$(xurl -X POST /2/tweets -d "{\"text\":\"2/ 续\",\"reply\":{\"in_reply_to_tweet_id\":\"$id\"}}" | jq -r '.data.id')

# Attach uploaded media (see Media section for how to get media ids).
xurl -X POST /2/tweets -d '{"text":"带图","media":{"media_ids":["<media_id>"]}}'

# Delete a tweet you own.
xurl -X DELETE /2/tweets/<tweet_id> | jq '.data.deleted'
```

## Read users and tweets

```bash
# A user by handle, with a few extra fields.
xurl --auth app '/2/users/by/username/xdevelopers?user.fields=description,public_metrics' \
  | jq '.data'

# A single tweet with author + metrics expanded.
xurl --auth app '/2/tweets/<tweet_id>?expansions=author_id&tweet.fields=created_at,public_metrics' \
  | jq '{text:.data.text, metrics:.data.public_metrics}'

# A user's recent tweets (get the user id first, then their timeline).
uid=$(xurl --auth app /2/users/by/username/<handle> | jq -r '.data.id')
xurl --auth app "/2/users/$uid/tweets?max_results=10&tweet.fields=created_at" \
  | jq -r '.data[] | "\(.created_at)\t\(.text)"'
```

## Search (recent)

Recent search covers roughly the last 7 days. URL-encode the query in the path; app-only
bearer is the usual auth for search.

```bash
# Latest tweets matching a query. Use single quotes so operators survive the shell.
xurl --auth app '/2/tweets/search/recent?query=from%3Axdevelopers%20-is%3Aretweet&max_results=10&tweet.fields=created_at' \
  | jq -r '.data[] | "\(.id)\t\(.created_at)\t\(.text)"'

# Paginate with next_token from the previous response's meta.
xurl --auth app '/2/tweets/search/recent?query=anyio&max_results=10&next_token=<token>' \
  | jq '{next: .meta.next_token, hits: [.data[].id]}'
```

Common query operators: `from:handle`, `to:handle`, `#tag`, `"exact phrase"`, `-is:retweet`,
`-is:reply`, `lang:en`, `has:media`. Spaces and `:` must be percent-encoded in the path
(`%20`, `%3A`).

## Engage: like, retweet, follow

These act as the authenticated user, so they need OAuth 2.0 user context. Get your own id
once (`xurl /2/users/me | jq -r '.data.id'`) and reuse it as `<me>`.

```bash
me=$(xurl /2/users/me | jq -r '.data.id')

xurl -X POST /2/users/$me/likes    -d '{"tweet_id":"<tweet_id>"}'   # like
xurl -X POST /2/users/$me/retweets -d '{"tweet_id":"<tweet_id>"}'   # retweet
xurl -X POST /2/users/$me/following -d '{"target_user_id":"<user_id>"}'  # follow

# Undo variants use DELETE on the sub-resource by id:
xurl -X DELETE /2/users/$me/likes/<tweet_id>
xurl -X DELETE /2/users/$me/retweets/<tweet_id>
xurl -X DELETE /2/users/$me/following/<target_user_id>
```

## Direct Messages

Send and read DMs (OAuth 2.0 user context with DM scopes).

```bash
# DM into an existing conversation by dm_conversation_id.
xurl -X POST /2/dm_conversations/<conversation_id>/messages \
  -d '{"text":"你好，这是一条私信"}' | jq '.data'

# Start / message a 1:1 conversation with a user by their id (creates it if needed).
xurl -X POST /2/dm_conversations/with/<participant_user_id>/messages \
  -d '{"text":"第一条私信"}' | jq '.data'

# Read recent DM events across all conversations.
xurl '/2/dm_events?max_results=20&dm_event.fields=created_at,sender_id,text' \
  | jq -r '.data[] | "\(.created_at)\t\(.sender_id)\t\(.text)"'

# Read one conversation's messages.
xurl '/2/dm_conversations/<conversation_id>/dm_events?max_results=20' | jq '.data'
```

## Media upload (attach images / GIF / video)

`xurl media upload` drives the chunked upload flow and auto-detects type/category from the
file extension. The returned **media id** is what you put in a tweet's `media.media_ids`.

```bash
# Upload and grab the media id (works for jpg/png/gif/mp4).
mid=$(xurl media upload path/to/photo.jpg | jq -r '.data.id // .media_id_string // .media_id')

# Video/GIF are processed async — wait until X finishes before attaching.
mid=$(xurl media upload path/to/clip.mp4 | jq -r '.data.id // .media_id_string // .media_id')
xurl media status --wait "$mid"

# Then post with the media attached.
xurl -X POST /2/tweets -d "{\"text\":\"带媒体\",\"media\":{\"media_ids\":[\"$mid\"]}}"
```

If you need to drive chunks manually, the path-style endpoints are
`/2/media/upload/initialize`, `/2/media/upload/<media_id>/append` (with `-F path`),
`/2/media/upload/<media_id>/finalize`, and `xurl '/2/media/upload?command=STATUS&media_id=<id>'`.
Prefer `xurl media upload` unless you have a specific reason not to.

## Get a raw token for other tooling

`xurl token` prints a valid OAuth2 bearer to stdout (refreshing if expired) without opening
a browser — script-safe when another tool needs the header directly.

```bash
TOKEN=$(xurl token) && curl -H "Authorization: Bearer $TOKEN" https://api.x.com/2/users/me
```

Treat that token like a password: use it inline, never echo or persist it.

## Handling errors

The X API returns a JSON error body with an HTTP status. Inspect it instead of assuming
success — `xurl` passes the body through.

```bash
resp=$(xurl -X POST /2/tweets -d '{"text":"..."}')
echo "$resp" | jq -e '.data.id' >/dev/null 2>&1 \
  || { echo "X API error:"; echo "$resp" | jq '.errors // .detail // .'; exit 1; }
```

Common cases:
- **401 / no token** — auth not set up or expired. Re-run the matching `xurl auth ...` flow;
  report the blocker, don't fabricate a result.
- **403 `client-forbidden` / `client-not-enrolled`** — OAuth worked but the app lacks
  access. Platform-side fix: move the app to the *Pay-per-use* package and *Production*
  environment in the X developer console. Not a local/callback problem.
- **429** — rate limited. Back off and retry later; quote the reset info if present.
- **400 `duplicate content`** — X rejects identical repeated tweets; vary the text.

Quote the actual HTTP status and error body. **Never** invent an id, secret, or a
fabricated success. **Never** read, echo, or commit `~/.xurl`, tokens, or client secrets.
