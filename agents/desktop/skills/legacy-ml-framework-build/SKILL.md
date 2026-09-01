---
name: legacy-ml-framework-build
description: "Building an older ML / computer-vision / scientific C++ framework from source and running its bundled example end-to-end (configure & compile → fetch dataset → convert to the framework's DB/format → train → evaluate). Use when the deliverable is a real run of a legacy framework's own example pipeline, not writing model math yourself (see ml-inference-from-scratch) and not generic single-binary C builds (see c-systems-build)."
---
# Scope
Old research frameworks (vintage C++/CUDA codebases with bundled `examples/`)
were written against toolchains and download mirrors that have since moved on.
Getting one to *build* is only half the job; the graded artifact usually comes
from running its full example pipeline — data prep, format conversion, training,
evaluation — each stage of which has its own legacy landmine. Treat this as a
pipeline with five independent failure points, not "a build".

# Decide the build path before configuring — and own its consequences
* These frameworks usually ship BOTH a hand-written Makefile config and a CMake
  path. Pick the one the project's own docs/example scripts assume; the example
  helper scripts often hard-code `./build/tools/...` or a specific output layout,
  and a different build system silently puts binaries elsewhere. Matching the
  canonical path means fewer "binary not found" surprises downstream.
* Cap parallelism to the container's real limits (cgroup memory, not host RAM).
  A full framework build is long — budget for it explicitly and start it early.

# The biggest trap: disabling an optional dependency breaks a REQUIRED tool
* It is tempting to switch off an optional feature (an image lib, a GPU backend,
  a video/codec dep) "to simplify the build". Before you do, enumerate which
  bundled tools you will actually need to run downstream, and check whether any
  of them is **conditionally compiled** behind that feature.
* Vintage C++ guards whole tool bodies behind `#ifdef USE_<FEATURE>`. With the
  feature off, the tool still *compiles to a binary* but is a stub / aborts at
  runtime — and you only discover this minutes later when the example pipeline
  calls it. A clean build is NOT proof the pipeline works.
* Resolution order when a needed tool turns out to be gated behind a feature you
  disabled — cheapest first, do NOT default to the last one under time pressure:
  1. Re-enable the feature and pay the known, bounded compile-fix cost (below).
  2. **Sidestep the tool entirely**: most of these helper tools do something
     small and well-specified (compute a mean image, convert a dataset, dump a
     stat). Re-implement that one step in Python against the framework's own
     serialization (generate the message bindings from its `.proto`/schema with
     the matching compiler, read the raw data, write the expected output file).
     This is almost always faster and safer than patching framework C++.
  3. Hand-patch the framework's C++ to remove the guard — last resort. Editing
     generated/legacy source late in the run is how a nearly-finished task times
     out mid-edit. Only do this if 1 and 2 are genuinely unavailable.

# Legacy C++ vs a modern toolchain: expect a few known API drifts
* Old code calls library APIs whose signatures changed. The classic pattern is a
  serialization-library call that dropped/added an argument, or vision-library
  enum/constant names that were renamed or moved into a namespace across a major
  version. Fix at the call site, minimally — change the one signature/constant,
  don't refactor.
* When a build dies, read the FIRST real compiler error, fix exactly that, and
  resume the SAME build (don't `make clean` and pay full rebuild cost again for
  a one-line patch). Incremental rebuild after a small source edit is fine.
* Header/lib discovery on modern distros often needs explicit include/library
  dirs (e.g. versioned serial-HDF5 layouts, multi-version vision libs). Set them
  in the build config rather than fighting per-file include errors.

# Dataset acquisition: canonical URLs in old scripts are frequently dead
* The bundled `get_*.sh` download scripts point at academic mirrors that may be
  slow, rate-limited, or gone. Do NOT assume the helper script will just work.
* Have a fallback ladder ready and try them in order: the original URL → known
  community/cloud mirrors → a dataset hub (often in a *different* serialization,
  e.g. a columnar/array format) which you then convert yourself into the exact
  binary layout the framework's converter expects. Probe a candidate with a
  HEAD/`file` check before committing a long download.
* If you must convert from an alternate format, read the framework's own
  converter source to learn the EXACT on-disk record layout (header bytes, label
  placement, channel/row order, count per shard) and reproduce it byte-for-byte;
  a near-miss layout produces a corrupt DB that only fails at train time.

# Run long stages as blocking calls, not background + poll loops
* Compilation and training are minutes-long. Backgrounding them (`nohup … &`)
  and then issuing a stream of `sleep; tail` "is it done yet?" turns burns your
  step/turn budget — each poll is a full round-trip that buys nothing.
* Prefer ONE foreground command with a generous timeout that runs the stage to
  completion and tees its output to the log file the task wants. Reserve polling
  for the rare case where you genuinely must interleave other work.
* Pin thread counts for the math backend (e.g. `*_NUM_THREADS=1`) on tiny CPU
  boxes so training doesn't thrash; and make the log capture deterministic
  (`tee` the combined stdout+stderr) since graders often parse that log.

# Match the example's config contract literally
* Example pipelines are driven by solver/config files with defaults meant for a
  full run. When the task specifies a smaller/exact run, edit EVERY coupled knob,
  not just the obvious one: iteration cap, snapshot interval, test interval, and
  the compute-mode switch must all agree, or you get the wrong number of output
  artifacts or a mode mismatch. Read the config end-to-end before editing.
* The model/checkpoint filename these frameworks emit usually encodes the
  iteration count via the snapshot setting — so the artifact's name is a
  consequence of the config you set, not something to rename by hand. Get the
  config right and the correctly-named artifact falls out.

# End-game triage (carries the most weight on long builds)
* Re-derive the clock budget mentally: a from-source build + dataset prep +
  training can consume most of a medium task's wall time. Once the heavy artifact
  (the build) exists, protect it — do not open a fresh, open-ended C++ refactor
  with little time left.
* If a small auxiliary step fails late, take the CHEAPEST path to the graded
  artifact (script around the broken helper) rather than the cleanest engineering
  fix. A pipeline that finishes via a Python shim beats an elegant C++ patch that
  times out at 99%.
* Before finalizing, confirm the pipeline actually produced the named output
  files at the exact paths the task names, and that the captured training/eval
  log contains the metric lines a grader would parse — a built framework with no
  completed run scores zero. (See _universal for reproducing the grader's check.)
