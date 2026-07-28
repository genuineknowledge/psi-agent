# FusionFlow Executor Declarations

## Goal

Add the two static executor declarations agreed in the reference conversation
while keeping dispatcher execution out of this pull request.

## Contract

| Declaration | Meaning |
| --- | --- |
| `program_path(Program) -> Path` | Catalog identity for a future Program dispatcher |
| `agent_system_prompt(Agent) -> Instruction` | Stable Agent system prompt identity |

`agent_system_prompt` maps to `AgentConfig.system_prompt`. The per-Step
`step_instruction` continues to map to `AgentInvocation.prompt`, and consumed
Artifacts remain invocation context. `AgentConfig.system` and the duplicate
`AgentConfig.prompt` alias are removed.

`program_path` and `agent_system_prompt` remain residual assertions for a later
catalog/dispatcher implementation.

## Non-goals

- Program or Agent dispatcher execution.

Renaming the Agent config payload changes its cache key, so old cached Agent
calls may execute again after migration.
