# Background Supervisor Agent Design

Date: 2026-07-24

Status: Approved

Scope: `psi-agent` Session lifecycle and `examples/haitun-workspace`

## 1. Objective

Add a mandatory, independent, background supervisor Agent for learning-oriented user questions. The supervisor observes only user questions and explicitly whitelisted aggregate context. It must never receive the main Agent's answer, draft, reasoning, tool calls, or tool results.

The supervisor's highest-priority responsibility is detecting breakout opportunities: cases where the user needs a broader framework, a deeper mechanism, a different perspective, a cross-domain connection, or a transition from conceptual learning to action. It also detects latent needs and stage-profile shifts, then returns structured advice before the main Agent generates the current answer.

The first implementation must produce a locally testable loop:

```text
user question
  -> before-turn supervisor lifecycle
  -> per-user supervisor Session
  -> shared domain map + private user heatmap
  -> validated SupervisorAdvice
  -> current-turn dynamic system prompt
  -> main Agent answer
  -> existing after-turn stage-profile update
```

## 2. Confirmed Product Decisions

1. Breakout detection has the highest supervision priority.
2. The main Agent waits for live supervision before generating its final answer.
3. Supervision has a bounded timeout; failure or timeout never blocks the main answer indefinitely.
4. Each `user_id` owns one long-lived logical supervisor identity, reusable across Sessions.
5. A `profile_id` represents a stage or scenario profile and remains separate from the `user_id` panorama.
6. Objective domain maps are shared by all users.
7. User traversal, heat, cognitive level, and intent history are isolated by `user_id`.
8. A new domain creates its panorama once; later questions reuse it and update only relevant branches and user heat.
9. Existing maps are locally expanded only when the current question cannot be represented, the map lacks an important branch, or the map is stale or low-confidence.
10. The supervisor does not inspect the main Agent's output. It predicts what the current answer must account for, but cannot claim that it verified whether the main Agent complied.

## 3. Identity Model

The design uses three distinct identities:

| Identity | Meaning | Lifetime | Main use |
|---|---|---|---|
| `user_id` | Long-term person identity | Cross-Session and cross-profile | Supervisor identity, panorama, user heatmaps |
| `profile_id` | Current stage or scenario | User-selected or request-derived | Existing three-dimensional adaptive answer profile |
| domain/topic | Current knowledge object | Per knowledge area | Shared map and per-user traversal state |

The logical supervisor Session ID is derived from a hash of `user_id`:

```text
supervisor-<user-id-hash>
```

Raw user identifiers are not used in filenames, socket paths, process IDs, or routine logs.

## 4. Runtime Data Flow

```text
Channel / Gateway
  -> Session parses user message and reserved identity fields
  -> refresh tools and schedules
  -> SystemPrompt.run_before_turn(hook_message)
       -> Haitun system_before_turn()
       -> learning-intent classification
       -> SupervisorManager.ensure_supervisor(user_id)
       -> send a whitelist-built supervision payload
       -> supervisor loads or creates the domain map
       -> supervisor loads and updates the user's heatmap
       -> supervisor returns SupervisorAdvice JSON
       -> validate, repair, bound, and cache the advice
  -> attach validated advice only to the ephemeral hook message
  -> system_prompt_builder() combines stage profile and advice
  -> main AI generates the current response
  -> successful assistant response is committed
  -> system_after_turn() updates the existing stage profile
```

The main Agent must not begin final SSE content generation before live supervision completes or reaches its timeout. Work that does not depend on the advice may run concurrently, including loading profiles, maps, tools, and schedules.

## 5. Session Lifecycle Extension

### 5.1 New hook

The Session system module loader supports an optional async function:

```python
async def system_before_turn(
    user_message: dict[str, Any],
) -> dict[str, Any]:
    ...
```

`SystemPrompt` gains:

- a default before-turn hook returning `{}`;
- dynamic extraction of `system_before_turn`;
- `run_before_turn(user_message)`;
- a bounded `anyio.fail_after()` timeout;
- recoverable handling for ordinary exceptions, invalid return types, and timeouts.

