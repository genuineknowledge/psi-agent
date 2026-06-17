---
name: code-review-checklist
description: PR review checklist for Python projects
category: coding
created_by: agent
created_at: 2026-06-01T00:00:00Z
updated_at: 2026-06-11T10:12:39Z
---

---
name: code-review-checklist
description: PR review checklist for Python projects
category: coding
created_by: agent
created_at: 2026-06-01T00:00:00Z
---

# Python PR Review Checklist

## Before Reviewing
- [ ] CI is green (tests, lint, type-check)
- [ ] PR description explains what and why
- [ ] Diff is reviewable size (<400 lines ideally)

## Code Quality
- [ ] New code has type hints (use `X | None`, not `Optional[X]`)
- [ ] No commented-out code or debug prints
- [ ] Imports are sorted and unused imports removed
- [ ] Docstrings on public functions/classes
- [ ] ruff / ty pass with zero issues

## Testing
- [ ] New behavior has tests
- [ ] Tests cover happy path + edge cases + error paths
- [ ] No flaky assertions (time-dependent, order-dependent)

## Design
- [ ] No duplication with existing code
- [ ] Functions are small and single-purpose
- [ ] Error handling is explicit, not swallowed
- [ ] Database / API calls have appropriate timeouts

## Security (if applicable)
- [ ] No secrets in code or config
- [ ] Input is validated/sanitized
- [ ] SQL uses parameterized queries