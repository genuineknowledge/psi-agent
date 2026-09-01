---
name: maps
description: "Geocoding, points-of-interest, routing, and timezone lookups over OpenStreetMap data using curl + jq through the bash tool. Forward/reverse geocode with Nominatim (nominatim.openstreetmap.org), find nearby POIs with the Overpass API (overpass-api.de), compute driving/walking/cycling routes and distances with the OSRM demo server (router.project-osrm.org), and resolve the IANA timezone + UTC offset for a coordinate with timeapi.io. All shell only — no dedicated Python tool and no extra Python dependencies. Needs curl + jq installed; no API key required, but every request MUST send a descriptive User-Agent. Use when the user wants to turn a place name into coordinates (or back), find what's near a point, get a route/travel distance/ETA between places, or know the timezone of a location."
category: research
---

# Maps (地理编码 / 兴趣点 / 路线 / 时区)

Turn place names into coordinates and back, find nearby points of interest,
compute routes and travel distances, and resolve timezones — all over
**OpenStreetMap** data, entirely with `curl` + `jq` through the **bash** tool.
No dedicated Python tool and **no extra Python dependencies** — it is shell only.
**No API key is required.**

Reply in Chinese unless the user clearly uses another language.

Four free public services, each an HTTP GET/POST returning JSON:

| Need | Service | Base URL |
|------|---------|----------|
| Geocode (name→coords) / reverse (coords→address) | **Nominatim** | `https://nominatim.openstreetmap.org` |
| Nearby POIs by tag | **Overpass API** | `https://overpass-api.de/api/interpreter` |
| Routes, distance, ETA | **OSRM** demo server | `https://router.project-osrm.org` |
| Timezone + UTC offset for a coord | **timeapi.io** | `https://timeapi.io/api` |

## Prerequisites

- **`curl` and `jq` installed.** Check with `command -v curl jq`. On Windows the bundled
  msys64 provides both; if `jq` is missing, install it (`winget install jqlang.jq` /
  `choco install jq` / `apt-get install jq`).

```bash
command -v curl jq >/dev/null || { echo "need curl + jq"; exit 1; }
```

## Send a real User-Agent (hard rule for the OSM services)

Nominatim, Overpass, and OSRM all run on **donated servers** and their usage policies
**require a descriptive `User-Agent`** that identifies the application. A missing or fake
User-Agent gets you a `403 Access denied` / temporary block. Set one once and reuse it:

```bash
UA="psi-agent-maps-skill/1.0"
```

Pass it on **every** OSM request with `curl -A "$UA"`. `timeapi.io` does not require it.

## Rate limits & etiquette (important)

These are shared free endpoints, not production infrastructure:

- **Nominatim: an absolute maximum of 1 request per second.** `sleep 1` between calls.
- **Overpass**: keep an embedded `[timeout:NN]` and don't hammer; a few queries is fine,
  loops are not. Heavy areas can be slow — raise the timeout, don't retry in a tight loop.
- **OSRM demo server**: for light/demo use only; no bulk matrix jobs.

If you get `403 Access denied`, you've been rate-limited or your User-Agent was rejected —
**wait ~15s and slow down**, don't retry immediately in a loop. Never fake another app's
User-Agent (that gets you blocked harder). For heavy/production use, host your own
instance instead of the public servers.

## Never fabricate coordinates, addresses, or routes (hard rule)

Every coordinate, address, place name, distance, duration, and timezone you give the user
**must come from an actual API response you just received** — copy it verbatim from the
`curl | jq` output. **Do not guess or invent** a lat/lon, an address, a road name, a travel
time, or a timezone from memory. A made-up coordinate points to the wrong place on Earth.

If you can't run a real query — `curl`/`jq` missing, network blocked, `403`, or the service
returned zero results — then **say so plainly and stop**. Report the exact blocker (which
service, the HTTP status, or that the result array was empty) and what the user should try.
**Never** substitute a fabricated location in place of a real lookup.

## Geocode — place name → coordinates (Nominatim)

Build requests with `curl -sG` + `--data-urlencode` so spaces and non-ASCII terms encode
safely. Use `format=jsonv2`; add `addressdetails=1` for a structured address, `limit=N` to
cap results. `accept-language` picks the label language.

```bash
UA="psi-agent-maps-skill/1.0"
curl -sG -A "$UA" "https://nominatim.openstreetmap.org/search" \
  --data-urlencode "q=Eiffel Tower, Paris" \
  --data-urlencode "format=jsonv2" \
  --data-urlencode "addressdetails=1" \
  --data-urlencode "accept-language=zh" \
  --data-urlencode "limit=5" \
| jq -r '.[] | "\(.lat),\(.lon)\t[\(.type)] \(.display_name)"'
```

Useful per-result fields: `lat`, `lon`, `display_name`, `name`, `type`/`category`,
`importance`, `boundingbox`, and (with `addressdetails=1`) the `address` object
(`.address.city`, `.address.country`, …). An **empty `[]`** means no match — broaden the
query, don't invent a hit. `sleep 1` before the next Nominatim call.