External cancellation must propagate. The implementation must not catch `BaseException` or swallow cancellation.

### 5.2 SessionAgent ordering

The turn-start order becomes:

```text
refresh tools
refresh schedules
run before-turn hook
add validated advice to ephemeral hook_message
build or rebuild dynamic system prompt
append and commit the user message
invoke the main AI loop
```

The advice is never:

- added to `Conversation`;
- persisted in main history;
- merged into AI request parameters;
- exposed as a Channel chunk;
- included in an after-turn Assistant message.

### 5.3 Failure behavior

The default total supervisor budget is 25 seconds. The Workspace may target a shorter live budget when a map already exists, such as 12 seconds, while first-map generation may use the full budget.

On timeout or recoverable failure:

1. use a recent validated advice only if it matches the same user and domain;
2. otherwise return no advice;
3. continue with the existing profile-driven answer path;
4. never roll back or reject the turn solely because supervision failed.

An incomplete map write is invalid. Persistence must use a temporary file followed by atomic replacement.

## 6. Supervisor Process Model

### 6.1 Logical persistence, process reuse

Each `user_id` has one logical supervisor identity. Its knowledge state and Session history persist. The OS Session process is started on demand and reused while healthy.

The first version must support:

- start on first learning question;
- reuse on later questions from the same user;
- probe the stored channel socket before reuse;
- restart once when the registered process is unavailable;
- degrade after the single restart fails.

Automatic idle TTL shutdown is deferred. The design allows it later without changing stored identity or map formats.

### 6.2 Reusing the existing subagent infrastructure

Automatic supervision reuses the implementation beneath:

- `tools/_subagent_helpers.py`;
- `tools/_background_process_registry.py`.

It reuses parent AI discovery, Gateway bindings, Windows TCP planning, process registration, readiness checks, and `ChannelCore` messaging. It does not ask the main LLM to call `subagent_plan`, `background_start`, `subagent_wait`, or `subagent_chat`.

The automatic path is:

```text
Session lifecycle
  -> Workspace SupervisorManager
  -> existing subagent/background helper functions
```

The normal public tools remain available for ordinary model-directed delegation.

### 6.3 Recursion prevention

The supervisor uses a dedicated Workspace and must never supervise itself. The supervisor Workspace declares a supervisor role, and any Session whose ID starts with `supervisor-` skips `system_before_turn` supervision.

## 7. Dedicated Supervisor Workspace

Create:

```text
examples/haitun-supervisor-workspace/
  AGENTS.md
  SOUL.md
  systems/system.py
  tools/
    knowledge_map_get.py
    knowledge_map_update.py
    user_heatmap_get.py
    user_heatmap_update.py
    supervisor_state_get.py
    supervisor_state_update.py
    wiki_related.py
  histories/.gitignore
```

The supervisor identity is:

> An independent side-channel supervisor that never interacts with the final user. It evaluates only user-question trajectories, aggregate stage profiles, domain maps, user heatmaps, and permitted retrieval results. It outputs validated supervision advice, not a user-facing answer.

The supervisor Workspace must not expose tools that can send final Channel replies, alter the main Conversation, read main reasoning, or recursively spawn more supervisors.

## 8. Input Isolation Contract

The request payload is built from an allowlist, never by copying and filtering the main Conversation.

Allowed fields include:

```json
{
  "event": "supervise_user_question",
  "user_id_hash": "...",
  "profile_id": "learning",
  "session_id_hash": "...",
  "turn_index": 4,
  "user_question": "...",
  "stage_profile": {
    "depth": 0.77,
    "goal": 0.44,
    "familiarity": 0.43
  },
  "previous_supervision": {
    "domain": "machine-learning",
    "topic": "overfitting"
  }
}
```

Forbidden input includes:

- Assistant messages;
- main Agent reasoning or thinking;
- drafts or intended answer plans;
- main Agent tool calls;
- tool results;
- full Conversation message arrays.

The supervisor's own history may contain supervision payloads, its structured advice, and its own map/retrieval tool results. Recent raw user questions may remain in the supervisor Session window; older information is compressed into heatmaps and aggregate state.

