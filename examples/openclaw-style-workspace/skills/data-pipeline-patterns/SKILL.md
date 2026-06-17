---
name: data-pipeline-patterns
description: Patterns for building data processing pipelines with Python
created_by: agent
created_at: 2026-06-01T10:00:00Z
updated_at: 2026-06-16T17:51:51Z
---

## Overview
Common patterns for building robust data processing pipelines in Python.

## Pattern 1: Batch Processing
Use chunked reads to avoid memory exhaustion with large datasets.
```python
def process_in_batches(iterable, batch_size=1000):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
```

## Pattern 2: Stream Processing
Process records one at a time using generators — minimal memory footprint.
```python
def transform(records):
    for record in records:
        yield process(record)
```

## Pattern 3: Incremental Loading
Track high-water marks to only process new/changed data.
```python
last_processed = load_checkpoint("last_id")
new_records = query("SELECT * FROM events WHERE id > ?", last_processed)
for batch in process_in_batches(new_records):
    handle_batch(batch)
    save_checkpoint("last_id", batch[-1].id)
```

## Pattern 4: Checkpointing
Save progress periodically so pipelines can resume after failure.
```python
import json

def save_checkpoint(name, value):
    ckpt = load_all_checkpoints()
    ckpt[name] = value
    with open("checkpoint.json", "w") as f:
        json.dump(ckpt, f)

def load_checkpoint(name):
    ckpt = load_all_checkpoints()
    return ckpt.get(name)
```

## Pattern 5: Dead Letter Queue
Route failed records to a DLQ for later inspection instead of crashing the pipeline.
```python
async def process_with_dlq(record):
    try:
        await process(record)
    except Exception as e:
        await dlq.put({"record": record, "error": str(e), "timestamp": time.time()})
```

## Pattern 6: Fan-out / Fan-in
Parallelize independent work, then aggregate results.
```python
import anyio

async def fan_out(items, worker, concurrency=10):
    semaphore = anyio.Semaphore(concurrency)
    async def bounded(item):
        async with semaphore:
            return await worker(item)
    async with anyio.create_task_group() as tg:
        results = []
        for item in items:
            tg.start_soon(lambda i=item: results.append(await bounded(i)))
    return results
```