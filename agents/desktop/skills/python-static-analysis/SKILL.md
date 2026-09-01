---
name: python-static-analysis
description: Static analysis and type checking for Python
category: coding
created_by: agent
created_at: 2026-02-10T00:00:00Z
updated_at: 2026-06-09T09:49:37Z
---

# Python static analysis and type checking

Use this skill when improving Python code quality with linting, formatting, import checks, dead-code detection, and type checking.

## Goals
- Catch bugs before runtime
- Keep style and imports consistent
- Enforce type safety where practical
- Make CI feedback fast and actionable

## Recommended toolchain
Prefer a small, modern stack:
- `ruff` for linting and many style rules
- `ruff format` or `black` for formatting
- `mypy` or `pyright` for type checking
- `pytest` for test execution

A good default is:
- `ruff check .`
- `ruff format --check .`
- `mypy .`
- `pytest -q`

## Type hints: practical guidance
Add type hints to:
- public functions
- return values
- dataclass fields
- complex module-level constants
- interfaces and protocol-like abstractions

Example:
python
from collections.abc import Iterable

def total_lengths(items: Iterable[str]) -> int:
    return sum(len(x) for x in items)


Prefer:
- built-in generics: `list[str]`, `dict[str, int]`
- `X | None` over `Optional[X]` when supported
- `TypedDict` for structured dicts
- `Protocol` for duck-typed interfaces
- `Literal` for constrained string options

Avoid:
- unnecessary `Any`
- misleading hints that do not match reality
- annotating everything before stabilizing APIs

## Mypy setup
Minimal `pyproject.toml` example:
toml
[tool.mypy]
python_version = "3.12"
warn_unused_ignores = true
warn_redundant_casts = true
warn_return_any = true
no_implicit_optional = true
disallow_untyped_defs = false
check_untyped_defs = true
strict_equality = true


For stricter projects, enable:
- `disallow_untyped_defs = true`
- `disallow_any_generics = true`
- `strict = true` (only if team is ready)

## Ruff setup
Example `pyproject.toml`:
toml
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]


Useful rule groups:
- `E`, `F`: pycodestyle/pyflakes basics
- `I`: import sorting
- `B`: bugbear
- `UP`: pyupgrade

## CI recommendations
In CI, run in this order:
1. formatting check
2. lint
3. type check
4. tests

This gives fast feedback and cheaper failures first.

## Triage guidance
When static analysis fails:
- fix real bugs first
- simplify code before silencing warnings
- use targeted ignores with comments when needed
- avoid broad disables

Examples:
- good: `# type: ignore[arg-type]  # third-party stub is incorrect`
- bad: `# type: ignore`

## Incremental adoption
For existing codebases:
- start with lint + format
- type check only selected packages or `src/`
- require type hints on new or changed code
- raise strictness gradually

## Output expectations
When helping a user:
- identify the failing tool and exact error class
- explain whether it is a correctness, style, or typing issue
- propose the smallest safe fix
- include updated code or config when useful
- mention tradeoffs if multiple fixes are possible
