---
name: async-concurrency-python
description: "Writing correct Python asyncio / concurrency code, especially around cancellation, cleanup, and concurrency limits: bounded concurrency with semaphores, propagating cancellation so every started task's cleanup (finally / except CancelledError) runs, and the asyncio.gather cancellation gotchas. Use for tasks implementing async task runners, schedulers, KeyboardInterrupt-safe cleanup, or 'run N tasks with max M concurrent' functions."
---
### Cancellation must let every STARTED task's cleanup run
The single most error-prone requirement: when the runner is cancelled (e.g. a
KeyboardInterrupt / SIGINT), every task that already started must still run its
cleanup code (its `finally` block or `except asyncio.CancelledError`).
*   **Wrap each task body so cleanup lives in `finally`/`except CancelledError`** — that is what runs when the task is cancelled. Cleanup outside a finally is skipped on cancellation.
*   **On cancellation, cancel all created child tasks and then AWAIT them to completion** (`await asyncio.gather(*children, return_exceptions=True)`) BEFORE re-raising. Cancelling a task only *requests* cancellation; its cleanup runs asynchronously and only completes if you await it. Re-raising immediately (or returning) without awaiting kills the event loop before cleanup finishes → cleanup silently doesn't run.
*   **Distinguish `asyncio.CancelledError` from other exceptions.** When the runner coroutine is itself cancelled, `asyncio.gather` has ALREADY propagated cancellation to its children — do NOT cancel them a second time, because a redundant `cancel()` can interrupt a child's *currently-running* cleanup. In the `CancelledError` branch just `await gather(*children, return_exceptions=True)` to let cleanups finish, then re-raise. Use the explicit-cancel path only in the "a task raised an error" branch (cancel the still-running ones, await, re-raise).

### The asyncio.gather queue gotcha
*   **`asyncio.gather` does NOT cleanly cancel tasks when there are still un-started tasks "in the queue".** With bounded concurrency, tasks above the limit are blocked waiting on the semaphore; on cancellation, naive `gather` handling can leave the started tasks' cleanup un-run (the classic "started N but cleaned up 0" bug). Handle it by explicitly cancelling + awaiting ALL created futures (started and queued) in your except branch, with the CancelledError-vs-other distinction above.
*   Be aware that a task blocked on `async with semaphore:` (waiting its turn) has NOT entered the user body yet, so it has no cleanup to run — but a task that acquired the semaphore and started IS in its body and MUST get its `finally` run on cancel.

### Bounded concurrency pattern
*   **Limit concurrency with `asyncio.Semaphore(max_concurrent)`**, wrapping `async with semaphore: await task()` inside a per-task coroutine; create all of them with `asyncio.ensure_future`/`create_task` up front, then `await asyncio.gather(*futures)`.
*   **Validate the limit** (`max_concurrent >= 1`) and handle the empty-task-list case early.
*   Match the EXACT signature, file path, and import name the task/grader requires, and don't add helper entry points.

### Verify against the grader's actual cancellation test
*   **The grader typically drives a real subprocess and sends SIGINT mid-run**, then asserts counts like "started == K" and "cleaned up == K". Reproduce that locally: spawn your runner with more tasks than the limit, send SIGINT after a short delay, and assert every started task printed its cleanup. The above-limit / "tasks still queued" case is the one that exposes the gather gotcha — test it specifically, not just the simple equal-or-below-limit cases.