## Reverse geocode — coordinates → address (Nominatim)

```bash
UA="psi-agent-maps-skill/1.0"
curl -sG -A "$UA" "https://nominatim.openstreetmap.org/reverse" \
  --data-urlencode "lat=48.8584" \
  --data-urlencode "lon=2.2945" \
  --data-urlencode "format=jsonv2" \
  --data-urlencode "accept-language=zh" \
| jq -r '.display_name // .error'
```

`.display_name` is the full human address; `.address` holds the structured parts. If the
point is in the ocean / unmapped, you get `{"error":...}` — report it, don't fabricate.

## Nearby POIs — what's around a point (Overpass API)

Overpass runs its own query language (Overpass QL) via `POST` to `/api/interpreter`. Pass
the query as the `data` field. `nwr[...]` matches nodes, ways, and relations; `around:R,LAT,LON`
is a radius in **metres**; `out center N;` returns up to N elements with a `center` for
ways/relations. Filter by any OSM tag — common ones: `amenity` (cafe, restaurant, bank,
hospital, pharmacy, school…), `shop`, `tourism` (hotel, museum, attraction), `leisure`.

```bash
UA="psi-agent-maps-skill/1.0"
LAT=48.8584 LON=2.2945 RADIUS=300 TAG_K=amenity TAG_V=cafe
curl -s -A "$UA" "https://overpass-api.de/api/interpreter" --data-urlencode \
  "data=[out:json][timeout:25];nwr[\"$TAG_K\"=\"$TAG_V\"](around:$RADIUS,$LAT,$LON);out center 20;" \
| jq -r '.elements[]
    | "\(.tags.name // "(unnamed)") | \(.lat // .center.lat),\(.lon // .center.lon)"
    + (if .tags.opening_hours then " | \(.tags.opening_hours)" else "" end)'
```

Nodes carry `.lat`/`.lon` directly; ways/relations carry `.center.lat`/`.center.lng` (from
`out center`) — the `// .center.lat` fallback above handles both. To search a named area
instead of a radius, first geocode the place with Nominatim to get a coordinate, then run
the `around:` query. An empty `.elements` array means nothing tagged that way nearby.

## Routes, distance & ETA (OSRM)

OSRM takes **`lon,lat`** pairs (longitude first!) separated by `;`, in the path. Pick the
profile in the URL: the demo server serves `driving` (also try `walking` / `cycling` if the
deployment supports them). `overview=false` skips geometry; `steps=true` adds turn-by-turn.

```bash
UA="psi-agent-maps-skill/1.0"
# from (2.2945,48.8584) to (2.3376,48.8606) — note lon,lat order
curl -s -A "$UA" \
  "https://router.project-osrm.org/route/v1/driving/2.2945,48.8584;2.3376,48.8606?overview=false" \
| jq -r 'if .code=="Ok"
    then .routes[0] | "distance=\(.distance/1000|.*100|round/100) km, duration=\(.duration/60|.*10|round/10) min"
    else "OSRM error: \(.code) \(.message // "")" end'
```

`.distance` is **metres**, `.duration` is **seconds** — convert for the user (km / minutes).
Chain more than two `lon,lat` points for a multi-stop trip. To route between two place
**names**, geocode each with Nominatim first, then feed the coordinates here (remember to
flip to `lon,lat`). Check `.code=="Ok"` before trusting a route; `"NoRoute"` means no path.

## Timezone + UTC offset for a coordinate (timeapi.io)

OpenStreetMap has no native timezone endpoint, so this uses **timeapi.io** (free, no key,
no User-Agent needed) to map a coordinate to its IANA timezone and current offset.

```bash
curl -s "https://timeapi.io/api/timezone/coordinate?latitude=48.8584&longitude=2.2945" \
| jq -r '"\(.timeZone) | local=\(.currentLocalTime) | UTC offset \(.currentUtcOffset.seconds/3600) h | DST=\(.hasDayLightSaving)"'
```

Returns `.timeZone` (e.g. `Europe/Paris`), `.currentLocalTime`, `.currentUtcOffset.seconds`,
`.standardUtcOffset`, and `.hasDayLightSaving`. Combine with Nominatim: geocode a place →
feed its `lat`/`lon` here to answer "what time is it in <place>".

## Present results

Give the user the concrete values copied verbatim from the parsed output: **coordinates**
(`lat,lon`), the **address / place name**, **distance in km and duration in minutes** for
routes, POI **names with their coordinates**, and the **IANA timezone with UTC offset**.
Link a location on the OSM map when helpful: `https://www.openstreetmap.org/?mlat=<lat>&mlon=<lon>#map=17/<lat>/<lon>`.
Always credit the data: **© OpenStreetMap contributors (ODbL)**. If a query returns empty
or errors, say so and suggest a fix (broaden the term, widen the radius, check the spelling)
— never invent a location to fill the gap.
