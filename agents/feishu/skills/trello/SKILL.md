---
name: trello
description: "Manage Trello boards, lists, and cards with curl + jq through the bash tool. Covers listing/reading boards, lists and cards; creating lists and cards; updating cards (rename, edit description, move between lists via idList, reposition, set due dates); archiving (closed=true) and deleting cards; commenting; and adding labels, members, and checklists. Uses the Trello REST API (https://api.trello.com/1) with an API key + token passed as query params. No dedicated Python tool and no extra Python dependencies — shell only. Needs TRELLO_API_KEY and TRELLO_TOKEN in the environment plus curl + jq installed. Use when the user wants to view, create, move, update, or organize Trello cards/lists/boards."
category: productivity
---

# Trello (看板管理)

Manage **Trello** boards, lists and cards entirely with `curl` + `jq` through the
**bash** tool. There is no dedicated Python tool and **no extra Python dependencies** —
it is shell only, the same shape as `gif-search` and `github-issues`.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites

- **`curl` and `jq` installed.** Check with `command -v curl jq`. On Windows the bundled
  msys64 provides both; if `jq` is missing install it (`winget install jqlang.jq` /
  `choco install jq` / `apt-get install jq`).
- **A Trello API key and token** in the environment as `TRELLO_API_KEY` and
  `TRELLO_TOKEN`. Get the key at <https://trello.com/power-ups/admin> (create a Power-Up →
  API key), then generate a token from that same page (the "Token" link grants your
  account's read/write access). **Never hard-code or echo the key/token** in chat, logs,
  or committed files — read them from the env only.

```bash
command -v curl jq >/dev/null || { echo "need curl + jq"; exit 1; }
: "${TRELLO_API_KEY:?set TRELLO_API_KEY in the environment first}"
: "${TRELLO_TOKEN:?set TRELLO_TOKEN in the environment first}"
API=https://api.trello.com/1
```

Every request appends credentials as query params: `key=$TRELLO_API_KEY&token=$TRELLO_TOKEN`.
Pass them (and every other param) with `curl -sG --data-urlencode` so names and non-ASCII
(Chinese) values are URL-encoded safely instead of pasted raw into the URL. Note: Forge and
OAuth2 apps cannot use this REST resource — the key+token auth above is the supported path.

## Never fabricate IDs (hard rule)

Every board / list / card **id** you act on must come from an actual API response you just
received — copy it verbatim from the `curl | jq` output. **Do not guess, invent, or
hand-craft a Trello id from memory.** A made-up id (e.g. moving a card to
`idList=5abbe...`) will fail with `400`/`404` or silently touch the wrong object. If you
can't run a real query (`curl`/`jq` missing, no token, or the API returned an error) then
**say so plainly and stop**: report the exact blocker (which dependency, or the HTTP
status / error body) and what the user must do (install jq, set the env vars, check the id).

## Orient first (read-only)

Start every session by discovering the real ids before you mutate anything.

```bash
# The boards you can see. `me` is the authenticated member.
curl -sG "$API/members/me/boards" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "fields=name,url,closed" \
| jq -r '.[] | select(.closed|not) | "\(.id)\t\(.name)\t\(.url)"'

# Lists on a board (the columns). Grab the list id you'll add cards to.
curl -sG "$API/boards/<board_id>/lists" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "fields=name,pos,closed" \
| jq -r '.[] | "\(.id)\t\(.name)"'

# Cards on a board (all lists at once). Use fields to keep output small.
curl -sG "$API/boards/<board_id>/cards" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "fields=name,idList,due,dueComplete,url" \
| jq -r '.[] | "\(.id)\t\(.idList)\t\(.name)"'

# Cards in one specific list.
curl -sG "$API/lists/<list_id>/cards" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "fields=name,due,url" \
| jq -r '.[] | "\(.id)\t\(.name)"'

# Read one full card.
curl -sG "$API/cards/<card_id>" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
| jq '{id,name,desc,idList,idBoard,due,dueComplete,labels:[.labels[].name],url}'
```

## Create lists and cards

```bash
# Create a list (column) on a board. `name` required; `pos` = top|bottom|<number>.
curl -sG -X POST "$API/boards/<board_id>/lists" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "name=待办 / To Do" --data-urlencode "pos=bottom" \
| jq -r '"\(.id)\t\(.name)"'

# Create a card. Only idList is required; name/desc/pos/due optional.
curl -sG -X POST "$API/cards" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "idList=<list_id>" \
  --data-urlencode "name=写周报" \
  --data-urlencode "desc=汇总本周进展与下周计划" \
  --data-urlencode "pos=top" \
  --data-urlencode "due=2026-07-20T09:00:00.000Z" \
| jq -r '"\(.id)\t\(.name)\t\(.url)"'
```

## Update, move, archive, delete a card

Moving and archiving both use the **same** update endpoint `PUT /cards/{id}` — there is
no separate "move" call; you set `idList`.

```bash
# Rename / edit description / set due date (any subset of fields).
curl -sG -X PUT "$API/cards/<card_id>" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "name=写周报(已更新)" \
  --data-urlencode "desc=新描述" \
  --data-urlencode "due=2026-07-21T09:00:00.000Z" \
  --data-urlencode "dueComplete=true" \
| jq -r '"\(.id)\t\(.name)"'

# Move a card to another list (optionally reposition within it).
curl -sG -X PUT "$API/cards/<card_id>" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "idList=<target_list_id>" --data-urlencode "pos=top" \
| jq -r '.idList'

# Archive a card (reversible — set closed=false to restore).
curl -sG -X PUT "$API/cards/<card_id>" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "closed=true" | jq -r '.closed'

# Delete a card PERMANENTLY (not reversible — prefer archive unless the user is sure).
curl -sG -X DELETE "$API/cards/<card_id>" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN"
```

**Archive vs delete:** archiving (`closed=true`) hides the card but keeps it recoverable;
`DELETE` erases it for good. Default to **archive**; only `DELETE` when the user explicitly
asks to permanently remove a card.

## Comments, labels, members, checklists

```bash
# Add a comment to a card.
curl -sG -X POST "$API/cards/<card_id>/actions/comments" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "text=已完成初稿，请评审" | jq -r '.id'

# Attach an existing label (get label ids from the board first).
curl -sG "$API/boards/<board_id>/labels" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "fields=name,color" | jq -r '.[] | "\(.id)\t\(.color)\t\(.name)"'
curl -sG -X POST "$API/cards/<card_id>/idLabels" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "value=<label_id>"

# Assign a member to a card (find member ids via /boards/<id>/members).
curl -sG -X POST "$API/cards/<card_id>/idMembers" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "value=<member_id>"

# Add a checklist to a card.
curl -sG -X POST "$API/cards/<card_id>/checklists" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN" \
  --data-urlencode "name=验收清单" | jq -r '"\(.id)\t\(.name)"'
```

## Handling errors

Trello returns a plain-text or JSON error body with a matching HTTP status. Capture the
status and body instead of assuming success:

```bash
resp=$(curl -sG -w '\n%{http_code}' "$API/members/me/boards" \
  --data-urlencode "key=$TRELLO_API_KEY" --data-urlencode "token=$TRELLO_TOKEN")
code=$(printf '%s' "$resp" | tail -n1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = 200 ] || { echo "Trello API $code: $body"; exit 1; }
printf '%s' "$body" | jq -r '.[].name'
```

Common statuses: `401` invalid key/token, `400` bad/missing param (e.g. missing `idList`
on create, malformed `due`), `404` unknown id, `429` rate limited (back off and retry).
Quote the actual HTTP status and body. **Never** invent an id or paper over a failure with
a fabricated result — see the hard rule above. Never ask for or echo the key/token in chat.

