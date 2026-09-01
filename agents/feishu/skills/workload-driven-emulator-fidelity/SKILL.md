---
name: workload-driven-emulator-fidelity
description: "Use ONLY when YOU must WRITE the engine yourself — author a CPU/ISA emulator, language interpreter, bytecode VM, metacircular evaluator, or protocol replayer (from scratch or by extending a stub) that then faithfully runs a REAL provided program end-to-end to a reference observable (exact stdout, a self-interpret round-trip, a produced output). The deliverable is the interpreter/emulator YOU build. DO NOT pick this skill if a runnable host/VM/loader is ALREADY PROVIDED and you must not modify it — then you are not writing an engine, you are producing the artifact that the provided engine runs, which is [[host-conformant-offline-reconstruct]]. Covers (for the write-the-engine case): decoding instructions and modeling registers/memory, discovering the real syscall ABI (numbering + register/arg convention) from the program's own source, servicing host calls against the real filesystem, running long-lived programs under a timeout, time-boxing reconnaissance so you actually ship a runnable skeleton, reverse-engineering which preprocessor branch / .o is truly compiled, and iterating by RUNNING the real workload. Use for 'write an interpreter/emulator/VM that runs this program and produces <observable>' tasks. For language/metacircular evaluators see [[interpreter-in-target-language]]."
---
# Building an engine that faithfully runs a real artifact

You must build an engine (CPU/ISA emulator, language interpreter, bytecode VM,
metacircular evaluator, protocol replayer) that runs a **real provided artifact**
to a **reference observable** the grader checks (a saved frame/file, exact stdout,
a self-interpret round-trip). There is no written instruction set — **the
specification IS the workload**. Correctness is judged by running the real thing,
not by your own test cases.

The single decisive split between a pass and a timeout on these tasks is NOT how
deeply you understand the ISA — it's **whether you stop reverse-engineering in
time to write a runnable engine and start iterating by running it.** Trials that
spend the whole budget reading source (the libc shim, the ABI, every opcode) and never
emit a runnable engine score **zero**; a half-finished engine that boots and
produces *an* observable beats a "complete" one that was never executed.

## STEP 0 (before any code): read the grader and write down the DONE definition
* **Open the checker/test file FIRST and copy out every concrete thing it asserts**
  — the exact output path(s), the exact byte/text it greps for (a precise banner/
  progress line, a magic header, a size, a similarity threshold), the command it
  runs, and HOW it captures output (does it read the file? capture stdout? both?).
  These literals ARE your definition of done. This costs one read and prevents the
  most insidious failure on these tasks (below). Skipping it is the #1 cause of a
  confident-but-wrong finalize.
* **"My engine produced the headline artifact" is NOT "the task passes" — beware
  the FALSE SUCCESS.** Graders typically assert MORE than the obvious output:
  a common pattern is a **lenient** check on the main artifact (a similarity
  threshold a half-correct output can sneak past) PLUS a **strict** check that the
  workload truly ran to depth — e.g. an exact progress/initialization line the
  program only prints once it has genuinely reached a late stage. An engine with a
  subtle execution bug can emit *an* artifact that scrapes past the lenient check
  while the workload never reached that late stage, so the strict line is absent
  and the task scores 0 despite a plausible-looking artifact. Treat the strict
  assertion as the real bar: confirm the workload actually executes far enough to
  emit it. (Observed: trials whose engine wrote a size-correct, ~similar frame —
  passing the file checks — but whose boot log stopped several init stages short of
  the exact line the grader greps for; they self-judged "frame produced, done" without
  ever opening the checker, and scored 0. The passing trials had read the checker,
  knew the exact required line, and verified the boot reached it.)

The single biggest behavioral failure here is finalizing on your OWN notion of
success ("the artifact looks right") instead of the grader's. Read it, list its
assertions, and make every one green before you stop.

## Reconnaissance is time-boxed — recon → SHIP A SKELETON → run-driven refine
* You DO need to reverse-engineer the contract first (see next section), but cap
  it HARD: a focused pass over the artifact + its source to nail the **ABI edge**
  (entry point, syscall/host-call numbers + calling convention, IO/output path,
  memory model), then **WRITE a runnable skeleton engine and RUN the real
  workload.** Do not keep confirming every opcode and edge form before a single
  line of the engine exists.
* **Front-loading exhaustive analysis is the #1 killer here.** It both delays the
  deliverable and — on a flaky/slow endpoint, where each turn is an expensive
  round-trip and you may get far fewer turns than you expect — risks the trial
  dying mid-recon with NOTHING on disk. You cannot count on a fast endpoint giving
  you enough turns; bank a runnable engine early.

