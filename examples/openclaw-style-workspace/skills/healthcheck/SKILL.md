---
name: healthcheck
description: Check HTTP service health endpoints and report status.
---

**Basic HTTP health check:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health
curl -s http://localhost:8080/health | python3 -m json.tool
```

**Check with timeout:**
```bash
curl -sf --max-time 5 http://localhost:8080/health && echo "OK" || echo "FAIL"
```

**Check multiple services:**
```bash
for svc in "http://localhost:8080/health" "http://localhost:9090/ready"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$svc")
  echo "$svc → $code"
done
```

**Common health endpoint conventions:**
- `/health` — basic alive check (returns 200 OK)
- `/ready` or `/readyz` — ready to accept traffic
- `/live` or `/livez` — process is alive (Kubernetes liveness)
- `/metrics` — Prometheus metrics endpoint

**What to check in the response:**
- HTTP status: 200 = healthy, 503 = degraded, connection refused = down
- Response body: look for `"status": "ok"` or `"healthy": true`
- Response time: `curl -w "%{time_total}s\n"` — flag if > expected threshold

**Process check (if no HTTP endpoint):**
```bash
pgrep -f "my-service-name" && echo "running" || echo "not running"
systemctl is-active myservice
```
