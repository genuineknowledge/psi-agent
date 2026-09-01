---
name: codebase-inspection
description: "Inspect codebases w/ pygount: LOC, languages, ratios. Use when asked how many lines of code a repo has, its language breakdown/composition, its size, or its code-vs-comment ratio."
category: coding
---

# Codebase Inspection

Use this skill to analyze a repository's size and composition: total lines of
code (LOC), per-language breakdown, file counts, and code-vs-comment ratios.
It is backed by [pygount](https://pypi.org/project/pygount/), which uses
Pygments to recognize hundreds of languages (similar to `cloc`/`sloccount`).

Reply in Chinese unless the user clearly uses another language.

## When to use

Trigger on requests like:
- "How many lines of code is this project?"
- "What languages is this repo written in?" / language composition or breakdown
- "How big is this codebase?" / repo size
- code-vs-comment ratio, comment coverage, documentation density

## How to run

Prefer the `inspect_codebase` tool — it wraps pygount and returns structured
JSON (totals, `code_to_comment_ratio`, `comment_percent`, and a per-language
breakdown). It runs the blocking scan in a worker thread.

- Whole repo: `inspect_codebase(path=".")`
- One language: `inspect_codebase(path=".", suffixes="py")`
- A few languages: `inspect_codebase(path=".", suffixes="py,ts,tsx")`
- Add extra skip folders on top of the defaults: `folders_to_skip="[...], data, fixtures"`

If the tool is unavailable, fall back to the pygount CLI:

```bash
pip install --break-system-packages pygount 2>/dev/null || pip install pygount
pygount --format=summary \
  --folders-to-skip=".git,node_modules,venv,.venv,__pycache__,.cache,dist,build,.next,.tox,.eggs,*.egg-info" \
  .
pygount --format=json .   # for programmatic parsing
```

## Folder skipping (important)

Always exclude dependency and build folders. Without it, pygount crawls
everything and may take minutes or hang on large trees. The tool skips a
sensible default set (`.git`, `node_modules`, `.venv`, `venv`, `dist`,
`build`, `.next`, `__pycache__`, `.tox`, `vendor`, `third_party`, ...). Tune
per project type when needed:

- Python: `.git, venv, .venv, __pycache__, .cache, dist, build, .tox, .eggs, .mypy_cache`
- JS/TS: `.git, node_modules, dist, build, .next, .cache, .turbo, coverage`

## Interpreting results

- Columns/fields: `language`, `files`, `code`, `documentation` (comments),
  `empty`, `string`, `source` (code + string).
- Pseudo-languages flag special cases and are marked `is_pseudo_language`:
  `__empty__`, `__binary__`, `__generated__`, `__duplicate__`, `__unknown__`.
- `code_to_comment_ratio` = code lines / documentation lines; `comment_percent`
  is documentation as a share of code+documentation.

## Pitfalls

- Markdown reports **zero** code lines — pygount treats all Markdown as
  documentation/comments. This is expected behavior, not a bug.
- JSON code counts run low (pygount counts JSON conservatively); use `wc -l`
  for an accurate raw line count of JSON files.
- For large monorepos, target languages with `suffixes` instead of scanning
  everything.
