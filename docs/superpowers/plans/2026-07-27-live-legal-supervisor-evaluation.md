# Live Legal Supervisor Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run seven legal tasks through the real main Agent and real supervisor without authored answers or mock Advice, preserving all successes and failures as evidence.

**Architecture:** Two neutral synthetic shareholder agreements are fixed test inputs. A fresh AI and main Session receive seven sequential user messages with a stable legal-worker identity. The runner captures SSE text, history, participation, metrics, Advice, maps, heatmaps, and generated files after each turn, then assembles reports from observed output only.

**Tech Stack:** psi-agent HTTP/SSE, PowerShell, JSON/YAML, Markdown, DOCX artifacts.

---

### Task 1: Prepare neutral synthetic inputs

- [ ] Create Agreement A and Agreement B as fictional Markdown fixtures with no analysis or risk labels.
- [ ] Verify they contain no real identity, signature, account, credential, or company data.

### Task 2: Run real seven-turn session

- [ ] Start fresh AI and main Session on unused ports using the current worktree executable.
- [ ] Send the seven approved legal questions sequentially with stable `user_id=experiment-legal-live` and `profile_id=legal-learning`.
- [ ] Reference Agreement A for founder review, both agreements for comparison, and Agreement B facts for drafting.
- [ ] Do not retry with authored or mocked content. Preserve exact transport, model, tool, timeout, and validation failures.

### Task 3: Capture evidence

- [ ] Save every user message and complete visible assistant response.
- [ ] Snapshot `latest-advice.json`, `participation.json`, `metrics.jsonl`, maps, heatmaps, profile, history, and generated files after every turn.
- [ ] Verify supervisor input isolation from main answer and reasoning.

### Task 4: Assemble observed-result documents

- [ ] Create a questions-and-answers report containing only observed responses.
- [ ] Create a supervisor effect report containing only observed Advice/state changes.
- [ ] Reference any Agent-generated review, comparison, research, DOCX, or SOP files without rewriting their content.
- [ ] Create a stability report with success/failure counts and observed timing metrics.

### Task 5: Verify and hand off

- [ ] Confirm no mock label or authored answer is present in the live result set.
- [ ] Confirm reports distinguish missing output from successful output.
- [ ] Provide exact artifact paths and reproduction commands.
