---
name: weather
description: Fetch current weather and forecasts using wttr.in via curl.
---

**Current weather for a location:**
```bash
curl "wttr.in/London?format=3"
# Output: London: ⛅️  +18°C

curl "wttr.in/Tokyo?format=2"
# Output: Tokyo: ⛅️ 🌡️+22°C 🌬️↗13km/h
```

**Full forecast (text):**
```bash
curl "wttr.in/Paris"
```

**JSON output (for structured data):**
```bash
curl -s "wttr.in/Berlin?format=j1" | python3 -m json.tool
# Key fields: current_condition[0].temp_C, weatherDesc, humidity, windspeedKmph
```

**Format codes:**
- `%C` — weather condition description
- `%t` — temperature (feels like)
- `%h` — humidity
- `%w` — wind speed and direction
- `%p` — precipitation
- `%m` — moon phase

**Custom format:**
```bash
curl "wttr.in/NYC?format=%C+%t+%h"
# Output: Partly cloudy +20°C 65%
```

**Location formats:**
- City name: `wttr.in/London`
- Airport code: `wttr.in/LAX`
- Coordinates: `wttr.in/-33.87,151.21`
- IP (current location): `wttr.in`

**Tips:**
- Add `?lang=zh` for Chinese, `?lang=ja` for Japanese, etc.
- No API key required.
