---
name: test-driven-development
description: Red-green-refactor TDD cycle for writing code test-first.
---

TDD cycle for every new feature or bug fix:

1. **Red** — write a failing test that describes the desired behaviour. Run it and confirm it fails for the right reason.
2. **Green** — write the minimum production code to make the test pass. Do not over-engineer.
3. **Refactor** — clean up both production and test code without changing behaviour. Run tests again.
4. Repeat from step 1 for the next requirement.

**Guidelines:**
- Test names should describe behaviour, not implementation: `test_returns_empty_list_when_no_items` not `test_get`.
- One assertion per test where possible.
- Tests should be fast and not touch the filesystem/network unless that is the feature under test.
- If writing a test is hard, the design is probably wrong — simplify the interface first.
- Use `bash` tool to run the test suite after each cycle: confirm red → green → still green after refactor.
