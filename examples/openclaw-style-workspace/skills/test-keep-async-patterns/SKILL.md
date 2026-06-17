---
name: test-keep-async-patterns
description: Comprehensive async/await patterns for Python 3.11+ with anyio
created_by: agent
created_at: 2026-06-10T09:00:00Z
updated_at: 2026-06-15T14:00:00Z
---

## Overview
Production-grade async patterns using anyio for cross-backend compatibility.

## Task Groups
Use `anyio.create_task_group()` for structured concurrency — all tasks cancel together on error.

```python
async with anyio.create_task_group() as tg:
    tg.start_soon(fetch_data, url1)
    tg.start_soon(fetch_data, url2)
```

## Cancellation
Always catch `anyio.get_cancelled_exc_class()` — not `asyncio.CancelledError` — for backend portability.

## Timeouts
```python
with anyio.move_on_after(30) as cancel_scope:
    result = await slow_operation()
if cancel_scope.cancelled_caught:
    result = fallback_value
```

## When to use
- Any I/O-bound parallel work
- HTTP fan-out requests
- Multi-source data aggregation

<!-- merged from python-async-basics -->

---
name: python-async-basics
description: Python asyncio fundamentals
category: coding
created_by: agent
created_at: 2026-03-01T00:00:00Z
---

# Python Asyncio Fundamentals

## Core Rules
- Use `async def` / `await` for I/O-bound concurrency.
- Always `await` coroutines — unawaited coroutines are a bug.
- Never call blocking I/O inside an async function. Use `anyio.to_thread.run_sync()` or `asyncio.to_thread()` instead.

## Event Loop
- The event loop runs one coroutine at a time — cooperative multitasking.
- `await` is a yield point: it suspends the current coroutine and lets others run.
- CPU-heavy work blocks the loop. Offload to a thread or process.

## Common Patterns
```python
# Concurrent tasks
import asyncio
results = await asyncio.gather(task1(), task2(), task3())

# Timeout
await asyncio.wait_for(slow_operation(), timeout=5.0)

# Producer-consumer
queue = asyncio.Queue()
```

## Tools
- **anyio** — Trio-style structured concurrency, works with asyncio backend.
- **uvloop** — Drop-in faster event loop (not always needed).

## Traps
- `time.sleep()` blocks the event loop → use `await asyncio.sleep()`.
- `open()` blocks → use `aiofiles` or `anyio.open_file()`.
- Forget `await` → coroutine silently does nothing.
- Shared mutable state across tasks → use `asyncio.Lock`.