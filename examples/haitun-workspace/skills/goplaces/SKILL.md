---
name: goplaces
description: "Query Google Places (New) — the Places API v1 — for real place data using curl + jq through the bash tool. Text Search (places:searchText) turns a free-text query like 'coffee near Shibuya station' into a list of matching places; Nearby Search (places:searchNearby) lists places of given types within a radius of a lat/lon; Place Details (GET places/PLACE_ID) fetches full detail (address, rating, hours, phone, website) for one place. All shell only — no dedicated Python tool and no extra Python dependencies. Needs curl + jq installed and a GOOGLE_MAPS_API_KEY in the environment with the Places API (New) enabled. Every request MUST send an X-Goog-FieldMask header (there is no default field set — omitting it is an error) and the field mask affects billing, so request only the fields you need. Use when the user wants to find a business/POI by name or description, list what's near a coordinate, or get details (hours, phone, rating, website) for a specific place. For key-free OpenStreetMap geocoding/routing use the maps skill instead."
category: research
---

# Google Places (查询地点 / 附近搜索 / 地点详情)

Query **Google Places API (New)** — the `places.googleapis.com/v1` service — to find
businesses and points of interest by name/description, list places near a coordinate, and
fetch full detail for a specific place. Entirely `curl` + `jq` through the **bash** tool.
No dedicated Python tool and **no extra Python dependencies** — it is shell only.

Reply in Chinese unless the user clearly uses another language.

Three endpoints, all under `https://places.googleapis.com/v1`:

| Need | Method + endpoint |
|------|-------------------|
| Free-text search (name/description → places) | `POST /places:searchText` |
| Nearby search (types within a radius of a coord) | `POST /places:searchNearby` |
| Place detail by place id | `GET /places/PLACE_ID` |

**Key-free alternative:** for plain OpenStreetMap geocoding, POI, routing, and timezone
lookups that need **no API key**, use the [[maps]] skill instead. Use `goplaces` when the
user specifically wants Google's data (ratings, business hours, phone, website) or Google's
place matching.

## Prerequisites

- **`curl` and `jq` installed.** Check with `command -v curl jq`. On Windows the bundled
  msys64 provides both; if `jq` is missing, install it (`winget install jqlang.jq` /
  `choco install jq` / `apt-get install jq`).
- **A Google Maps API key** in the environment as `GOOGLE_MAPS_API_KEY`, with the
  **Places API (New)** enabled for the project and billing turned on. Create/enable it in the
  Google Cloud console (APIs & Services → enable "Places API (New)" → Credentials → API key).

```bash
command -v curl jq >/dev/null || { echo "need curl + jq"; exit 1; }
: "${GOOGLE_MAPS_API_KEY:?set GOOGLE_MAPS_API_KEY in the environment first}"
```

The key travels in the **`X-Goog-Api-Key`** header on every request (never in the URL, so it
does not leak into logs/history). Do not echo the key value back to the user.

## Field mask is mandatory (hard rule) and it costs money

Every Places (New) call **requires an `X-Goog-FieldMask` header** — there is **no default set
of returned fields**, and omitting the mask makes the request fail with an error. Rules:

- Comma-separated field paths, **no spaces anywhere** in the list.
- Search responses nest results under `places`, so mask paths are prefixed: `places.displayName`,
  `places.formattedAddress`, `places.location`, `places.rating`, `places.id`. Place **Details**
  returns a single object, so its paths are unprefixed: `displayName`, `formattedAddress`, ….
- **The mask drives billing.** Fields fall into SKU tiers (Essentials < Pro < Enterprise <
  Enterprise+Atmosphere). `id`/`formattedAddress` are cheap; `displayName`/`location` are Pro;
  `rating`/`regularOpeningHours`/`internationalPhoneNumber`/`websiteUri` are Enterprise. **Request
  only the fields you actually need**, and avoid the `*` wildcard mask outside quick debugging.

## Never fabricate places, addresses, ratings, or hours (hard rule)

Every place name, address, coordinate, rating, phone number, opening hour, and website you give
the user **must come from an actual API response you just received** — copy it verbatim from the
`curl | jq` output. **Do not guess or invent** any of these from memory.

If you can't run a real query — `curl`/`jq` missing, `GOOGLE_MAPS_API_KEY` unset, network
blocked, an HTTP error, or an empty `places` array — then **say so plainly and stop**. Report the
exact blocker (which endpoint, the HTTP status / `error.status` message, or that the result was
empty) and what the user should try. **Never** substitute a made-up place to fill the gap.

## Text Search — free text → places (`places:searchText`)

