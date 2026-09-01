---
name: weather
description: "Query current weather and forecasts for any place using Open-Meteo through curl + jq via the bash tool — no API key and no extra Python dependencies. Two APIs, both key-free: the Geocoding API (geocoding-api.open-meteo.com/v1/search) turns a place name like 'Tokyo' or 'Shibuya' into latitude/longitude/timezone, and the Forecast API (api.open-meteo.com/v1/forecast) returns current conditions, an hourly forecast, and a daily forecast for those coordinates. Weather condition is a WMO weather_code integer that this skill maps to a human label. Use when the user asks what the weather is like now, the temperature/wind/humidity/precipitation at a location, or a forecast (today, hourly, or multi-day) for a city or coordinate. Needs curl + jq installed. Present only values copied verbatim from a real API response — never invent weather data. For plain geocoding/routing without weather use the maps skill instead."
category: research
---

# Weather (当前天气 / 预报查询)

Query **current weather and forecasts** for any place on earth via **Open-Meteo** — a free,
**key-free** weather API. Entirely `curl` + `jq` through the **bash** tool. No dedicated Python
tool and **no extra Python dependencies** — it is shell only.

Reply in Chinese unless the user clearly uses another language.

Two APIs, both free and needing **no API key**:

| Need | Method + endpoint |
|------|-------------------|
| Place name → coordinates + timezone | `GET https://geocoding-api.open-meteo.com/v1/search` |
| Current weather / hourly / daily forecast | `GET https://api.open-meteo.com/v1/forecast` |

The typical flow is **two calls**: geocode the place name to get `latitude`/`longitude`/`timezone`,
then pass those to the forecast endpoint. If the user already gives you a coordinate, skip
straight to the forecast call.

**Related skill:** for geocoding, POIs, routing, and timezone lookups *without* weather, use the
[[maps]] skill (OpenStreetMap, also key-free). Use `weather` when the user wants actual weather
or forecast data.

## Prerequisites

- **`curl` and `jq` installed.** Check with `command -v curl jq`. On Windows the bundled msys64
  provides both; if `jq` is missing, install it (`winget install jqlang.jq` / `choco install jq`
  / `apt-get install jq`).
- **No API key and no account needed** for non-commercial use. Open-Meteo is free for reasonable
  request volumes — don't hammer it in tight loops.

```bash
command -v curl jq >/dev/null || { echo "need curl + jq"; exit 1; }
```

## Never fabricate weather data (hard rule)

Every temperature, wind speed, humidity, precipitation figure, condition, and forecast time you
give the user **must come from an actual API response you just received** — copy it verbatim from
the `curl | jq` output. **Do not guess or invent** weather from memory or from what is "typical"
for a place or season.

If you can't run a real query — `curl`/`jq` missing, network blocked, an HTTP error, an
`"error": true` body, or an empty `results` array from geocoding — then **say so plainly and
stop**. Report the exact blocker (which endpoint, the HTTP status / `reason` message, or that
geocoding returned no match) and what the user should try. **Never** substitute made-up weather
to fill the gap.

## Step 1 — Geocode: place name → coordinates (`/v1/search`)

Turn a free-text place name (or postal code) into `latitude`, `longitude`, and the location's
`timezone`. Only `name` is required. Useful optional params: `count` (results, default 10),
`language` (e.g. `zh`), `countryCode` (ISO-3166 alpha-2 filter).

```bash
PLACE="Tokyo"
curl -s -G "https://geocoding-api.open-meteo.com/v1/search" \
  --data-urlencode "name=$PLACE" \
  --data-urlencode "count=5" \
  --data-urlencode "language=zh" \
| jq -r 'if .results then .results[]
    | "\(.name)\t\(.admin1 // "-")\t\(.country // "-")\t\(.latitude),\(.longitude)\t\(.timezone)"
  else "ERROR / no match: \(.reason // "empty results")" end'
```

Each result gives `.name`, `.latitude`, `.longitude`, `.timezone` (a tz-database name like
`Asia/Tokyo`), plus `.country`, `.admin1` (region/state), `.population`, `.feature_code`. Pick the
match the user meant (disambiguate by country/region if several) and carry its `latitude`,
`longitude`, and `timezone` into step 2. Note: a 2-character `name` only matches exactly; 3+ chars
do fuzzy matching. For the Americas, longitudes are negative (west of Greenwich).

## Step 2 — Forecast: coordinates → weather (`/v1/forecast`)

