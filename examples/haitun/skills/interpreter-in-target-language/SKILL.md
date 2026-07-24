---
name: interpreter-in-target-language
description: "Writing an interpreter/evaluator FOR a language, especially a metacircular evaluator written IN that same language that must interpret itself. Covers: reading a provided reference interpreter (interp.py) as the exact spec, the read-filename-from-stdin I/O contract, skeleton-first incremental bring-up tested against the reference, and the self-hosting bootstrap constraint. Use for 'write eval.scm / a metacircular evaluator / an interpreter that runs these test programs and itself' tasks."
---
# Writing an evaluator/interpreter (incl. metacircular self-hosting)

You are asked to write an interpreter — often in the SAME language it interprets
(a metacircular evaluator that must also evaluate itself). A reference
implementation (e.g. `interp.py`) defines the language exactly. The single
biggest failure mode is spending the whole budget writing a large evaluator
before ever running it, then timing out with nothing that works. The discipline
below is what separates a pass from a timeout.

## The reference implementation IS the spec — mine it, don't reinvent
* Read the provided interpreter end-to-end ONCE to extract: the exact special
  forms (`if`/`define`/`lambda`/`let`/`cond`/`begin`/`quote`/`set!`…), the
  primitive set and their arities, the truthiness rule (e.g. only `#f` is false —
  everything else including `0`/`'()` is true), how `define` shorthand works, and
  the data representation (pairs, how `nil`/None is produced).
* Note primitives that make your job easier: if the host exposes `read`/`fread`
  that PARSE s-expressions for you, you do NOT need to write a tokenizer/parser —
  consume parsed cons-cells directly. Confirm this before writing a parser.
* A quick census of the head symbols actually used across the test programs tells
  you which special forms you MUST support — but do this fast (one `grep`), don't
  turn reconnaissance into the whole session.
* **Cap reconnaissance HARD: one read of the reference + one census grep, then
  WRITE.** Do not spend a dozen turns confirming every primitive's arity and
  hunting for edge-case forms before a single line of the evaluator exists. The
  core special forms and ~20 primitives are visible in one read; everything else
  you discover by RUNNING failing tests. Front-loading exhaustive analysis both
  delays the deliverable and — on a flaky endpoint — risks the trial dying mid-
  reconnaissance with NOTHING on disk. Get a runnable skeleton written within the
  first handful of turns; refine the spec understanding against real test output,
  not against more reading.

## Match the I/O contract exactly
* These tasks usually specify a precise driver protocol: e.g. "read ONE line from
  stdin = the path to the program to interpret; remaining stdin is that program's
  input; program output goes to stdout." Implement exactly that — read the
  filename first, then leave the rest of stdin for the interpreted program.
* The grader compares your output to the reference interpreter's output on the
  SAME inputs. Don't print return values of top-level expressions unless the
  reference does; only explicit `display`/`newline`/`putchr` should produce
  output. A stray trailing print fails the byte comparison.
* Deliver at the EXACT path named (e.g. `/app/eval.scm`). See [[_universal]].

## SKELETON-FIRST, then grow against the reference — never write it all blind
This is the rule that wins these tasks:
1. Write a MINIMAL evaluator first: self-evaluating literals, symbol lookup,
   `if`, `define`, `lambda`, application with a global env of a few primitives.
2. **RUN IT IMMEDIATELY** against the reference on the simplest test
   (`echo ... | python3 interp.py eval.scm` vs the direct
   `python3 interp.py test/simple.scm`) and diff. Get ONE program passing before
   adding anything.
3. Add special forms / primitives ONE family at a time, re-running the test suite
   after each. Let failing tests drive what you implement next — do not
   speculatively implement forms no test uses.
* The losing pattern, to avoid at all costs: writing the full ~300-line evaluator
  end-to-end and only then trying to run it. If you reach the time limit you have
  ZERO working — a half-tested skeleton that passes 3 programs scores more than a
  "complete" evaluator that was never executed. Aim to have *something runnable*
  within your first few turns and a green simple-test well before the midpoint.

## Write the file ATOMICALLY, not line-by-line
* Emit the whole evaluator in ONE command, not dozens of `printf '...' >> file`
  appends — that pattern is slow (one round-trip per line), fragile (a single
  mis-sent newline or `!`/quote breaks it), and on a flaky endpoint it gets you
  killed mid-file with an unrunnable fragment. Use a single `cat > eval.scm
  <<'EOF' … EOF` heredoc, or base64-decode in one command for large files (see
  [[_universal]] file-writing guidance). Then edit incrementally with targeted
  rewrites, re-emitting the whole file when changes are substantial.

## The self-hosting (metacircular) constraint
* If your evaluator must interpret ITSELF, it may only rely on language features
  that IT implements. Every special form and primitive your `eval.scm` *uses* in
  its own source must also be a form/primitive your `eval.scm` *handles*. E.g. if
  you write the evaluator using `let` or `cond`, the evaluator must implement
  `let`/`cond`; if you call a helper like `cadr`, define it in-language.
* **DO NOT use a convenience builtin the HOST `interp.py` does not provide.** This
  is the #1 silent killer here. You will reflexively reach for Scheme staples like
  `(list ...)`, `length`, `map`, `append`, `assoc`, `cadr`, `member`, `modulo` —
  but the host interpreter defines only a SMALL primitive set (often just
  `+ - * / = < > <= >= cons car cdr null? pair? eq? equal? not and or` plus a few
  I/O ones; `list`/`map`/`length` are typically NOT among them). Before writing
  `eval.scm`, list the EXACT primitives `interp.py` registers (grep its
  `env.define(...)`), and treat that as your whole vocabulary. Anything outside it
  you must define in-language at the top of `eval.scm` (e.g. `cadr`, `caddr`,
  `to-list`, `length`), or rewrite to use only real primitives (build lists with
  nested `cons`, not `(list ...)`). A single stray `(list ...)` →
  `Error: Undefined variable: list` and the whole evaluator fails the moment that
  code path runs — even though it looks fine and passes the tests that don't hit
  it. (Real failure: an eval.scm that built closures with `(list 'procedure …)`
  passed 61/63 tests and failed only `closures.scm`, purely because `list` isn't a
  host primitive; switching to nested `cons` fixed it.)
* Test the metacircular path explicitly, exactly as the grader will:
  `echo -e 'eval.scm\ntest/calculator.scm\n(+ 7 8)' | python3 interp.py eval.scm`
  — i.e. your evaluator interpreting your evaluator interpreting a program. This
  catches features you used but forgot to implement.
* Watch recursion depth AND speed: a metacircular eval nests deeply (outer eval →
  inner eval → program) and is interpreted-on-interpreted, so it runs MUCH slower
  than the direct path. The grader times each metacircular run (e.g. 60s per
  program); a deeply-recursive test can take many seconds. Keep the evaluator's
  own call depth lean (tail-style loops, avoid gratuitous wrapper frames, use
  iterative list ops), and time the slowest test through the meta path before
  finishing — if it's near the limit, optimize the hot path (env lookup, the eval
  dispatch) rather than shipping something that times out only under double
  interpretation.

## Validate before finishing
* Run the FULL provided test suite through both paths (direct vs via your
  evaluator) and confirm outputs match for every program, then the self-hosting
  cases. The grader may also run HIDDEN tests (a `shadow_test/` dir) — so make
  the evaluator correct for the language generally, not overfit to the visible
  programs.
