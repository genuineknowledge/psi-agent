---
name: regex-codegen
description: "Generating regex/substitution tables to transform or extract TEXT (log parsing, field extraction, find/replace cleanups) — where the deliverable is genuinely a set of regular expressions for text wrangling. Use for pattern extraction and bulk text substitution. NOTE: for HTML/XSS sanitization use the html-sanitization skill instead (regex is the wrong tool there); for encoding a COMPUTATION/state-machine/move-generator as an ordered re.sub pipeline graded by an executable oracle, use oracle-checked-substrate-synthesis instead."
---
### Programmatic Regex & State Machines
* Generate massive regex rule sets programmatically via scripts (e.g., cartesian products of state transitions) rather than hand-authoring.
* Normalize inputs into fixed-width, sentinel-prefixed canonical formats before applying mutations to enable strict positional anchoring.
* Structure complex transformations as multi-pass pipelines: expand/normalize -> generate all candidates (newline-separated) -> filter invalid states via negative lookarounds/deletions -> collapse to final spec.
* Use `re.MULTILINE` with `^` and `$` anchors alongside sentinel prefixes to isolate per-candidate mutations and prevent overlapping `re.sub` collisions.
* In `re.sub` replacements, treat regex metacharacters as literal and use back-references (`\1`) to preserve unmodified state.

### Pattern Extraction & Matching
* Use exactly ONE capturing group around the target data so `re.findall` returns a flat list of strings. Wrap all internal logic in non-capturing groups `(?:...)`.
* Order alternations widest-first (e.g., 3-digit before 2-digit) because regex alternation evaluates left-to-right, not longest-match.
* Avoid `\b` for alphanumeric boundaries, as it triggers on internal punctuation (hyphens, dots). Use explicit fixed-width lookarounds `(?<![A-Za-z0-9])` and `(?![A-Za-z0-9])`.
* To extract the last occurrence on a line, place a greedy `.*` (or `[^\n]*` for strict line-scoping regardless of `DOTALL`) before the capture group to force rightmost selection via backtracking.

### Validation & Environment Discipline
* Read the verifier script first to map the exact scoring contract, ignored fields, and evaluation API.
* Write regexes to disk using quoted heredocs (`<<'EOF'`) to prevent shell interpolation of backslashes and variables.
* Re-read the pattern from disk and validate using the exact Python `re` function and flags the grader uses.
* Account for `/bin/sh` being `dash` in minimal environments; avoid bash-isms like `pipefail` unless wrapped in explicit `bash -c`.
* Clean bytecode caches and temporary test artifacts before final submission to prevent stale state from masking results.