## 9. Knowledge Storage

### 9.1 Shared domain maps

Store shared objective maps under:

```text
examples/haitun-workspace/wiki/supervisor/maps/<domain-id>.yaml
```

A map contains:

- canonical domain ID and label;
- aliases;
- generation and update timestamps;
- scope and confidence;
- nodes with importance and cognitive level;
- parent/child relationships;
- typed edges such as `explained_by`, `diagnosed_by`, or `mitigated_by`.

Ordinary concepts are nodes in a broader domain map. A complex topic becomes a separate sub-map only after sustained depth or when the branch cannot be represented clearly in the parent map.

### 9.2 Per-user heatmaps

Store per-user state under:

```text
examples/haitun-workspace/wiki/supervisor/users/<user-id-hash>/
  overview.yaml
  domains/<domain-id>.yaml
  latest-advice.json
```

A domain heatmap records:

- first and last visit;
- question count;
- visited nodes and heat;
- evidence for surface, mechanism, practice, and strategy depth;
- cognitive-level history;
- intent history;
- weighted coverage ratio;
- local concentration;
- repeated surface-question count;
- recent breakout recommendations and suppression signals.

Objective maps are shared. Heatmaps and advice are isolated by `user_id`.

### 9.3 Map generation policy

For a new domain:

1. normalize the domain;
2. check the shared map store;
3. generate a baseline panorama if absent;
4. validate its structure;
5. write atomically;
6. map the current question to nodes;
7. update the user's heatmap.

For an existing domain, reuse the map. Expand only the affected branch when the question cannot be mapped, an important branch is missing, Wiki evidence materially changes the map, or the map is stale or low-confidence.

The first version uses YAML/JSON files and introduces no graph database, vector database, or new runtime dependency.

## 10. Learning-Question Classification

The Workspace performs a fast deterministic classification before starting expensive supervision.

Typical learning signals include:

- what, why, how to understand;
- principles, mechanisms, derivations, differences, comparisons;
- learn, understand, explain, examples, depth, framework, field, concept;
- code questions asking why behavior occurs or how a mechanism works.

Clearly operational requests may skip supervision, but the classifier must not exclude learning merely because the subject is code or a concrete system.

Ambiguous cases may be sent to the supervisor for lightweight classification. A non-learning classification creates no map, updates no heatmap, and returns no breakout advice.

## 11. Supervision Priorities

One turn may contain multiple findings. Priority order is:

1. P0 breakout opportunity;
2. P1 latent need;
3. P2 stage-profile shift;
4. P3 answer-strategy advice;
5. P4 insufficient evidence / observe.

### 11.1 Breakout types

The supervisor supports five types:

| Type | Trigger | Main answer effect |
|---|---|---|
| `broaden` | User wants a field panorama but stays in one local cluster | Place the topic in a broader framework |
| `deepen` | User wants depth but remains at definitions/examples | Move to mechanism, boundaries, or implementation |
| `reframe` | Current perspective prevents a useful conclusion | Change the decision or explanatory frame |
| `cross_domain` | A strong adjacent-field connection materially improves understanding | Add one justified cross-domain connection |
| `operationalize` | User's real goal has moved from knowledge to action | Shift toward decisions, risks, steps, or planning |

Breakout evidence uses:

- stated goal scope;
- weighted coverage gap;
- local concentration;
- mismatch between desired and observed depth;
- missing perspectives that would change the problem or conclusion.

A soft score may guide consistency:

```text
breakout_score =
    0.30 * coverage_gap
  + 0.25 * local_concentration
  + 0.25 * depth_mismatch
  + 0.20 * perspective_gap
```

Suggested interpretation:

- below 0.45: no breakout;
- 0.45 to below 0.65: light optional angle;
- 0.65 to below 0.80: explicit breakout section;
- 0.80 or above: restructure the answer around the missing framework or perspective.

The score is supporting evidence, not a rigid product rule.

### 11.2 Over-breakout suppression

Do not actively expand when the user explicitly asks for only the current answer, requests brevity or no expansion, is handling an urgent failure, or has not yet received a direct answer to the current question.

