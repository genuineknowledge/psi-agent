---
name: _universal
description: "Universal working discipline that applies to every task (always loaded)."
---
# Universal working discipline

Applies to every task regardless of domain.

## Orientation
- Operate in pass@1 mode: take a quick shell inventory of the workspace before
  deep reasoning. Do not finish without at least one concrete action.
- Read the task's own provided sources, checkers, and interfaces before assuming
  textbook behaviour — implementations often diverge from canonical ones.

## Match the contract exactly
- Match the public, scorer-facing interface exactly: the same command, paths,
  function signatures, and output format the verifier will use. No private
  helper flags or alternative entry points.
- Never edit the test harness, grader, or provided runner — those are restored
  at grading time. Fix YOUR program, not the checker.
- The deliverable lives at the EXACT path the task names (e.g. `/app/mystery.c`),
  not in a scratch dir. Drafting/iterating under `/tmp` is fine, but the file the
  grader opens is the only one that counts — a perfect `/tmp/v3.c` scores zero if
  `/app/mystery.c` is missing or stale. So: do your FINAL build-and-verify FROM
  the real deliverable path (compile `/app/mystery.c` itself, not a /tmp copy),
  and the moment a /tmp draft passes, copy it to the real path and re-verify
  there. Before calling task_complete, `ls`/`test -f` every required output path
  to confirm the graded artifact actually exists and is the version you verified.
  Do not let correct work die in a scratch file the grader never sees.

## Match the grader's expected value, not whatever a source happens to say
- The ground truth is what the GRADER checks, which is not always the raw bytes
  of an upstream source. Two opposite traps, both → reward 0:
  - Editing AWAY from the expected value: "fixing"/normalizing/collapsing a
    source that the grader actually wants verbatim (tags, padding, odd casing,
    leader/trailer, duplicated fields are often the exact match anchor). Don't
    gratuitously clean up a source that looks weird.
  - Copying a source TOO literally when it contains a token your output medium
    cannot reproduce. If a source holds a placeholder/modified token that is
    physically impossible to emit — e.g. an `X` (unknown/modified residue) in a
    FASTA, which no DNA codon can ever translate to; an ellipsized/truncated
    field; an encoded-entity stand-in — then the grader CANNOT be expecting that
    literal token. It must expect the RESOLVED real value. Echoing the
    impossible token guarantees the grader's `find()`/equality check fails.
