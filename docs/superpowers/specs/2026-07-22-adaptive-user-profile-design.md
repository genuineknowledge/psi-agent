# Adaptive User Profile and Learning Coach Design

## Objective

Build a framework-backed adaptive learning loop for the Haitun workspace that:

- isolates profiles by stable user identity, falling back to session identity;
- dynamically discovers and merges knowledge-point profiles without a domain allowlist;
- maintains long-term, short-term, and per-user global dimensions;
- injects exactly one current-topic teaching policy before every response;
- validates required coaching structures and performs at most one correction round;
- updates the final topic profile after every successfully committed turn;
- uses the LLM Wiki for grounded breakout suggestions when relevant pages exist;
- never stores raw user or assistant turn text in profile files.

## Identity and Storage

Profile identity uses this precedence:

1. explicit `profile_id` from trusted channel metadata;
2. stable channel `user_id` namespaced by channel type;
3. session ID fallback.

Raw external identifiers are not used as paths. The runtime creates a profile key from the
source namespace plus a stable digest. Profiles are stored under:

```text
wiki/profiles/<source>-<digest>/_profile.md
```

The runtime context passed to workspace lifecycle hooks carries the selected profile key,
identity source, and session ID. Missing channel identity therefore isolates sessions but does
not promise cross-session continuity.

The legacy `wiki/_profile.md` is migrated once into the first fallback profile that needs it.
Migration is atomic and retains a backup until the new file is successfully readable.

Each profile owns an async lock. Read-update-save sequences are serialized per profile and use
temporary-file plus `os.replace` persistence. Different users may update concurrently.

## Profile Schema

Each user profile contains one global profile and independent topic profiles:

```yaml
version: 3
identity:
  key: session-a42f...
  source: session
global:
  turns: 12
  dimensions:
    depth: 0.54
    goal: 0.61
    familiarity: 0.48
topics:
  overfitting:
    label: 过拟合
    aliases: [模型过拟合, overfitting]
    keywords: [训练集, 泛化, 验证集]
    turns: 5
    last_seen: 2026-07-22T08:00:00+00:00
    dimensions:
      long: {depth: 0.46, goal: 0.37, familiarity: 0.42}
      short: {depth: 0.78, goal: 0.41, familiarity: 0.51}
    signals:
      depth_high: 2
      depth_low: 1
    recent_directions:
      depth: [-1, 1, 1]
      goal: [0, 0, 0]
      familiarity: [-1, 0, 1]
```

Profile persistence contains aggregate values only. Raw user messages, assistant messages,
quotations, summaries, and tool results are forbidden in this file.

## Topic Resolution

Topic resolution has three stages.

### Meta-instruction inheritance

Messages primarily controlling answer style inherit the last active topic. Examples include
requests to simplify, expand, derive, continue, provide formulas, provide examples, change
perspective, compare risks, or explain implementation. These expressions update dimensions but
do not become topic labels.

### Existing-topic matching

The resolver compares normalized candidates against labels, aliases, keywords, substring
matches, and the recently active topic. It returns the best existing topic only above an
explicit confidence threshold. Matching is deterministic and testable.

### New-topic creation

When no existing topic matches, the resolver derives a label from quoted terms, English
technical tokens, question objects, and normalized noun-like phrases. It does not use a domain
allowlist. Generic conversational prefixes and style signals are removed. The slug is stable and
collision-safe.

Topic merging uses label/alias equality plus weighted keyword similarity. It never merges solely
because both topics contain generic words. Merge calculations preserve weighted long and short
dimensions, signal counts, recent direction windows, turn counts, and the newest timestamp.

## Dimension Updates

The three dimensions are depth preference, decision orientation, and domain familiarity.

Signals use signed directions:

```text
high = +1
neutral = 0
low = -1
```

Long-term updates use EMA alpha `0.35`; short-term updates use EMA alpha `0.80`. Specific negative
phrases take precedence over contained positive tokens, so `不用深入` is low depth rather than
both low and high depth.

The recent direction window contains the last three observations per dimension. Direction
changes increase short-term weight. Stable preferences reduce it. Effective dimensions use:

```text
effective = long * (1 - short_weight) + short * short_weight
```

Short weight is clamped to `[0.25, 0.85]`. This lets one explicit request quickly change the next
answer while repeated stable behavior gradually moves the long-term profile.

The per-user global profile is a turn-weighted aggregate of non-empty topic long-term profiles.
New topics initialize from `70% global + 30% neutral` and initialize short-term values to the same
warm-start values.

## Runtime Lifecycle

The Session runtime passes current user message and immutable runtime identity context into the
workspace prompt lifecycle. Exactly one workspace integration point builds adaptive context:

```text
user request
  -> resolve profile identity
  -> system_prompt_builder(user_message, runtime_context)
  -> load profile and select topic
  -> calculate effective dimensions and current turn number
  -> query Wiki suggestions when breakout is required
  -> inject one teaching policy and one turn policy below the cache boundary
  -> generate response
  -> validate required response structures
  -> optionally perform one correction generation
  -> commit final response
  -> system_after_turn(user_message, assistant_message, runtime_context)
  -> update and atomically persist selected user/topic profile
```