The main Agent must:

1. answer the current question first;
2. add at most one framework or one to three directions;
3. explain why the direction matters;
4. leave the choice to the user;
5. avoid repeating ignored recommendations.

Explicit refusal creates a temporary suppression signal. Repeated unaccepted recommendations lose priority.

## 12. Latent Need and Profile Shift

The supervisor recognizes the intent progression:

```text
knowledge curiosity
  -> advanced comparison
  -> solution selection
  -> cost assessment
  -> risk judgment
  -> implementation
  -> long-term planning
```

It also recognizes cognitive levels:

```text
L1 introductory understanding
L2 advanced comparison
L3 implementation decision
L4 strategic planning
```

Two consecutive turns with a clear level or intent transition confirm a profile shift. The first two turns generally remain `observing` unless the user states an unambiguous goal.

Because the supervisor cannot see the main answer, it reports `profile_shift_detected`, not `main_agent_missed_shift`. It may require the current main answer to adjust, but it cannot claim to have audited prior compliance.

## 13. SupervisorAdvice Contract

The supervisor outputs strict JSON with these sections:

```text
schema_version
advice_id
user_id_hash
profile_id
turn_index
classification
user_state
breakout
latent_need
profile_shift
response_strategy
map_updates
diagnostics
```

Key response-strategy enums include:

- `answer_depth`: `concise`, `balanced`, `deep`;
- `answer_scope`: `local`, `framework`, `cross_domain`;
- `goal_mode`: `explain`, `compare`, `decide`, `execute`, `plan`;
- `terminology`: `explain_all`, `explain_key_terms`, `professional`;
- `breakout_integration`: `none`, `light_footer`, `integrated_section`, `restructure_answer`.

The validator must:

- extract the JSON object;
- verify required sections and types;
- bound numeric values to 0 through 1;
- validate enums;
- limit directions to three;
- limit reason and evidence lengths;
- reject or remove nonexistent map-node references;
- resolve contradictory flags conservatively;
- label the result `live`, `repaired`, `stale`, or `unavailable`.

Malformed output must not reach the main system prompt directly.

## 14. Main Prompt Integration

The Workspace converts validated advice into a concise dynamic section. It does not inject the full raw JSON.

The section may include:

- current domain and topic;
- breakout type, confidence, reason, and directions;
- latent need and missing dimensions;
- confirmed or observed profile transition;
- concrete answer-structure instructions.

The main Agent must not expose internal supervision language, scores, user-level judgments, or the existence of a background supervisor. It translates the advice into natural guidance.

Precedence is:

```text
explicit current user instruction
  > high-confidence supervisor advice
  > current profile short-term state
  > current profile long-term state
  > user panorama defaults
```

The first implementation does not let SupervisorAdvice directly modify EMA dimensions. It affects the current answer only. Low-weight profile deltas may be introduced after real-world accuracy is measured.

## 15. Components and Files

Core changes:

- `src/psi_agent/session/system_prompt.py`: load and run `system_before_turn`, timeout, validation of the return container, recoverable errors.
- `src/psi_agent/session/agent.py`: invoke before-turn supervision before prompt construction and keep advice ephemeral.
- `src/psi_agent/session/AGENTS.md`: document lifecycle ordering, failure semantics, and isolation guarantees.

Haitun Workspace changes:

- `examples/haitun-workspace/systems/system.py`: define `system_before_turn` and add concise advice to the dynamic prompt.
- `examples/haitun-workspace/systems/supervisor.py`: manage per-user supervisor Sessions, payload isolation, advice validation, caching, and recovery.
- `examples/haitun-workspace/tools/_subagent_helpers.py`: reuse or expose stable internal helper entry points as needed without duplicating transport logic.
- `examples/haitun-workspace/tools/_background_process_registry.py`: reuse or expose a stable internal process-start entry point as needed.
- `examples/haitun-workspace/wiki/supervisor/`: shared maps and private heatmap state.
- `examples/haitun-workspace/demo_supervisor_breakout.py`: local two-user demonstration.
- `examples/haitun-workspace/AGENTS.md`: document the automatic supervisor architecture and deliberate isolation rules.

