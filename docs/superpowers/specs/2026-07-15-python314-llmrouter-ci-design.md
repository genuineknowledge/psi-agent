# Python 3.14 LLMRouter CI Environment Design

## Goal

Make every GitHub workflow that synchronizes or builds psi-agent use a
consistent, reproducible Python 3.14 environment capable of building
`litellm==1.92.0`, while leaving README files unchanged.

## Root Cause

`llmrouter-lib==0.3.1` depends on LiteLLM, whose source distribution builds a
Rust extension with PyO3 0.23.5. That PyO3 version rejects Python 3.14 unless
`PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` is present during the build. Local shell
variables are not inherited by GitHub Actions, so clean runners fail at
`uv sync` even though dependency resolution and `uv.lock` succeed.

## Workflow Scope

The change covers:

- `.github/workflows/ci.yml`;
- `.github/workflows/nuitka.yml`;
- `.github/workflows/pyinstaller.yml`;
- `.github/workflows/auto-alpha-tag.yml`.

README and README_en are explicitly outside scope.

## Shared Build Environment

Each workflow defines this non-secret workflow-level variable:

```yaml
env:
  PYO3_USE_ABI3_FORWARD_COMPATIBILITY: "1"
```

It applies to dependency installation and package/build commands without
duplicating job-level declarations. API keys are not introduced into CI.

Every `astral-sh/setup-uv@v7` invocation uses the locally verified uv version
and dependency cache:

```yaml
with:
  version: "0.11.23"
  python-version: "3.14"
  enable-cache: true
  cache-dependency-glob: |
    uv.lock
    pyproject.toml
```

The cache stores uv downloads rather than `.venv`, so OS and Python binary
differences cannot reuse an incompatible virtual environment.

## Frozen Synchronization and Rust

Every project synchronization command changes from `uv sync` to
`uv sync --frozen`. CI must consume the committed lock file and fail when
`pyproject.toml` and `uv.lock` disagree rather than silently relocking.

Jobs that execute sync install a stable Rust toolchain immediately before it:

```yaml
- uses: dtolnay/rust-toolchain@stable
- run: uv sync --frozen
```

This applies to CI lint/test and the Nuitka/PyInstaller matrix jobs. Publish
jobs execute `uv build` but do not resolve/install LiteLLM, so they retain the
shared compatibility variable and fixed uv version without an unnecessary
Rust setup step.

`UV_FROZEN` is not set globally because packaging jobs also run `uv pip
install` and `uv build`; freezing is expressed only on project sync commands.

## Smoke Validation

After the lint job synchronizes the environment, it imports LiteLLM and
`LLMMultiRoundRouter`, asserts `_decompose_and_route` exists, and verifies the
three packaged prompt resources:

- `agent_decomp_route.yaml`;
- `agent_decomp_cot.yaml`;
- `agent_prompt.yaml`.

The existing test job depends on lint, so an invalid LLMRouter environment
fails before the duplicate test-job synchronization and test suite.

## Preserved Behavior

The change does not alter job dependencies, trigger conditions, OS matrices,
Node/SPA builds, Inno Setup packaging, artifact names, tag creation, or PyPI
trusted publishing.

## Validation

Local/static validation will parse every workflow as YAML, assert all setup-uv
steps use version 0.11.23 and caching, assert all project sync commands are
frozen and preceded by Rust setup in their job, confirm all four workflow-level
compatibility variables, and run `git diff --check`. GitHub Actions remains the
authoritative clean-runner proof because the PyO3 build behavior occurs during
remote environment creation.

## Compatibility Note

The environment variable is a forward-compatibility workaround, not formal
PyO3 0.23.5 support for Python 3.14. When LiteLLM publishes a Python 3.14 wheel
or upgrades to a PyO3 release that supports 3.14, CI should test removing the
variable in a dedicated change rather than retaining it indefinitely.
