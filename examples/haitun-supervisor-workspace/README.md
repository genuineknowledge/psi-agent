# Haitun Background Supervisor Agent

This Workspace implements Haitun's independent, background learning and breakout supervisor. It is not a second user-facing assistant. Its job is to analyze the user's current learning or decision path before the main Agent answers, maintain a long-term knowledge map and private user heatmap, and return a validated response strategy.

## Why a supervisor exists

A single Agent must simultaneously understand the question, remember the user, decide the appropriate depth, find missing dimensions, decide whether to broaden or deepen, and write the final answer. Those goals compete for attention. The supervisor separates the meta-level judgment from answer generation.

The supervisor is especially useful when a user:

- repeatedly asks surface questions but wants a complete field overview;
- asks for depth without knowing the relevant mechanism or prerequisite;
- changes from learning to selection, cost, risk, execution, or strategy;
- needs a cross-domain connection;
- explicitly changes the desired answer depth;
- returns to a knowledge area across Sessions.

## Information flow

```text
user question
  -> main Session identifies user/profile/topic
  -> first eligible turn: main Agent answers immediately
  -> after first answer: supervisor warmup starts from user question only
  -> later eligible turn: supervisor is required
       -> matching warmup/cache Advice, or
       -> live Advice within 30 seconds, or
       -> explicit unavailable fallback
  -> Advice is validated and rendered into the main system prompt
  -> main Agent answers the immediate question and integrates useful breakout directions
  -> durable map, heatmap, participation state, and metrics are updated
```

The main Agent never receives arbitrary child instructions. The validated protocol exposes controlled strategy fields such as answer depth, scope, goal mode, terminology behavior, and breakout integration.

## First-turn warmup

The first eligible learning turn does not wait for a new supervisor process or LLM call. The main Agent answers from the stage profile and global rules. Once the visible answer is complete, `system_after_turn` primes the supervisor while the user reads the answer.

Warmup receives:

- the first user question;
- hashed identity;
- `profile_id`;
- normalized stage profile;
- existing map/heatmap summaries;
- previous validated supervision when applicable.

Warmup never receives the assistant answer, reasoning, drafts, tool calls, or tool results.

## Second and later turns

Every later eligible learning turn executes the supervisor path. The source is one of:

- `live`: a current supervisor result;
- `repaired`: a current result that required safe protocol repair;
- `cache`: fresh same-user/profile/topic Advice created during warmup or an earlier turn;
- `unavailable`: process, model, transport, timeout, or validation failure.

“Required” means the system must execute and record this path. It does not mean fabricating Advice when an external model is unavailable.

## Breakout modes

- `broaden`: add missing parts of the current field or framework;
- `deepen`: move from definition to mechanism, evidence, derivation, or boundary conditions;
- `reframe`: change the question's frame when the current framing hides the real decision;
- `cross_domain`: connect another discipline that materially changes understanding;
- `operationalize`: convert knowledge into a decision, pilot, policy, workflow, or measurable action.

The supervisor must first respect the current question. A breakout is an optional integrated direction, not permission to ignore the user's request or force a topic change.

## Identity model

- `user_id`: long-term person identity and private heatmap owner;
- `profile_id`: current role, scene, or learning phase;
- domain: shared objective knowledge area;
- topic: current branch inside the domain.

Raw user IDs are SHA-256 hashed before filenames, process identities, or routine logs are created.

## Shared knowledge maps

Maps live under:

```text
examples/haitun-workspace/wiki/supervisor/maps/<domain>.yaml
```

Maps are shared across users and include:

- schema version and map revision;
- nodes and edges;
- aliases for Chinese/English or alternate names;
- confidence and source count;
- first/last seen timestamps;
- conservative duplicate-node and duplicate-edge merging.

Later users should reuse an existing field map and add only missing branches rather than regenerate the panorama.

## Private user heatmaps

Heatmaps live under:

```text
examples/haitun-workspace/wiki/supervisor/users/<user-hash>/domains/<domain>.yaml
```

They retain complete history. There is no time decay and no automatic truncation. Historical evidence records what the user explored and requested; `active_branches` controls only the current answer strategy.

Example:

```text
deep derivation requested
  -> active_depth=deep
simple explanation requested later
  -> transition=rollback
  -> active_depth=simple
  -> earlier deep event remains intact
renewed derivation request
  -> transition=advance
```

A rollback affects only the current topic branch and never another domain, user, or historical event.

## Advice cache

Cache reuse is intentionally strict. It requires the same hashed user, `profile_id`, topic, valid source, and freshness window. Explicit simplification, depth change, breakout suppression, or topic change rejects the cache. Cache hits are marked in diagnostics and metrics.

## Failure behavior

The synchronous supervisor budget is 30 seconds. Real legal runs placed successful Supervisor responses near 19-21 seconds, so 30 seconds provides headroom for normal variance. Ordinary failures return `unavailable` or an eligible cache; they do not reject the user's turn. Cancellation propagates. Persistence uses atomic replacement and keyed locks.

External failures that cannot be solved by this Workspace include provider downtime, invalid credentials, DNS/network failure, provider rate limits, and a model that never returns valid JSON. These cases are recorded rather than hidden.

## Metrics

Per-user append-only metrics are stored at:

```text
wiki/supervisor/users/<user-hash>/metrics.jsonl
```

They include turn index, first-turn status, whether supervision was required, source, warmup status, and elapsed milliseconds. They must not include raw user identity, question text, assistant text, reasoning, or secrets.

## Local testing

Use a main Session ID that does not start with `supervisor-`:

```powershell
Set-Location "C:\Users\ad100104\Desktop\网页\psi-agent\.worktrees\background-supervisor-agent"
uv sync
uv run psi-agent run config.yml
```

Useful log markers:

```text
Supervisor first-turn warmup requested
Supervisor first-turn warmup finished
Before-turn advice present: True
Supervisor cache hit: source=cache
```

Inspect state:

```powershell
Get-ChildItem examples\haitun-workspace\wiki\supervisor -Recurse
```

## Experiments

The maintained scenario runner covers a CEO deciding whether to adopt CI/CD and a technology-company legal worker learning Agent governance:

```powershell
uv run --no-cache python examples/haitun-workspace/demo_supervisor_scenarios.py --output artifacts/supervisor-scenarios
```

Real LLM evidence and deterministic fallback must always be labeled separately.

## Current limitations

- The first new learning answer is not influenced by new live supervision.
- Warmup still depends on the configured model and network.
- The main system prompt remains large and may dominate first-token latency.
- Background map enrichment is not yet a fully independent process lifecycle.
- Semantic alias merging is conservative and can miss paraphrases.
- The supervisor cannot verify compliance with its Advice because it never receives the main answer.
- Cross-process map conflict resolution is not yet a full transactional revision protocol.

## Roadmap

1. Full Session/AI mock E2E with warmup and second-turn injection.
2. Real P50/P95 latency and source-rate experiments.
3. FastAdvice versus FullAdvice separation.
4. Managed background enrichment task lifecycle.
5. Semantic topic and alias normalization.
6. Blind single-Agent versus supervised-Agent evaluation.
7. Optional post-answer auditor that sees only final visible text, never reasoning.
