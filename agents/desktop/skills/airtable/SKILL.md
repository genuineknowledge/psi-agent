---
name: airtable
description: "Read and write Airtable bases through the Airtable Web REST API (api.airtable.com/v0) using curl + jq via the bash tool. Covers record CRUD (list/create/update/delete), filtering with filterByFormula, offset pagination, batch writes (<=10), and upsert (performUpsert + fieldsToMergeOn) that matches on a merge field instead of a record id. Also finds baseId/tableId via the meta API. No dedicated Python tool and no extra dependencies - shell only. Needs a Personal Access Token in AIRTABLE_API_KEY (starts with pat...) plus curl + jq. Use when the user wants to query, add, edit, upsert, or delete rows in an Airtable table."
category: productivity
---

# Airtable (Airtable REST API / 表格记录读写)

Read and write an Airtable base entirely with `curl` + `jq` through the **bash** tool,
against the Airtable Web API. **No dedicated Python tool and no extra Python
dependencies** — it is shell only. Covers record **CRUD**, **filtering**, offset
**pagination**, **batch** writes, and **upsert**.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites

- **`curl` and `jq` installed.** Check with `command -v curl jq`. On Windows the bundled
  msys64 provides both; if `jq` is missing, install it (`winget install jqlang.jq` /
  `choco install jq` / `apt-get install jq`).
- **A Personal Access Token (PAT)** in the environment as `AIRTABLE_API_KEY`. PATs start
  with `pat...`; create one at <https://airtable.com/create/tokens> with the scopes you
  need (`data.records:read`, `data.records:write`, `schema.bases:read`) and add the target
  bases to the token's **Access** list. Legacy API keys were fully disabled on 2024-02-01
  and no longer authenticate — you must use a PAT. Never hard-code, echo, or commit the
  token; read it from the env only.

```bash
command -v curl jq >/dev/null || { echo "need curl + jq"; exit 1; }
: "${AIRTABLE_API_KEY:?set AIRTABLE_API_KEY (a pat... token) in the environment first}"
API=https://api.airtable.com/v0
AUTH="Authorization: Bearer $AIRTABLE_API_KEY"
```

All requests below assume `$API` and `$AUTH` are set as above. Rate limit is **5
requests/sec per base**; a `429` means back off (honor the `Retry-After` header).

## Object IDs (prefer IDs over names)

Bases are `app...`, tables `tbl...`, records `rec...`, fields `fld...`. **IDs never
change; names can** — prefer IDs in anything you save or reuse. `$TABLE` in the paths
below may be either the table id (`tbl...`) or the URL-encoded table name.

## Find baseId & tableId (meta API)

```bash
# List bases this token can access -> id + name
curl -s -H "$AUTH" "$API/meta/bases" \
| jq -r '.bases[] | "\(.id)\t\(.name)"'

# List tables + full schema for a base (also surfaces field names/ids and
# single/multi-select options.choices)
BASE_ID=appXXXXXXXXXXXXXX
curl -s -H "$AUTH" "$API/meta/bases/$BASE_ID/tables" \
| jq -r '.tables[] | "\(.id)\t\(.name)"'
```

## List records + filterByFormula

```bash
BASE_ID=appXXXXXXXXXXXXXX
TABLE=Tasks   # table name or tblXXXXXXXXXXXXXX

# filterByFormula must be URL-encoded. Let curl --data-urlencode do it (with -G the
# data is appended to the query string). This also encodes sort[] / fields[] safely.
curl -sG -H "$AUTH" "$API/$BASE_ID/$TABLE" \
  --data-urlencode "filterByFormula={Status}='Todo'" \
  --data-urlencode "maxRecords=20" \
  --data-urlencode "sort[0][field]=Priority" \
  --data-urlencode "sort[0][direction]=desc" \
| jq -r '.records[] | "\(.id)\t\(.fields.Name)\t\(.fields.Status)"'
```

Common `filterByFormula` patterns (single-quote string literals):
- exact match `{Email}='user@example.com'`
- case-insensitive contains `FIND('bug', LOWER({Title}))`
- combine `AND({Status}='Todo', {Priority}='High')` / `OR(...)`
- not empty `NOT({Assignee}='')`
- date `IS_AFTER({Due}, TODAY())`

