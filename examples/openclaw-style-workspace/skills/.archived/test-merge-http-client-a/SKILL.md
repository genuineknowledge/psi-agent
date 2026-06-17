---
name: test-merge-http-client-a
description: httpx async client usage patterns
created_by: agent
created_at: 2026-05-01T08:00:00Z
updated_at: 2026-05-01T08:00:00Z
---

## Overview
Using httpx for async HTTP requests.

## Basic usage
```python
async with httpx.AsyncClient() as client:
    resp = await client.get("https://api.example.com/data")
    resp.raise_for_status()
    return resp.json()
```
