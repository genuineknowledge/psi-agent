# Fast Supervision and Long-Term Knowledge Quality Design

## Goal

Adopt option B: keep current-turn supervision useful while bounding first-token latency to a short budget, and move expensive map/heatmap quality work off the critical path.

## Runtime Contract

For a learning turn:

```text
request
  -> load exact-match Advice cache and lightweight profile context
  -> start/reuse Supervisor Session
  -> wait at most 10 seconds for a compact live Advice
  -> live Advice wins when valid
  -> otherwise use only a fresh, same-user/same-domain/same-intent cache
  -> otherwise use global profile fallback with no forced breakout
  -> start background enrichment for map and heatmap quality
  -> build prompt and start main Agent
```

The 10-second default is configurable. A timeout must never use another user's cache, a stale domain cache, or a cache whose intent conflicts with the current question.

## Fast Advice

The fast Advice schema contains only fields needed by the current response:

- domain and topic;
- learning classification;
- breakout needed/type/score/reason/directions;
- response depth/scope/goal/terminology;
- profile shift summary;
- source and created time.

Map proposals, long evidence, branch additions, and heatmap aggregation are excluded from the synchronous response. The existing full SupervisorAdvice remains the durable enrichment format.

## Cache Eligibility

An Advice cache is eligible only when all are true:

1. `user_id_hash` matches exactly;
2. `profile_id` matches exactly;
3. normalized domain matches;
4. normalized topic or intent family matches;
5. age is within the configured TTL;
6. source is `live` or `repaired`, never `unavailable`;
7. the current question has not explicitly changed depth, scope, or breakout preference;
8. a cached `none` breakout is not reused after a clear request for depth or breadth.

Cache reuse is observable through `diagnostics.source = "cache"` and a reason code. The raw user question is never persisted in the cache.

## Background Enrichment

After the current response has started, a bounded background task may:

- create or expand the shared map;
- normalize node aliases;
- merge duplicate branches;
- update user heat counts;
- apply time decay;
- record breakout acceptance/rejection evidence when explicitly observable;
- persist a full Advice snapshot.

Background work is best-effort, cancellable, atomic, and isolated from the main answer. A failed enrichment task logs a warning and leaves the last valid state intact.

## Long-Term Map Quality

Maps gain metadata:

- schema version;
- map revision;
- node confidence;
- source count;
- first seen and last seen timestamps;
- aliases;
- parent/child relation;
- stale flag.

Merges use normalized lowercase identifiers and conservative alias matching. A merge must not delete an existing node without retaining an alias and revision history.

## Long-Term Heatmap Quality

Heatmaps retain complete historical evidence and do not apply time decay or automatic deletion. They gain:

- immutable event records for every observed question and advice decision;
- current branch state separate from the historical event log;
- last-seen timestamp without reducing the evidentiary weight of older events;
- repeated-surface count;
- explored-node count;
- accepted and rejected breakout counts when known;
- confidence and evidence age as descriptive metadata, not decay factors;
- cognitive and intent transition history without truncation.

When the user explicitly changes direction—for example, from deep derivation to a simple explanation, or from strategic planning back to basic understanding—the system may roll back the *active response strategy* for the affected topic branch. This rollback changes the next-answer starting point and branch-local active depth; it must not delete, lower, or rewrite historical heatmap events. Later renewed depth can build forward from the preserved history.

## Failure and Observability

Record non-sensitive metrics:

- fast supervision duration;
- cache hit/miss and reason;
- live Advice success/repair/timeout;
- background enrichment success/failure;
- map revision before/after;
- heatmap revision before/after;
- main response start delay.

Never log raw user IDs, raw questions, API keys, main reasoning, or tool results.

## Acceptance Criteria

- A cold learning turn starts the main Agent after the short budget rather than waiting 60 seconds.
- A fresh exact-match cache can influence the current turn without a live supervisor call.
- A mismatched or stale cache is rejected and cannot leak across users or domains.
- Background map/heatmap updates do not delay the first main response chunk.
- Map revisions preserve aliases and do not duplicate normalized nodes.
- Heatmaps preserve complete history, and explicit depth/goal changes can roll back only the affected branch's active response strategy without deleting historical events.
- Existing supervisor tests remain green; new tests cover timeout, cache eligibility, fallback order, background failure, map merge, and heat decay.
- Real multi-turn tests report P50/P95 supervision and first-token latency separately.

## Explicit Trade-off

The first cold turn may answer with profile/global fallback when live Advice exceeds 10 seconds. This is intentional: bounded responsiveness is preferred over guaranteeing a live breakout on every first question. The next turn benefits from the completed background Advice and map state when enrichment succeeds.
