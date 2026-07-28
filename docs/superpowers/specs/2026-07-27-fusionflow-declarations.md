# FusionFlow Executor and Value-Source Declarations

## Goal

Add the three static declarations agreed in the reference conversation while
keeping execution, Human suspension, and external-event delivery out of this
pull request.

## Contract

| Declaration | Meaning |
| --- | --- |
| `program_path(Program) -> Path` | Catalog identity for a future Program dispatcher |
| `agent_system_prompt(Agent) -> Instruction` | Stable Agent system prompt identity |
| `value_from(Constant) -> Path` | Catalog identity that supplies a workflow value |

`agent_system_prompt` maps to `AgentConfig.system_prompt`. The per-Step
`step_instruction` continues to map to `AgentInvocation.prompt`, and consumed
Artifacts remain invocation context. `AgentConfig.system` and the duplicate
`AgentConfig.prompt` alias are removed.

## Compilation

`value_from(value) == source`:

- records `ValueSource(value_id=value, source_id=source)`;
- implicitly marks `value` as a workflow input Artifact;
- creates no Event node and no graph edge.

The compiler rejects duplicate sources for one value, a source-managed value
that is also Step-produced, and a source-managed value that is neither consumed
nor a workflow output. Results are deterministic regardless of assertion order.

`program_path` and `agent_system_prompt` remain residual assertions for a later
catalog/dispatcher implementation.

## Non-goals

- Program, Agent, or Human dispatchers;
- Human request persistence, response correlation, or resume;
- source listeners, event inboxes, delivery, or workflow wake-up;
- Feishu approval integration.

Renaming the Agent config payload changes its cache key, so old cached Agent
calls may execute again after migration.
