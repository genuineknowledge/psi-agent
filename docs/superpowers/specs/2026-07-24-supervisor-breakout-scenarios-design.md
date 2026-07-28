# Background Supervisor Breakout Scenario Evaluation Design

## Objective

Evaluate the background supervisor in two realistic, multi-turn user journeys and produce an auditable report showing the complete main-Agent conversation, isolated supervisor inputs and outputs, knowledge-map and heatmap changes, breakout decisions, and the effect of advice on the main Agent's response strategy.

Real LLM calls take precedence. If the upstream LLM is unavailable, the evaluation must preserve the real failure evidence and use deterministic protocol-compatible mock responses only to complete the demonstration. Real and mocked evidence must be labeled separately and must never be blended.

## Scenario 1: CEO Decides Whether to Adopt CI/CD

### Persona

- User ID: `demo-ceo-cicd`
- Profile ID: `executive-decision`
- Role: CEO of a small or medium technology company
- Priorities: cost, delivery speed, operational risk, investment return, and a usable decision
- Assumption: the CEO does not want an implementation tutorial before understanding the business decision

### Conversation Arc

Use four to five turns:

1. Ask whether the company should use CI/CD.
2. Ask about implementation cost and short-term benefits.
3. Supply company context such as team size, release frequency, test coverage, and production incidents.
4. Ask for an adopt, defer, or staged-adoption decision.
5. If useful, ask for a measurable pilot plan.

### Desired Breakout

The supervisor should consider `operationalize`, `broaden`, or `reframe`, moving the discussion beyond a technology yes/no answer toward:

- automated-test readiness;
- deployment frequency, change-failure rate, and recovery time;
- a minimum viable pipeline versus full automation;
- approvals, rollback, security, and organizational ownership;
- a time-boxed pilot whose measurements determine further investment.

Success means the main Agent turns the tool-selection question into a staged, evidence-based business decision without ignoring the CEO's request for a direct recommendation.

## Scenario 2: Legal Counsel Learns and Governs Agents

### Persona

- User ID: `demo-legal-agent-governance`
- Profile ID: `legal-learning`
- Role: legal counsel at a technology company
- Prior knowledge: law, contracts, privacy, compliance, and corporate policy
- Knowledge gap: little or no understanding of AI agents
- Goal: understand agents, identify implicated legal domains, and draft useful corporate rules

### Conversation Arc

Use six to seven turns:

1. Ask what an agent is.
2. Ask how an agent differs from a chatbot and conventional automation.
3. Ask which legal risks agent use creates.
4. Explore privacy, trade secrets, intellectual property, employment, product liability, authorization, or related fields.
5. Ask which governance mechanisms the company needs.
6. Request a concise `Company AI Agent Use Policy` draft.
7. If useful, stress-test the policy with a higher-autonomy or incident scenario.

### Desired Breakout

The likely progression is `deepen` to `broaden`, `cross_domain`, and `operationalize`, covering:

- autonomy level and allocation of legal responsibility;
- human approval gates;
- tool permissions and least privilege;
- logs, evidence retention, and auditability;
- data provenance and output intellectual property;
- third-party models, cross-border data, and supplier terms;
- incident response, suspension controls, and accountable owners.

Success means the user progresses from conceptual understanding to a policy grounded in the Agent lifecycle, while explanations remain accessible to a legal expert without assuming engineering knowledge.

## Execution Modes and Evidence Labels

### Real Mode

Run the current main Workspace and dedicated supervisor Workspace against the configured LLM. Preserve:

- request and response timestamps;
- complete user and main-Agent visible messages;
- the supervisor's isolated payload;
- raw child output and validated advice when available;
- system-prompt evidence that the advice section was injected;
- persisted latest advice, heatmap, and shared map files;
- errors from process start, timeout, transport, validation, or upstream model access.

### Deterministic Fallback Mode

If real execution cannot complete, use deterministic supervisor and main-Agent fixtures that exercise the real manager, validation, rendering, map, and heatmap code. Label every such turn `DETERMINISTIC MOCK`. A mock result cannot be cited as proof of real LLM quality or availability.

## Isolation and Identity Requirements

- The supervisor receives only the current user question and the established allowlisted aggregates.
- It must not receive the main Agent response, reasoning, draft, tool calls, or tool results.
- The two user IDs must yield distinct SHA-256 user directories and distinct heatmaps.
- Shared maps may be reused by domain, but user visitation state remains isolated.
- Main Session IDs must not start with `supervisor-`.

## Per-Turn Evidence

Record for every turn:

1. execution mode and timestamp;
2. simulated user message;
3. complete main-Agent response;
4. supervisor isolated input or a redacted structural representation proving the allowlist;
5. raw supervisor JSON when available;
6. validated advice;
7. breakout needed, type, score, reason, evidence, and directions;
8. response-strategy fields;
9. whether `## 旁路监督建议` appeared in the main prompt;
10. whether and how the visible answer reflected the advice;
11. stage-profile snapshot;
12. heatmap before and after;
13. shared-map before and after;
14. errors and their user-visible or data-quality impact.

## Output

Create one Markdown report containing:

- environment and evidence methodology;
- a complete transcript for each scenario;
- per-turn supervisor evidence;
- knowledge-map and heatmap evolution;
- a breakout timeline;
- analysis of what the supervisor added beyond a conventional answer;
- explicit separation of real and mocked evidence;
- achieved capabilities, observed limitations, and reproducible commands;
- links or paths to all generated state artifacts.

Substantial raw JSON may be placed in appendices, but the report must retain it in full rather than replacing it with summaries.

## Acceptance Criteria

- Both personas complete their intended multi-turn arc or have an exact real-mode failure record plus a complete deterministic fallback.
- At least one meaningful breakout occurs in each scenario.
- The main Agent answers the immediate question before integrating breakout guidance.
- The CEO scenario produces a defensible staged decision or pilot.
- The legal scenario produces a usable policy draft tied to Agent-specific risks and controls.
- The report contains complete visible dialogue and complete supervisor outputs.
- Separate user heatmaps are demonstrably isolated.
- Map reuse or map creation behavior is documented from filesystem evidence.
- No result is represented as real when it came from a mock.

## Out of Scope

- UI development;
- production privacy hardening beyond existing isolation rules;
- long-term map quality scoring or semantic deduplication;
- changing the supervisor policy merely to force a desired breakout;
- treating generated legal policy as jurisdiction-specific legal advice.