One endpoint serves current conditions, hourly, and daily forecasts — you choose which by sending
the `current`, `hourly`, and/or `daily` parameters (each a comma-separated list of variables, **no
spaces**). Required: `latitude`, `longitude`. **`timezone` is required whenever you request
`daily`** — pass the tz name from geocoding, or `auto` to resolve it from the coordinates. Other
useful params: `forecast_days` (0–16, default 7), `past_days` (0–92), `temperature_unit`
(`celsius`/`fahrenheit`), `wind_speed_unit` (`kmh`/`ms`/`mph`/`kn`).

### Current weather

```bash
LAT=35.6895; LON=139.6917; TZ="Asia/Tokyo"
curl -s -G "https://api.open-meteo.com/v1/forecast" \
  --data-urlencode "latitude=$LAT" \
  --data-urlencode "longitude=$LON" \
  --data-urlencode "current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation" \
  --data-urlencode "timezone=$TZ" \
| jq -r 'if .error then "ERROR: \(.reason)" else .current as $c |
    "时间: \($c.time)",
    "气温: \($c.temperature_2m)\(.current_units.temperature_2m)  体感: \($c.apparent_temperature)\(.current_units.apparent_temperature)",
    "天气: \($c.weather_code) (见 WMO 对照表)",
    "湿度: \($c.relative_humidity_2m)%",
    "风速: \($c.wind_speed_10m)\(.current_units.wind_speed_10m)",
    "降水: \($c.precipitation)\(.current_units.precipitation)"
  end'
```

### Daily forecast (multi-day)

```bash
LAT=35.6895; LON=139.6917; TZ="Asia/Tokyo"
curl -s -G "https://api.open-meteo.com/v1/forecast" \
  --data-urlencode "latitude=$LAT" \
  --data-urlencode "longitude=$LON" \
  --data-urlencode "daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max" \
  --data-urlencode "timezone=$TZ" \
  --data-urlencode "forecast_days=7" \
| jq -r 'if .error then "ERROR: \(.reason)" else .daily as $d |
    range(0; ($d.time|length)) as $i |
    "\($d.time[$i])\t\($d.temperature_2m_min[$i])~\($d.temperature_2m_max[$i])°C\t降水 \($d.precipitation_sum[$i])mm (\($d.precipitation_probability_max[$i])%)\tWMO \($d.weather_code[$i])\t风 \($d.wind_speed_10m_max[$i])"
  end'
```

### Hourly forecast

Request `hourly=temperature_2m,precipitation,precipitation_probability,weather_code,wind_speed_10m`.
The response arrays in `.hourly` are index-aligned with `.hourly.time`; slice to the range the user
cares about (e.g. next 12 hours) rather than dumping all 168. Use `forecast_days=1` to keep it to
today.

Common variables — **current/hourly**: `temperature_2m`, `apparent_temperature`,
`relative_humidity_2m`, `precipitation`, `precipitation_probability`, `weather_code`,
`cloud_cover`, `wind_speed_10m`, `wind_direction_10m`, `is_day`. **daily**: `weather_code`,
`temperature_2m_max`/`_min`, `apparent_temperature_max`/`_min`, `precipitation_sum`,
`precipitation_probability_max`, `wind_speed_10m_max`, `sunrise`, `sunset`, `uv_index_max`.

## WMO weather_code → label

`weather_code` is a [WMO 4677](https://open-meteo.com/en/docs) integer. Map it for the user:

| Code | 天气 | Code | 天气 |
|------|------|------|------|
| 0 | 晴 Clear | 51/53/55 | 毛毛雨 弱/中/强 Drizzle |
| 1 | 大部晴 Mainly clear | 56/57 | 冻毛毛雨 Freezing drizzle |
| 2 | 多云 Partly cloudy | 61/63/65 | 雨 小/中/大 Rain |
| 3 | 阴 Overcast | 66/67 | 冻雨 Freezing rain |
| 45/48 | 雾 / 雾凇 Fog | 71/73/75 | 雪 小/中/大 Snow |
| 77 | 米雪 Snow grains | 80/81/82 | 阵雨 弱/中/强 Rain showers |
| 85/86 | 阵雪 Snow showers | 95 | 雷暴 Thunderstorm |
| 96/99 | 雷暴伴冰雹 Thunderstorm w/ hail | | |

## Present results

Give the user the concrete values **copied verbatim** from the parsed output: the **place** (name
+ region/country you geocoded), the **local time** of the reading, **temperature** (and apparent
temperature), **condition** (WMO code translated via the table above), **humidity**, **wind**, and
**precipitation** — with the units from `*_units` in the response. For forecasts, present a compact
per-day or per-hour table. State the **timezone** the times are in. Credit the source: **data from
Open-Meteo (open-meteo.com), CC BY 4.0**. If geocoding returns no match or the forecast call errors
/ is blocked, say so and suggest a fix (spell the place differently, add a country, or pass a
coordinate directly) — never invent weather to fill the gap.