- Resolving rule: when one authoritative source gives a non-reproducible or
  ambiguous token, cross-reference a SECOND authoritative source to recover the
  concrete value the grader expects (e.g. a FASTA `X` chromophore → expand to
  the real tripeptide from the protein database's clean sequence). Prefer the
  interpretation your output can actually reproduce end-to-end.
- Litmus test before finalizing: can your output, fed through the grader's own
  transformation (e.g. DNA→translate), EQUAL the target? If a position can never
  match by construction, you have the wrong interpretation — keep digging, do
  not ship it behind a "matches the source" self-check.

## Shell hygiene
- The bash tool often spawns /bin/sh (dash), which rejects `set -o pipefail`.
  Use `set -eu`, or invoke `bash -c '...'` explicitly when you need pipefail.
- Probe for missing tools up front and fall back (e.g. od/stat/python3) rather
  than repeatedly retrying an absent utility.
- To write a multi-line file, do NOT use a `cat <<'EOF' ... EOF` heredoc, and do
  NOT paste a large multi-line block in one command. All file content reaches the
  shell as terminal keystrokes; a single big block with many newlines risks one
  `\n` being mis-sent, which leaves the shell stuck waiting for the heredoc's
  closing marker — then every later command is swallowed and the session hangs
  unrecoverably until the budget is gone. Safer patterns — pick by file size:
    - **For a LARGE file (more than ~20 lines: a full program, an interpreter, a
      source module) transmit it as ONE single-line command** — base64-encode the
      content and decode in place: `printf '%s' '<base64>' | base64 -d > /path/out`.
      One command writes the whole file: no per-line round-trips, and base64 has
      no shell-special characters so it sidesteps quote/`!`-history/backslash
      escaping entirely. This is by far the cheapest and most robust way to land a
      big file — critical when each turn is an expensive LLM round-trip, where
      hundreds of per-line `printf`s would exhaust the budget before the file is done.
    - **For a SHORT file, build it line-by-line with appends**, each its own
      command: `: > /path/out` then `printf '%s\n' 'line1' >> /path/out`, … A
      mis-sent newline then only breaks one short line you can retry. Avoid
      embedding raw `!`, single quotes, or backslashes in `printf` args (history
      expansion / quote breakage) — another reason base64 wins for anything big.
  If the terminal ever does get stuck mid-heredoc (repeated prompts that don't
  return), stop sending EOF/Ctrl-D — that almost never recovers it. Instead send a
  fresh line that closes any open quote/heredoc (`\nEOF\n`) once, then switch to
  the line-by-line `printf` approach; do not spend many turns retrying the close.

## Verify before declaring done — reproduce the grader, don't invent your own
- "A file exists" is NOT success, and neither is "my own sanity check passed".
  Before calling task_complete you MUST reproduce the grader's judgement locally.
- First, FIND AND READ the provided test/checker file itself (e.g.
  `tests/test_*.py`). Extract the grader's LITERAL inputs/cases and its exact
  pass condition — the specific vectors, fixtures, expected values, tolerances,
  tool, and interface it uses. Do NOT make up your own test cases or trust
  intuition about what "should" be tested: a self-built mini-harness of a few
  invented samples that always "passes" is the #1 false-positive trap that ends
  in reward=0. Use the grader's OWN cases.
- Then run that same check against your solution, the way the grader runs it:
  - If the grader drives a browser/Selenium, run its actual vectors through your
    output (install + open in that browser locally if available); confirm the
    bad behaviour does not occur and the good behaviour does.
  - If the grader compares bytes/numbers, diff your output against the grader's
    own expected values, at the grader's tolerance.
  - If correctness depends on matching an external/canonical source (an API,
    a reference file, a fetched sequence), do NOT just diff your output against
    the raw source and call it verified — the grader's expected value may be a
    RESOLVED form of that source, not its raw bytes (see "Match the grader's
    expected value" above). Instead, run your output through the grader's own
    transformation (translate the DNA, parse the file, hash the bytes) and
    confirm the RESULT equals the target the grader asserts, position by
    position. If any position can never match by construction (e.g. you emitted
    a placeholder the medium can't reproduce), that is a real failure — keep
    fixing, do not ship it. A "✅ matches the source" self-check against the
    wrong reference is exactly how a wrong answer ships with high confidence.
  - If the grader runs commands, run those exact commands and check exit codes
    and stdout.
- Test EVERY case the grader file contains, each in isolation — not just one.
- Stop when enough is enough. Once your reproduction of the grader's OWN cases
  passes (every clean sample preserved, every malicious/edge case handled),
  call task_complete — do NOT keep inventing extra cases beyond what the grader
  tests, and do NOT re-run the same passing suite over and over. Right-size the
  effort: a simple task needs ONE clean confirmation, not an open-ended
  self-test loop. Over-verifying burns the time budget and can time out a task
  you had already solved.
- KEEP ITERATING only while a real case still fails — do not declare done on a
  known-failing solution, but equally do not spin on an already-passing one. If
  you genuinely cannot run the check, say so and test as close to the grader's
  real cases as possible rather than guessing.
- Watch the clock: if the grader's own cases already pass and you have spent a
  large share of the budget, finalize now rather than polishing.

## Budget
- Commit to an implementation early; reserve most of the budget for writing,
  building, and debugging. If the same approach fails repeatedly, switch
  strategy rather than retrying minor variations.
- Don't let setup eat the budget. A slow install/compile (apt-get, building a
  toolchain, downloading a model) should be ONE foreground command that runs to
  completion (chain a `&& echo __DONE__` marker), not a background `&` job you
  then poll across many empty "wait" turns — each idle poll is a turn that
  produces nothing and can starve the actual implementation. Get the environment
  ready in as few turns as possible, then spend the rest writing and verifying
  the deliverable. The most common way to score 0 on a large implementation task
  is to burn the whole budget on environment setup and never write the solution
  file at all.