Other useful query params: `fields[]=Name` (return only some fields), `view=Grid view`
(apply a view's filters/sort), `pageSize` (<=100), `maxRecords`, `cellFormat`.

## Pagination (offset loop)

List endpoints return **at most 100 records per page** — a hard limit. If the response
has a top-level `offset`, pass it back to get the next page; loop until `offset` is absent:

```bash
BASE_ID=appXXXXXXXXXXXXXX; TABLE=Tasks; OFFSET=""
while : ; do
  resp=$(curl -sG -H "$AUTH" "$API/$BASE_ID/$TABLE" \
           --data-urlencode "pageSize=100" \
           ${OFFSET:+--data-urlencode "offset=$OFFSET"})
  echo "$resp" | jq -r '.records[] | "\(.id)\t\(.fields.Name)"'
  OFFSET=$(echo "$resp" | jq -r '.offset // empty')
  [ -z "$OFFSET" ] && break
done
```

## Create records (POST)

```bash
# Single record
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  "$API/$BASE_ID/$TABLE" \
  -d '{"fields":{"Name":"New task","Status":"Todo","Priority":"High"}}' \
| jq -r '.id'

# Batch: up to 10 records per request. "typecast": true auto-coerces values and can
# create new single/multi-select options on the fly.
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  "$API/$BASE_ID/$TABLE" \
  -d '{"typecast": true, "records": [
        {"fields": {"Name": "Task A", "Status": "Todo"}},
        {"fields": {"Name": "Task B", "Status": "In progress"}}
      ]}' \
| jq -r '.records[].id'
```

## Update records (PATCH)

`PATCH` merges the supplied fields and preserves the rest. (`PUT` would clear any field
you omit — prefer `PATCH` unless you truly want a full replace.)

```bash
REC=recXXXXXXXXXXXXXX
curl -s -X PATCH -H "$AUTH" -H "Content-Type: application/json" \
  "$API/$BASE_ID/$TABLE/$REC" \
  -d '{"fields":{"Status":"Done"}}' \
| jq -r '.id'
```

## Upsert (performUpsert + fieldsToMergeOn)

`PATCH` the **table** (no record id in the path) with `performUpsert.fieldsToMergeOn`.
Airtable creates records whose merge-field value is new and patches records whose
merge-field value already exists — no need to look up the record id first. Batch of <=10,
`fieldsToMergeOn` may list up to 3 fields.

```bash
curl -s -X PATCH -H "$AUTH" -H "Content-Type: application/json" \
  "$API/$BASE_ID/$TABLE" \
  -d '{
        "performUpsert": {"fieldsToMergeOn": ["Email"]},
        "typecast": true,
        "records": [
          {"fields": {"Email": "user@example.com", "Status": "Active", "Name": "Jo"}}
        ]
      }' \
| jq -r '.records[] | "\(.id)\t\(.fields.Status)"'
# The response also carries createdRecordIds / updatedRecordIds telling you which happened.
```

## Delete records (DELETE)

```bash
# Single
curl -s -X DELETE -H "$AUTH" "$API/$BASE_ID/$TABLE/$REC" | jq -r '.deleted'

# Batch (up to 10). The records[] bracket param must be URL-encoded as %5B%5D; let
# curl --data-urlencode + -G build it.
curl -sG -X DELETE -H "$AUTH" "$API/$BASE_ID/$TABLE" \
  --data-urlencode "records[]=rec1XXXXXXXXXXXX" \
  --data-urlencode "records[]=rec2XXXXXXXXXXXX" \
| jq -r '.records[] | "\(.id)\t\(.deleted)"'
```

## Field body shapes (selected)

- Text: `"Name": "hello"` · Number: `"Score": 42` · Checkbox: `"Done": true`
- Single select: `"Status": "Todo"` · Multi-select: `"Tags": ["urgent", "bug"]`
- Date: `"Due": "2026-04-01"` · DateTime: `"At": "2026-04-01T14:30:00.000Z"`
- Linked record: `"Owner": ["recXXXX..."]` (array of linked record ids)
- Attachment: `"Files": [{"url": "https://…"}]`

Add top-level `"typecast": true` to auto-coerce values (e.g. string -> number) or create
new select options. Without it, an unknown single-select option returns
`INVALID_MULTIPLE_CHOICE_OPTIONS`.

## Security (hard rules)

- **Read `AIRTABLE_API_KEY` from the environment only.** Never hard-code it, echo it in
  chat/logs, or write it into a committed file. Pass it via the `Authorization` header.
- Prefer a **read-only token** for query-only work; only use a write-scoped token when the
  task actually writes. Scope the token to just the needed bases.
- A `403` on one base but not another usually means **that base isn't on the token's
  Access list** — not a scope/auth bug. Add the base to the token or use the right token.

## After any operation

- On **success**: say what changed in a sentence and report the affected record id(s)
  (and for upsert, whether each was created or updated via
  `createdRecordIds`/`updatedRecordIds`) so the user can follow up.
- On **failure**: don't just say "失败了" — map the error to its cause and quote the actual
  HTTP status and the structured code from the response `errors[]`/`error` (e.g.
  `AUTHENTICATION_REQUIRED`, `INVALID_MULTIPLE_CHOICE_OPTIONS`, `ROW_DOES_NOT_EXIST`):
  - `curl`/`jq` missing → install them.
  - no `AIRTABLE_API_KEY` → set it (a `pat...` token).
  - `401` → bad/expired token · `403` → base not on token Access list or missing scope.
  - `422` → bad field name/value or formula (check meta API for exact field names).
  - `429` → rate limited (5 req/s/base), back off and honor `Retry-After`.
  - empty `.records` → no matches; suggest a different `filterByFormula` or check the view.
- **Never fabricate** a record id, base id, or field value — every id you give the user
  must come from an actual API response you just received. If you can't run a real request
  (missing dep, no key, or an API error), say so plainly and report the exact blocker.
