# Legal Supervisor Evaluation Design

## Objective

Evaluate Haitun's supervisor, adaptive profile, shared map, and private heatmap through a reproducible seven-task legal workflow for a technology-company legal professional. The evaluation uses synthetic facts and agreements under a mainland China company-law context and is not formal legal advice.

## Synthetic Company

- PRC limited liability technology startup;
- angel/Pre-A financing stage;
- two founders, employee incentive pool, and one institutional investor;
- products involve enterprise software and AI services;
- founder perspective is the default review position.

## Synthetic Documents

Create two shareholder agreements covering the same fictional company:

- Agreement A: investor-protective version with broad veto, aggressive repurchase, full-ratchet anti-dilution, founder restrictions, and strong information rights;
- Agreement B: balanced version with reserved matters thresholds, weighted-average anti-dilution, fault-based founder liability, reasonable transfer restrictions, and governance safeguards.

No real company name, person, deal, signature, seal, account, ID number, or confidential term may appear.

## Seven-Turn Workflow

1. Explain major company financing matters, process, and legal documents.
2. Review Agreement A from the founder perspective and identify risks.
3. Explain common equity-structure models and their trade-offs.
4. Build a comprehensive legal research library under two top-level frameworks: mandatory/basic shareholder-agreement matters and autonomous negotiated matters; subdivide and map applicable legal sources.
5. Compare Agreements A and B, risks, differences, advantages, and disadvantages.
6. Draft a standard-form legal document based on the fictional transaction with professional contract layout.
7. Design a company legal-management SOP.

## Supervisor Evaluation

Record per turn:

- first-turn warmup and later-turn participation state;
- Advice source (`live`, `cache`, `repaired`, `unavailable`, or explicitly labeled deterministic mock);
- breakout need/type/score/reason/directions;
- profile depth, goal, and familiarity changes;
- map revision and node/alias changes;
- heatmap history and active branch transitions;
- whether Advice appeared in the main prompt;
- whether the visible answer reflected the Advice;
- errors and their impact.

Expected progression:

```text
field overview
  -> founder-side risk judgment
  -> structure selection
  -> systematic legal research
  -> comparative document analysis
  -> professional drafting
  -> operational legal governance
```

Likely breakout modes include `broaden`, `deepen`, `reframe`, `cross_domain`, and `operationalize`. The supervisor must not force a breakout where the user requests a concise or narrowly scoped answer.

## Legal Research Boundaries

Any legal-source section must distinguish:

- confirmed statutory text or judicial rule;
- interpretation/inference;
- matters requiring current-source verification;
- mandatory corporate-law requirements versus contractual autonomy;
- company articles, shareholder resolutions, investment agreements, and shareholder agreements as distinct instruments.

Because legal rules change, a real research run must verify current official sources. Deterministic fallback may demonstrate structure but must not present invented article numbers as verified law.

## Deliverables

1. Complete questions-and-answers Markdown report.
2. Supervisor/profile/map/heatmap effect report.
3. Founder-side Agreement A risk-review report.
4. Agreements A/B comparison report.
5. Comprehensive shareholder-agreement legal research library.
6. Professionally formatted synthetic legal document in DOCX.
7. Company legal-management SOP in DOCX or Markdown plus DOCX.
8. Raw per-turn JSON evidence and synthetic source agreements.

## Acceptance Criteria

- All seven tasks have complete visible answers or an exact real-mode failure plus labeled deterministic fallback.
- Agreements A and B are synthetic, internally consistent, and comparable clause by clause.
- Founder review identifies control, economics, liability, exit, transfer, information, employment/IP, and dispute-resolution risks.
- Legal library uses the approved two-framework hierarchy and does not fabricate verified law.
- Drafted document is a real DOCX with contract-style headings, numbering, margins, fonts, signature section, and page layout.
- SOP covers intake, contract review, corporate governance, seals, litigation, compliance, external counsel, records, incidents, metrics, and responsibility.
- Supervisor inputs contain no main answer, reasoning, drafts, tool calls, or tool results.
- Real and mocked evidence are never blended.

## Evaluation Limit

This experiment evaluates reasoning structure, document workflow, adaptation, and supervision. It does not certify legal accuracy for a live transaction. Any production use requires current-law verification and qualified counsel review.