New supervisor Workspace:

- `examples/haitun-supervisor-workspace/` as defined above.

Documentation affected by changed behavior must be synchronized under the repository Definition of Done.

## 16. Concurrency, Cancellation, and Persistence

The main Session lock continues to serialize requests within one Session. Multiple Sessions may address the same user, so the supervisor layer requires:

- an in-process lock per `user_id_hash` for Session startup and user heatmap updates;
- an in-process lock per `domain_id` for first-map creation and map expansion;
- one restart attempt for a dead supervisor Session;
- shielded cleanup where cancellation crosses awaited cleanup operations;
- no `asyncio` APIs;
- atomic map and heatmap replacement.

Cross-process file locking is deferred. The persisted format must carry versions and timestamps so stronger conflict handling can be added later.

## 17. Logging

Use existing loguru conventions.

INFO:

- supervisor Session started;
- shared map first created;
- high-confidence breakout triggered.

DEBUG:

- supervisor cache and process reuse;
- map reuse;
- advice validation;
- lifecycle duration;
- lock acquisition and release.

WARNING:

- supervisor timeout;
- repaired JSON;
- socket recovery;
- failed map persistence;
- fallback to stale or unavailable advice.

Routine logs must not include raw `user_id`, API credentials, main reasoning, or full supervision prompts.

## 18. Testing and Acceptance

### 18.1 Session tests

Cover:

- absence of the new hook preserves existing behavior;
- a valid hook result reaches the builder;
- timeout and ordinary exceptions degrade safely;
- external cancellation propagates;
- invalid return types are ignored;
- advice is absent from Conversation and request parameters;
- schedule turns do not invoke user-learning supervision unless explicitly enabled.

### 18.2 Workspace tests

Cover:

- one supervisor identity per `user_id`;
- different users remain isolated;
- different `profile_id` values share panorama but retain stage context;
- first-map creation and later reuse;
- `broaden`, `deepen`, and `reframe` detection;
- explicit no-expansion suppression;
- two-turn profile transitions;
- JSON repair and bounding;
- dead socket restart;
- timeout degradation;
- recursion prevention;
- strict payload isolation.

### 18.3 End-to-end mock test

Use a per-request mock AI that distinguishes supervisor and main requests. Verify that supervision completes first, the validated advice appears in the main system prompt, and the main output changes accordingly.

### 18.4 Local demonstration

Alice asks to understand machine learning broadly but repeatedly stays on surface-level overfitting questions. The expected breakout is `broaden` into a field framework.

Bob asks to understand database indexes deeply but remains at definitions and benefits. The expected breakout is `deepen` into selectivity, lookup cost, and query planning.

The demo displays map status, domain, breakout type, score, persisted map/heatmap paths, and the resulting main answer strategy.

## 19. First-Version Success Criteria

The first version is successful when:

1. learning questions automatically invoke supervision without model tool-call discretion;
2. current-turn main generation waits for advice within a bounded budget;
3. every stable `user_id` reuses one logical supervisor identity;
4. new domains create shared maps and later questions reuse them;
5. per-user heatmaps remain isolated;
6. the supervisor receives no main answer, reasoning, draft, tool call, tool result, or full Conversation;
7. high-confidence breakout advice changes the current main prompt;
8. supervision failure never prevents a normal main answer;
9. focused tests, repository lint/type checks relevant to the changes, and local demonstrations pass.

## 20. Deferred Capabilities

The first version does not include:

- post-answer auditing of main-Agent compliance;
- age, profession, or registration-derived profile fields;
- cross-device identity linking;
- a graphical knowledge-map UI;
- mandatory external web research for every new map;
- graph or vector databases;
- automatic idle process TTL;
- cross-process strong file locking;
- direct SupervisorAdvice writes to profile EMA values;
- a response validator that guarantees the main Agent follows the advice.

These can be layered onto the lifecycle and storage contracts without changing the core separation between main answers and side-channel supervision.