`System.build_system_prompt()` builds base prompt content only. It must not load, create, mutate,
or inject profiles. This prevents duplicate profile sections and zero-turn `general-N` topics.

The rebuild checker returns true for interactive turns so the latest profile and current topic
are used. Schedule turns may opt out of profile mutation and coaching policy through runtime turn
kind metadata.

## Teaching Policy

The effective current-topic dimensions produce direct response instructions:

- low depth: lead with one-sentence conclusion and analogy, minimize detail;
- medium depth: give a framework and optional deeper layer;
- high depth: include mechanism, derivation, implementation, and boundaries;
- high goal: emphasize risks, costs, comparisons, applicability, and execution;
- low goal: emphasize conceptual understanding and curiosity;
- low familiarity: define terminology and avoid assumed prerequisites;
- high familiarity: skip basic definitions and discuss boundaries and advanced implications.

The current turn number is `completed_topic_turns + 1`.

## Supervision and Validation

The turn policy contains machine-readable requirements:

```python
TurnPolicy(
    topic_key="overfitting",
    require_certainty=True,
    require_counterexample=True,
    require_socratic=current_turn % 3 == 0,
    require_breakout=current_turn >= 5 and familiarity > 0.5,
    wiki_suggestions=(...),
)
```

The system prompt renders the same requirements as natural-language instructions and a private
self-check list. Structural validation checks:

- at least one certainty marker and no wholly unmarked factual section;
- `🧪 反例：` when a concept is explained;
- an interrogative question when Socratic guidance is required;
- `💡 破圈思考：` when breakout guidance is required.

The first validation failure adds precise feedback and requests one complete corrected answer.
There is no infinite retry. If the correction still fails, the runtime keeps the usable answer,
adds only safe structural fallback text where possible, logs the remaining violations, and
commits the final version.

Certainty validation is necessarily approximate: the system cannot prove that every natural
language claim is true or that `[已确认]` has external evidence. The validator enforces structure,
while factual grounding remains a model/tool responsibility.

## LLM Wiki Integration

Breakout lookup uses a fallback chain:

1. search the Wiki using topic label and aliases;
2. choose the best matching existing page slug;
3. query co-citation relations using `wiki_related`;
4. fall back to direct outgoing/back links;
5. fall back to tag-similar pages;
6. inject no recommendation when the Wiki has no grounded candidate.

Wiki lookup failures are warnings and never block a response. Recommendations always identify an
existing Wiki page; the system does not fabricate titles. `wiki_write` remains an explicit Agent
tool used only when the user requests saving or when the existing authorization policy permits
knowledge persistence.

## Error and Cancellation Behavior

- Profile prompt loading failure logs a warning and allows a normal unprofiled response.
- Wiki lookup failure removes Wiki suggestions but preserves breakout guidance.
- Profile save failure occurs after response commit, logs a warning, and does not roll back the
  delivered response.
- Cancellation propagates; cleanup and atomic file replacement remain cancellation-safe.
- Validation correction is capped at one additional model round.
- JSON/YAML data is treated as untrusted and type-checked before access.

## Migration

Schema migration supports the existing global version-2 topics and the older raw-history format.
Raw history is replayed only in memory to derive aggregate signals and is omitted from the new
file. A successful migration verifies the new file before retaining a backup of the old source.
Migration never silently replaces a non-empty legacy profile with an empty profile.

## Verification

Automated tests cover:

- Session import and resolution of all Git conflict markers;
- current message and runtime identity propagation to lifecycle hooks;
- exactly one profile section and one supervision section per prompt;
- no empty-topic creation during base prompt construction;
- meta-instruction topic inheritance and genuine new-topic creation;
- deterministic topic merging and non-merging of unrelated topics;
- long/short EMA response, signed volatility, and global warm start;
- independent profile files for two user IDs and session fallback;
- third-turn Socratic and fifth-turn breakout policies;
- Wiki search, related lookup, link/tag fallbacks, and empty-Wiki behavior;
- response validation, one correction, and capped fallback;
- after-turn persistence and per-profile locking;
- absence of raw conversation text in persisted profiles;
- legacy schema migration without data loss;
- lint, formatting, type checks, focused tests, and the full non-schedule test suite.

## Explicit Limitations

- CLI/REPL users without a stable channel identity are isolated by session and do not receive
  automatic cross-session identity matching.
- Natural-language certainty and factual truth cannot be perfectly verified mechanically.
- Empty or weakly linked Wikis cannot provide grounded breakout recommendations.
- A failed validation correction may still produce a partially non-compliant answer; the runtime
  logs this rather than retrying indefinitely.
- Correction rounds add model latency and token cost only when the first answer violates a hard
  structural requirement.