### THE RUN GATE — the most important rule on these tasks, treat it as mandatory
Writing a large engine straight through to completion and only running it at the
end is the dominant way these trials score 0 — it ties with not-reading-the-grader.
The instruction set looks like it must be complete before anything works, so the
natural move is to author loader + memory + registers + decode + every ALU family +
FPU + every syscall across a dozen turns, then run. That is the trap. Instead:
* **You may not write the engine's body past a minimal core until you have a real
  `run the engine on the artifact` output pasted in this conversation.** The first
  successful run is a HARD GATE, not a milestone you drift toward — hit it inside
  the first third of your budget.
* **The minimal core that earns the first run is small and fixed:** load the
  artifact + set up memory/PC/SP and any required base registers + a decode loop
  that dispatches the handful of opcodes the entry path actually executes +
  **trap-LOUDLY-on-unknown** (print the unimplemented opcode/PC/syscall and halt).
  It does NOT need the full ISA, the FPU, unaligned loads, or most syscalls. It
  needs to LOAD and STEP and then tell you the first thing it doesn't know.
* **After it runs, grow ONLY in run→trap→fix cycles: add what the trap names, then
  RUN AGAIN — every turn ends in a run, never in "now I'll also add the next
  family".** If you catch yourself writing a second or third instruction family
  (or the FPU, or unaligned load/store, or more syscalls) without an intervening
  run, STOP and run what you have. Batch obviously-needed families when you add
  them, but the loop is always write-a-bit → RUN → read trap → repeat.
* **Concrete anti-patterns that mean you've left the loop — all observed in trials
  that timed out with engine run-count = 0:** "writing it in parts / in chunks",
  appending family after family across turns, repeatedly `sed`/rewrite-ing a file
  you have **never executed**, or saying "now the FPU and unaligned loads and the
  main loop" before ever seeing the engine load the binary. Re-editing an unrun
  engine is a budget black hole. The fix is the same every time: RUN IT NOW, even
  half-built, and let the trap tell you what's actually next.
* After the skeleton runs, **let the real workload drive what you implement next**:
  run it, see where it traps (`unknown opcode`, `unhandled syscall N`, wrong
  output), add exactly that, repeat — but batch obvious families (all loads/stores,
  all branches, the arithmetic block) so you're not single-stepping the budget away.
  Where practical, verify decode/execute on a MINIMAL program first (a hello-world
  that makes one syscall) to confirm the ABI and a few instructions before turning
  the full binary loose.
* Write the engine ATOMICALLY (one base64-decode command for a large file), not
  via dozens of per-line `printf`/`sed` appends — see [[_universal]] file-writing
  guidance. A mis-sent newline mid-heredoc can hang the session until the budget is
  gone, and per-line edits to a not-yet-run file waste turns you need for running.

## Reverse-engineer the REAL contract — and don't trust source at face value
* **The ABI edge is usually NOT the textbook/native one.** A binary cross-built
  for a custom VM often issues host calls with *another* OS's numbering (e.g.
  x86_64 Linux `read=0 write=1 open=2 exit=60`, NOT the target ISA's native table
  like MIPS o32 `write=4004`). Assume the canonical table and every call dispatches
  wrong and the workload never gets going. Grep the program's libc/`stdlib` shim,
  its syscall wrapper, and any `#define SYS_*` to learn the live numbering — and
  **map the calling convention exactly**: which registers hold the syscall number
  and args and where the return goes (e.g. MIPS: number in `$v0`, args in
  `$a0–$a3`, return in `$v0` — so get the register-file indices right,
  `regs[2]`, `regs[4..7]`).
* **Service syscalls against the real environment — don't reimplement the
  program's job.** Implement enough host calls (`open/read/write/lseek/close/
  brk/mmap/exit`) against the real filesystem that the binary's OWN code produces
  the artifact at the path it chooses. You provide a correct-enough CPU + syscalls;
  the binary does the rest.
* **Wire the standard streams, not just files — `write` to fd 1/2 must reach the
  process's REAL stdout/stderr.** A grader frequently asserts on a banner/log line
  the program prints (an init message, a version string), captured by running your
  engine and reading its stdout. If your `write` handler only services file
  descriptors from `open` and drops or mis-routes fd 1/2, the program's own
  `printf` produces nothing on stdout and that assertion fails even though file
  output is perfect. Map fd 0/1/2 to real stdin/stdout/stderr explicitly.
* **CONFIRM which branch is actually compiled before you build on it — the
  highest-value 30 seconds of recon.** Conditional-compilation switches are the
  classic misread: a `#define`-gated alternate-IO block (an in-memory
  fake-filesystem, a buffered shim, a debug path) sitting inside an `#if 0 …
  #endif` is **disabled**, so IO falls through to the OTHER branch — often plain
  real host syscalls — NOT the shim it looks like at a glance. Misreading a
  disabled block as active sends you down a hard, wrong path (e.g. inventing a way
  to "leak" data out of an in-memory buffer that the program never actually uses),
  burning the whole budget without shipping. So: check the `#if 0`/`#ifdef`/`#else`
  context around any IO-routing define, and which `.o` is actually linked, before
  concluding how IO is routed. When in doubt, the simplest live path (the program
  does its own IO via real syscalls and writes its own output file) is usually the
  intended one.
