---
name: c-systems-build
description: "Getting a C/C++/Rust/OCaml BUILD to succeed when compiling from source is the deliverable: fixing broken builds and compiler/linker errors, toolchain/dependency bootstrap, linker-flag and link-order problems, polyglot sources, and memory/heap bugs in code you are building. Use when the goal is 'make this source compile / fix this build / get this program to build and run'. NOT for this — do NOT pick this skill — when a fixed, prebuilt host/VM/loader is PROVIDED that you must not modify and your job is to produce the artifact IT runs and reproduce its reference output (compiling is then just one minor sub-step, and the real task is conforming to that host's contract and reconstructing its output): use [[host-conformant-offline-reconstruct]] instead. And if you must WRITE the emulator/VM/interpreter itself, use [[workload-driven-emulator-fidelity]]."
---
# Environment & Toolchain Bootstrapping
* Probe for standard utilities before use; minimal containers often lack `file`, `xxd`, or `git`. Fall back to `od`, `readelf`, `strings`, or Python scripts.
* On modern Debian systems, enable source packages by modifying `deb822` formatted files in `/etc/apt/sources.list.d/` to include `deb-src` before running `apt-get source`.
* Disable sandboxing in package managers when running inside unprivileged containers to prevent namespace/bubblewrap failures.
* Cap build parallelism (`make -j`) based on container cgroup memory limits (`/sys/fs/cgroup/memory.max`), not host RAM, to prevent silent OOM kills.
* **Install heavy dependencies from the distro's PRE-BUILT packages (apt), not by source-compiling them (opam/pip-from-source/build-from-git).** The biggest budget sink on a big-build task is recompiling dependencies the OS already packages: e.g. for CompCert/Coq, `apt-get install -y coq ocaml-nox ocaml-findlib menhir libcoq-flocq libcoq-menhirlib libmenhir-ocaml-dev` lands the Coq toolchain + heavy proof libraries (Flocq, MenhirLib) in seconds, whereas `opam install` rebuilds OCaml/Coq packages and can eat the entire episode budget before the real build even starts. Then point the project's configure at those system libraries (`./configure -ignore-coq-version -use-external-Flocq -use-external-MenhirLib <target>`) so it does NOT rebuild them. General rule: prefer `apt-cache search`/`apt-get install` for any large library a build needs, and use the build's "use-external/system" flags to skip recompiling vendored copies.
* **A long build (CompCert/Coq, LLVM, a kernel — tens of minutes) must be ONE foreground command that runs to completion, not a background job you poll across dozens of turns.** The decisive failure split on these tasks: the passing run does `make -jN 2>&1 | tee build.log; echo DONE_$?` and waits it out in a single turn; the failing run launches make in the background and burns 100+ turns on short "wait 30s / poll" steps, never finishing before the step/episode cap (each idle poll is a wasted turn, doubly so on a slow LLM endpoint). So: run the build in the foreground with a completion marker and a generous duration; use `-jN` (N = available cores, memory-capped) so it actually finishes in one wait; do NOT chunk the wait into many tiny polls. Only fall back to background+poll if the harness truly cannot block long enough — and then poll with LONG intervals, not 10–50s ones.
* Build incrementally where the build system allows resuming (`make` re-entry after a fixed error) so a single failed file doesn't force a full multi-ten-minute rebuild.

# Working discipline for build tasks — probe early, commit early
* A clean diagnosis is NOT progress. The failure mode that wastes whole budgets
  on a hard build/cross-compile task is reading the provided sources, runner, or
  VM/emulator end-to-end before producing anything. You can understand the entire
  ABI and still score zero if you never wrote a file. Reading many turns in a row
  without compiling something is a failure signal — switch to building.
* Validate the target's contract EMPIRICALLY with the smallest possible artifact
  before porting the real program. Do not reverse-engineer a large interpreter/VM
  file line-by-line to infer its interface; instead write a ~15-line program that
  exercises the contract (a hello-world that makes one syscall / prints one line)
  and run it through the PROVIDED runner. One successful round-trip pins the entry
  symbol, syscall numbers, register ABI, and load address faster and more reliably
  than any amount of reading. Only then scale up to the full source.
* Commit to an implementation early and reserve most of the budget for the
  write → build → read-first-error → fix → rebuild loop, which is where real
  build tasks are actually won. Skim the provided runner only enough to write the
  probe; let the probe's behaviour teach you the rest.

# Legacy & Custom C/C++ Builds
* Compile pre-ANSI/K&R C code on modern GCC using `-std=gnu89 -fcommon -fno-strict-aliasing -w` to restore legacy semantics and implicit declarations.
* Stage all sources, headers, and platform shims into a single flat directory if legacy Makefiles lack `VPATH` support.
* Override Makefile variables (`CFLAGS`, `LDFLAGS`, `LIB`) directly via the command line to strip unwanted dependencies (e.g., GUI libraries) without editing the Makefile.
* Stub deprecated or removed libc symbols with no-ops rather than attempting to reimplement legacy OS semantics.
* Force rebuilds after patching by deleting stale object files or `touch`ing the source; do not trust legacy Makefiles to track header or configuration changes.
* Always re-run `./configure` whenever toolchain pins, environment variables, or dependencies change to keep generated Makefiles consistent.
* When a build fails, read the FIRST real compiler/linker error and fix exactly
  that, then rebuild the same tree — don't `make clean` and pay a full rebuild for
  a one-line patch, and don't scroll to the last error (it's usually a cascade).

