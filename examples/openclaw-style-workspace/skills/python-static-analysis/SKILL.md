---
name: python-static-analysis
description: Static analysis and type checking for Python
category: coding
created_by: agent
created_at: 2026-02-10T00:00:00Z
updated_at: 2026-06-11T10:12:39Z
---

---
name: python-static-analysis
description: Static analysis and type checking for Python
category: coding
created_by: agent
created_at: 2026-02-10T00:00:00Z
---

# Python Static Analysis & Type Checking

## Toolchain
- **ruff** — linting (`ruff check`) + formatting (`ruff format`). Single tool, fast, replaces flake8/isort/black.
- **ty** / **mypy** — type checking. `ty check` (faster) or `mypy` (more plugins).
- **pyright** — alternative type checker, good for strict mode.

## Configuration (`pyproject.toml`)
```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM"]

[tool.ty]
pythonVersion = "3.12"
```

## CI Integration
Always run in CI:
```yaml
- run: ruff check .
- run: ruff format --check .
- run: ty check
```

## Type Hinting Best Practices
- Annotate all function signatures (args + return type).
- Prefer `X | None` over `Optional[X]` (Python 3.10+).
- Use `TypeAlias` for complex types.
- Enable strict mode incrementally with `# ty: ignore` as needed.
- Never use `Any` as an escape hatch — use `object` or a Protocol instead.