* **Implement only the slice the workload touches, but get it EXACTLY right.** You
  don't need the whole ISA/language — only what this artifact exercises. But the
  used slice must be faithful: delay slots (the instruction after a branch still
  runs), sign/zero-extension, unaligned/width-specific access, endianness, FP
  representation, evaluation order/scoping. A subtly-wrong core still boots and
  produces *an* output that fails the similarity/exact check.

## When the skeleton boots but misbehaves — map the SYMPTOM to a root cause
The run-driven loop only works if you can read its failures. These engine-class
bugs surface as vague symptoms, not clear error messages — when you see one, suspect
the matching cause before random-poking (examples are illustrative, not this task's
answer):
* **Crashes/segfaults the instant it starts, before any real work** → an
  uninitialized ABI precondition the runtime assumes: a global/data-pointer base
  register the toolchain expects set up at entry (e.g. a `$gp`/TOC/PIC base whose
  value lives in the ELF/reginfo, not computed by code), the stack pointer not
  placed, or argc/argv/env not laid out where the entry stub reads them. Read the
  entry stub and program headers; set these from the artifact's own metadata.
* **Dies trying to grow/allocate memory, or "out of memory" on a normal-looking
  program** → the program's allocator wants a large flat region (a big static
  BSS heap, or `brk`/`mmap` growth). A dense backing array for the whole address
  space blows up — model memory **sparse/paged** and honor the growth syscalls.
* **Boots and runs but the output is subtly wrong/garbled** → a silent-correctness
  bug in the core: control transfer that ignores the delay slot, a return address
  computed off the branch instead of past its delay slot, wrong sign/zero-extension
  or load/store width, endianness, or floating-point modeled with the wrong
  register pairing/width mode. Trace a few known basic blocks by hand against a
  reference disassembly to localize it.
* **Hangs / spins forever producing nothing** → either you forced the long-lived
  workload to behave like a one-shot, or an unimplemented op/host-call is silently
  no-oping in a loop. Add trap-on-unknown so a missing instruction/syscall is loud,
  not silent.

## You supply a faithful core + correct edge — the workload computes the result
* **Do NOT render/compute the observable yourself.** If the program writes its own
  frame/log/file, your engine just needs a correct-enough core + the right host
  calls so the program's own code produces the artifact at the path it chooses.
  Honor that exact output path (read it from the source); the grader checks it.
* **Hand-faking the observable is an anti-path** — it fails the moment the grader
  uses a fresh input. Genuine fidelity is what the reference observable rewards.

## Respect liveness — many workloads never exit
* The artifact often loops forever (animating/serving) after producing the first
  observable. The grader runs it under a timeout and checks the artifact exists —
  so **do NOT force termination and do NOT fake an early exit.** Make sure the
  output is flushed/written to the path before the timeout, then let it loop.

## Verify by reproducing the grader's FULL check, then STOP — don't over-verify
* Prove it by **running the real workload the grader's way** (same command, same
  paths, same comparisons/tolerances), not via your own invented cases. Find and
  read the checker FIRST and enumerate **every** assertion it makes — see
  [[_universal]] for the reproduce-the-grader discipline and the exact-path rule.
* **The grader usually checks MORE THAN ONE observable — satisfy ALL of them, not
  just the obvious headline artifact.** A produced file is often only one of
  several assertions: the same checker may also require specific text on **stdout**
  (a boot/init banner the program prints), an exit condition, a secondary file, a
  size/format property, or a similarity threshold. These are independent host-call
  paths — e.g. the program's `write` to fd 1 must actually reach the process's real
  stdout, not just its file output. **The headline artifact passing does NOT mean
  the task passes.** (Observed: a trial whose engine rendered the correct frame —
  passing the file-exists and file-similarity checks — finalized as soon as the
  frame looked right, but its `write`-to-stdout path was a no-op, so the checker's
  stdout-banner assertion failed and the whole task scored 0 despite a perfect
  frame.) Run the checker's complete assertion set and confirm **every** one is
  green before finalizing.
* **Once the grader's FULL check passes, you are DONE — finalize immediately; do
  not over-verify into a timeout.** On these tasks the engine often comes up late
  (long build), so by then you are near the budget wall. After all grader
  assertions are green, burning the last minutes on confirmation theatre —
  rendering the output to a viewable form to "eyeball" it, dumping pixel/byte
  histograms, re-running "one more clean reproducibility pass" — adds ZERO reward
  and is how a SOLVED trial dies by timeout before it can finalize. The bar is the
  grader's own full check passing once, not your own extra polish — meet it, then
  stop.