Best when the user describes what they want in words ("拉面 near Tokyo station", "bookstores in
Berlin"). Only `textQuery` is required in the body. Useful optional body fields: `languageCode`
(e.g. `"zh"`), `regionCode` (e.g. `"jp"`), `pageSize` (≤20), `minRating`, `openNow`, and
`locationBias`/`locationRestriction` to steer toward an area.

```bash
: "${GOOGLE_MAPS_API_KEY:?set GOOGLE_MAPS_API_KEY first}"
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.location,places.rating" \
  -d '{"textQuery":"coffee near Shibuya station","languageCode":"zh","pageSize":5}' \
| jq -r 'if .places then .places[]
    | "\(.displayName.text)\t\(.rating // "-")\t\(.location.latitude),\(.location.longitude)\t\(.formattedAddress)\t[\(.id)]"
  else "ERROR: \(.error.status // "unknown") \(.error.message // .)" end'
```

Each result carries the fields you masked: `.id` (place id, feed it to Place Details),
`.displayName.text`, `.formattedAddress`, `.location.latitude`/`.location.longitude`, `.rating`.
An empty/absent `places` means no match — broaden the query, don't invent a hit. The `else`
branch surfaces the API error object (`.error.status`, `.error.message`) instead of pretending
success. Text Search returns at most 60 results across pages (`nextPageToken` for more).

## Nearby Search — places near a coordinate (`places:searchNearby`)

Best when you already have a lat/lon and want a category of places around it. Requires a
`locationRestriction.circle` with a `center` and a `radius` in **metres** (`0 < radius ≤ 50000`).
`includedTypes` filters by place type (e.g. `restaurant`, `cafe`, `pharmacy`, `atm`, `hotel`,
`tourist_attraction`); omit it for all types. `maxResultCount` is 1–20. To search near a place
**name**, geocode it first (Text Search, or the [[maps]] skill's Nominatim) to get a coordinate.

```bash
: "${GOOGLE_MAPS_API_KEY:?set GOOGLE_MAPS_API_KEY first}"
curl -s -X POST "https://places.googleapis.com/v1/places:searchNearby" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.location,places.rating" \
  -d '{
        "includedTypes":["restaurant"],
        "maxResultCount":10,
        "languageCode":"zh",
        "locationRestriction":{"circle":{"center":{"latitude":35.6595,"longitude":139.7005},"radius":500.0}}
      }' \
| jq -r 'if .places then .places[]
    | "\(.displayName.text)\t\(.rating // "-")\t\(.location.latitude),\(.location.longitude)\t[\(.id)]"
  else "ERROR: \(.error.status // "unknown") \(.error.message // .)" end'
```

`radius` is metres from `center`; a `radius` of `0` or `>50000` is rejected. Empty `places`
means nothing of that type in range — widen the radius or drop `includedTypes`, don't fabricate.

## Place Details — full detail for one place (`GET /places/PLACE_ID`)

Take a place `id` from a search result and fetch the rich fields. The id goes **in the URL path**
after `places/`; the field mask here is **unprefixed** (single object, not a `places` array).

```bash
: "${GOOGLE_MAPS_API_KEY:?set GOOGLE_MAPS_API_KEY first}"
PLACE_ID="ChIJN1t_tDeuEmsRUsoyG83frY4"   # from a search result's .id
curl -s -X GET "https://places.googleapis.com/v1/places/$PLACE_ID" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: id,displayName,formattedAddress,rating,userRatingCount,internationalPhoneNumber,websiteUri,regularOpeningHours,googleMapsUri" \
| jq -r 'if .error then "ERROR: \(.error.status) \(.error.message)" else
    "名称: \(.displayName.text)",
    "地址: \(.formattedAddress // "-")",
    "评分: \(.rating // "-") (\(.userRatingCount // 0) 条)",
    "电话: \(.internationalPhoneNumber // "-")",
    "网站: \(.websiteUri // "-")",
    "地图: \(.googleMapsUri // "-")",
    "营业时间:", (.regularOpeningHours.weekdayDescriptions[]? // "-")
  end'
```

Common detail fields to mask as needed: `displayName`, `formattedAddress`, `location`, `rating`,
`userRatingCount`, `internationalPhoneNumber`/`nationalPhoneNumber`, `websiteUri`, `googleMapsUri`,
`regularOpeningHours` (`.weekdayDescriptions[]` are human-readable lines), `priceLevel`, `types`.
Remember each Enterprise field adds cost — mask only what the user asked for.

## Present results

Give the user the concrete values copied verbatim from the parsed output: the **place name**,
**formatted address**, **coordinates** (`lat,lon`), **rating** (and count), **phone / website /
hours** when fetched, and the **place id** so a follow-up detail lookup is possible. Link the
place with its `googleMapsUri` when available. Credit the source: **data © Google**. If a query
returns empty or an error, say so and suggest a fix (broaden the text query, widen the radius,
drop `includedTypes`, check the place id / that the key has Places API (New) enabled) — never
invent a place to fill the gap.
