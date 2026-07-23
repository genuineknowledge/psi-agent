# Concurrent Fan-out/Fan-in Router Design

## Goal

Replace the current Router-internal serial branch loop with a Session-driven
round protocol. For every round, Router concurrently calls every configured
upstream socket with the same request context, aggregates model output and
deduplicated tool calls, and returns one OpenAI-compatible response to Session.
Session remains the sole owner of conversation history and workspace tool
execution. After tools execute, Session sends the next round back to Router.

## Data flow

```text
Session -> Router
           |-- concurrent upstream A
           |-- concurrent upstream B
           `-- concurrent upstream C
                 |
           deterministic aggregation
                 |
Router -> Session (text + tool_calls)
Session executes tools and updates history
Session -> Router (next round)
```

Each upstream receives a copy of `messages`, the complete `tools` list, and
ordinary passthrough parameters. Internal `routing` metadata is not forwarded
to ordinary providers. The stable `routing.session_id` remains available at
the Router boundary for correlation.

## Aggregation

- Results are ordered by `upstream` configuration order, regardless of
  completion order.
- Non-empty `content` values are joined with a newline.
- Non-empty `reasoning` values are joined with a newline.
- Tool calls are scanned in deterministic upstream/result order. Calls are
  deduplicated by the original `tool_call.id`; the first complete definition
  wins. Different IDs are retained even when name and arguments match.
- If any tool call remains, response `finish_reason` is `tool_calls`, and the
  combined content/reasoning is emitted with those calls.
- Without tool calls, the response is a normal final answer (`stop`).
- Empty choices/heartbeat chunks are ignored. A malformed or incomplete tool
  call is not emitted as executable output.

## Failure and fallback

Upstream calls run in an AnyIO task group and are cancellation-safe. A failed
upstream is recorded while successful upstream results continue to aggregate.
If all upstream calls fail, Router invokes `default_socket` once with the
original request (minus internal routing/model fields). Fallback responses are
proxied without a second fallback attempt. Startup, shutdown, cancellation,
and stream cleanup remain shielded as required by the existing server rules.

## Compatibility and scope

The public Router configuration remains `session_socket`, `router_socket`,
`default_socket`, and `upstream: list[tuple[str, str]]`. The router socket is
no longer used for planner/aggregator calls in this mode; it may remain in the
configuration for backwards compatibility but does not participate in each
fan-out round. No model names are added to Router configuration.

## Verification

Add unit tests for concurrent dispatch, deterministic aggregation, ID-based
deduplication, mixed text/tool responses, partial failures, and all-failed
fallback. Add an integration test covering two Session rounds: first round
returns deduplicated tool calls, Session executes them, and the second round
returns the final aggregate answer.