# Cross-Compilation & Freestanding Targets
* Match the target ISA exactly. If the target VM/emulator lacks an FPU, compile with `-msoft-float` and link compiler-rt/libgcc math helpers.
* **Pick the freestanding-shim route over the system-libc route, and commit to it.** For a custom VM/emulator target there are two paths: (A) pure freestanding — `-nostdlib -nostartfiles -nostdinc`, supply your own minimal headers + a small libc shim wired to the VM's syscall ABI; or (B) install the distro's cross libc-dev (e.g. `libc6-dev-<arch>-cross`) and compile against the system headers. Route B looks easier but drags you into toolchain-config quicksand: missing variant headers (e.g. `stubs-o32_soft.h` for soft-float MIPS), `-mfp32`/ABI flag mismatches, and a glibc startup that still needs dozens of real libc functions linked. For a small VM with a custom loader, **route A is almost always faster to converge** — you control exactly which symbols exist and never fight the distro's float/ABI variants. So: do NOT `apt-get install` the target libc-dev and try to stitch system headers together when the freestanding path is available. If you catch yourself debugging missing soft-float stub headers or distro ABI-variant files, that is the signal you are on route B — back out and go pure freestanding with your own shim. Choose the route in the first few steps and don't oscillate between them mid-build.
* Identify the startup contract before writing the libc. A custom loader often has
  no crt0/dynamic linker and jumps straight to a named entry symbol (e.g. `main`)
  rather than `_start`. Build with `-nostdlib -nostartfiles` and set the entry
  explicitly (`-Wl,-e<symbol>`) and the load/text address the loader expects
  (`-Wl,-Ttext=…` or a small linker script); add a tiny startup shim that calls
  the entry and then halts if the loader returns to nowhere.
* When authoring a freestanding libc for a custom VM, map standard I/O directly to the VM's specific syscall ABI and numbers; do not pull in glibc/musl headers.
* When porting a LARGE program (e.g. a game) to a freestanding target, do NOT try to compile the whole thing and then chase `undefined reference` errors one symbol at a time — that turns into an endless add-one-function/rebuild loop (sscanf, then snprintf, then atof, then mkdir, …) that never converges and burns the whole budget. Instead:
  - **Get the link to SUCCEED first, then make symbols correct.** Collect ALL the missing symbols at once: from the linker output (`-Wl,--unresolved-symbols=ignore-all` to see them all in one pass, or `nm -u` on the objects), and provide a stub for every one in a single batch — empty/`return 0`/`abort()` bodies are fine to start. Once it LINKS and runs the minimal path, replace only the stubs that are actually exercised with real implementations, driven by what the program does wrong at runtime, not by the linker.
  - **Grow from a minimal running skeleton, never attack the full build cold.** Build a tiny hello-world for the VM ABI first (you already verified the contract that way); then link the real program against your stub libc so it RUNS (even if output is wrong); then fix behavior incrementally against the actual runner the task provides, one observed defect at a time. A program that links + runs + prints garbage is far closer to done — and far more debuggable — than one that has never linked.
  - Prefer pulling proven implementations (libgcc/compiler-rt for math/64-bit ops, a small public-domain printf) over hand-writing each libc function; reserve hand-written shims for the I/O/syscall boundary.
* Most "impossible" includes in a portable program (SDL, platform/OS headers,
  GUI/codec libs) sit inside `#ifdef` blocks for backends you will never define.
  Confirm they are macro-guarded and simply leave those macros undefined — do NOT
  reimplement those subsystems. Reserve real shims for the handful of headers the
  active code path truly needs.
* Analyze the custom loader's memory model. If it ignores `PT_LOAD` segments or lacks a dynamic linker, ensure the build produces a statically linked, flat executable.
* Verify linked dependencies using `readelf -d` or `ldd` to guarantee unwanted shared libraries are excluded; confirm the ELF machine type/endianness with `readelf -h` matches what the VM decodes.

# Polyglots & Cross-Language Parity
* Structure C/Python polyglots with a top sandwich of `#if 0`, `"""`, `#endif` to hide C from Python, and close the docstring before the Python logic.
* **For a Rust + C/C++ polyglot, exploit that Rust has NESTED block comments while C/C++ has the `#if 0`/`#endif` preprocessor.** The proven sandwich: open with `/* /* */` then `#if 0` then `*/` — Rust sees a nested block comment that opens and (via the inner `/*`) stays balanced, so the C++ side is hidden inside a Rust comment; C/C++ sees the first `/* */` as a closed comment and then `#if 0` skipping everything until `#endif`. Put the Rust code next (C++ is in the `#if 0` skip), then `/* /* */` + `#endif` to flip: re-enter a Rust comment AND end the C++ skip. Put the C/C++ code (with `#include`s) next, and close the file with `// */` (a C++ line comment that also closes the outstanding Rust block comment). Net: `rustc main.rs` compiles only the Rust half, `g++ -x c++ main.rs` compiles only the C/C++ half.
* **Build and run the file under BOTH compilers/toolchains before finishing** — a polyglot that compiles under one language but errors under the other scores zero; test `rustc main.rs && ./main N` AND `g++ -x c++ main.rs -o m && ./m N` and diff their outputs on a few inputs.
* Ignore benign C compiler warnings about missing quotes inside skipped `#if 0` preprocessor blocks.
* Disable Python's integer string-conversion length limits (`sys.set_int_max_str_digits(0)`) when outputting large computed numbers.
* Implement custom bignum logic in C when outputs exceed 64-bit bounds; native C types will silently overflow and diverge from Python's arbitrary precision.

# Debugging & State Management
* Distinguish true memory leaks from acceptable one-time standard library allocations (e.g., C++ exception pools) when auditing with Valgrind.
* Clean up all scratch files, test binaries, and temporary scripts before final validation to ensure strict workspace cleanliness checks pass.
