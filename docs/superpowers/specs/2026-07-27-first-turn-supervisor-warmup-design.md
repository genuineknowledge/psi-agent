# First-Turn Supervisor Warmup and Evaluation Design

## Objective

Reduce perceived first-turn latency while ensuring that every second and later learning turn passes through the supervisor path. Use the user's first-answer reading time to start the per-user supervisor, generate initial Advice, and initialize long-term knowledge state.

## Product Decisions

1. The synchronous supervisor budget is 20 seconds rather than 10 or 60 seconds.
2. A user's first eligible learning turn does not wait for live supervision.
3. After the first visible answer completes, `system_after_turn` warms the supervisor using only the first user question and aggregate profile state. It never sends the main answer, reasoning, tool calls, or tool results.
4. The second and every later eligible turn must execute the supervision path.
5. “Supervisor must participate” means the path must attempt `live`, then eligible `cache`, and explicitly report `unavailable` when external dependencies fail. It never fabricates Advice.
6. Map and heatmap history remain durable and non-decaying. Explicit simplification changes only the affected active branch.

## Lifecycle

### First eligible turn

```text
user question
  -> detect first eligible learning turn
  -> mark warmup required
  -> skip synchronous supervisor wait
  -> profile-driven main answer streams immediately
  -> after successful answer, run supervisor warmup
  -> persist initial Advice, map revision, and heatmap event
```

The user can read streamed content while after-turn warmup finishes. Warmup may delay the final request completion marker, but not already emitted answer content.

### Second and later eligible turns

```text
user question
  -> require supervisor path
  -> use eligible first-turn warmup cache immediately when it matches
  -> otherwise request live Advice for at most 20 seconds
  -> live Advice wins
  -> eligible cache is the timeout fallback
  -> otherwise return unavailable and use profile fallback
  -> inject validated Advice into the current main prompt
```

## State Model

Per-user supervisor state records:

- whether an eligible first turn has been seen;
- whether warmup was requested, completed, failed, or timed out;
- latest eligible Advice metadata;
- last supervised turn index;
- cumulative live/cache/repaired/unavailable counts;
- duration metrics without raw identity or question content.

State uses the existing hashed user directory. A process restart reconstructs participation state from durable heatmap history and latest Advice rather than depending only on memory.

## Observability

Emit structured, non-sensitive metrics for each eligible turn:

- hashed user prefix;
- turn index;
- first-turn flag;
- supervisor required flag;
- Advice source;
- supervisor total duration;
- startup and chat durations when available;
- cache hit and reason;
- Advice repair flag;
- map revision before and after;
- heatmap event count before and after;
- prompt Advice injection flag;
- warmup status and error class.

Never log raw user IDs, raw questions, API keys, main answers, reasoning, or tool results.

## Experiment

Run two stable identities:

### CEO

- `user_id=experiment-ceo`
- `profile_id=executive-decision`
- CI/CD adoption, cost, company baseline, staged decision, pilot metrics, simplification, renewed depth, and organizational-risk breakout.

### Technology-company legal worker

- `user_id=experiment-legal`
- `profile_id=legal-learning`
- Agent basics, comparison, implicated legal fields, governance controls, policy drafting, procurement-Agent stress test, simplification, and renewed detail.

Use 8–12 turns per identity. Real LLM execution is primary. Preserve real upstream failures and use deterministic fallback only when labeled explicitly.

## Deliverables

1. Experiment evaluation report with complete dialogue, per-turn Advice, breakout analysis, maps, heatmaps, and profile effects.
2. Stability and engineering report with P50/P95 latency, source distribution, timeout/restart/repair rates, isolation evidence, failure impacts, and maturity gaps.
3. `examples/haitun-supervisor-workspace/README.md` documenting purpose, architecture, lifecycle, isolation, Advice flow, maps, heatmaps, caching, warmup, errors, local operation, experiments, and roadmap.

## Acceptance Criteria

- The default synchronous budget is exactly 20 seconds.
- The first eligible learning turn streams without waiting for live Advice.
- First-turn warmup receives no main answer or reasoning.
- The second eligible turn always executes the supervisor path.
- A matching warmup cache can affect the second turn.
- An unavailable supervisor is reported honestly and never blocks indefinitely.
- Existing Session/profile/supervisor tests remain green.
- New tests cover process restart reconstruction, warmup success/failure, second-turn participation, cache fallback, isolation, and structured metrics.
- CEO and legal-worker experiments produce separately labeled real and fallback evidence.

## Known Trade-off

The first answer is not influenced by new live supervision when no prior state exists. This intentionally trades first-turn adaptive quality for responsiveness. The design recovers value by warming during first-answer reading time and requiring supervision from the second eligible turn onward.
