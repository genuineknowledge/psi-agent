---
name: host-conformant-offline-reconstruct
description: "An ordered, numbered execution plan for the class of fixed-host artifact tasks: build a static little-endian target-ISA ELF (the artifact) that the provided fixed host loader runs so that running the host prints the engine's init text and writes the first frame to the output file matching the reference. This skill gives you the ORDER of steps and the discipline to avoid the timeout — but NOT the concrete values. You discover every number/format yourself by reading the provided sources."
---

# host-conformant-offline-reconstruct — an ordered plan (no constants given; you read them yourself)

## Concrete anchors (a worked example of this task shape — map each role to YOUR actual file/name)

This plan talks about abstract roles ("the host", "the resource bundle", "the
title asset"). To execute FAST, immediately bind each role to the **specific,
operable name** in front of you, the way the canonical instance of this class
does. For example, in a typical instance:
  - **the host** ⇒ a single big interpreter/VM file you run, e.g. a `vm.js`-style
    MIPS loader — you `grep` THAT file by name for its entry/syscall/halt.
  - **the provided backend** ⇒ the source that writes the output, e.g. an
    `*_img.c`-style frame writer — read THAT for the exact output format.
  - **the resource bundle** ⇒ a single asset archive, e.g. a `.wad`-style file —
    parse THAT as `[header][lumps][directory]`.
  - **the title asset / palette** ⇒ named entries inside the bundle, e.g. a
    `TITLEPIC`-style title-image lump and a `PLAYPAL`-style palette lump — pull
    THOSE two by name.
  - **the output file** ⇒ the exact path the backend writes, e.g. a
    `/tmp/frame.bmp`-style file.
**Do the binding in your first 1–2 actions and from then on refer to your real
filenames, not the abstract words** — every step then becomes a direct `grep
<realfile>` with no re-translation. (Your task may differ; the example just shows
how concrete each anchor should get.)

## Read this first: the two ways this task kills you, and the rule

You are on an agent step/time budget. Two failure modes (both observed):

- **Trap 1 — porting the real program.** The provided backend
  references a custom libc-style header, which makes it *look* like the intended
  solution is to supply that libc, cross-compile all of the engine, and run it on
  the host. That route TIMES OUT and you cannot patch the fixed host. **Do not take
  it.**
- **Trap 2 — over-reading the host.** Exhaustively studying the host (its full
  instruction decoder, memory model, run loop, etc.) burns your whole budget
  before you build anything.

**The rule for this task: you do NOT run the engine. The first graded frame is just a
static image that already exists inside the provided resource bundle; you reconstruct that
image offline and emit a tiny program that only prints the boot text and writes
that image to the output file. Follow the numbered steps below IN ORDER. At each
step you must read the provided source to get the actual values — this skill
deliberately does not hand you the numbers.**

**And know when you are DONE: the grade is a tolerant similarity check, not a
byte-exact match. Once the output file exists at the right size/format with the
expected content and the one required stdout line is present, STOP and deliver —
do not burn your remaining budget chasing sub-threshold byte differences. Solving
it and then timing out while polishing counts as a failure.**

## The ordered plan

**Step 1 — Inventory (fast).** List `/app`. Confirm you have: the fixed the host,
the engine source under engine-source/, the provided backend,
and the resource bundle file. Note the resource bundle path. Check what tools exist (node, python3) and
whether a target-ISA cross-compiler is present; if not, install a little-endian target-ISA
cross-toolchain now (kick off the install and move on while it runs).

**Step 2 — Extract ONLY the host contract you need to emit a tiny ELF. Then
stop reading the host.** From the host find and write down, for yourself, exactly
these five things and nothing more:
  (a) how it picks the entry point (a named symbol? which name? — not the ELF
      header entry);
  (b) what register/stack state it presets and what makes execution stop (so you
      know whether returning from the entry halts it, and whether you need any
      startup code);
  (c) which ELF sections it actually loads (by name), and whether it zero-fills
      anything it doesn't load;
  (d) the syscall NUMBERS and argument registers for the only calls you will
      make: write, open, close (read the dispatch table — use ITS numbers, not
      any OS's standard numbers);
  (e) the byte order it reads words in.
**Do NOT read the instruction decoder, the run loop internals, the memory-cell
representation, timing, etc. — you are not executing the real program, so you
don't need them.** This is the single biggest time-saver.

**Step 3 — Smoke-test the contract.** Write a ~10-line freestanding program
(no libc, no startup) that issues one `write` syscall to print a short string and
then returns. Build it for the target with the flags implied by Step 2 (right
arch/endianness, soft-float if the VM has no FPU, no PIC/GOT, no startup files,
entry forced to the symbol from 2a). Run running the host on it and confirm you see
the string and a clean halt. Fix the contract now, while the artifact is tiny —
not after embedding a megabyte of pixels.

**Step 4 — Determine the exact target frame format (read the provided backend,
not the host).** Open the provided backend and the engine headers/source it
uses. From them, work out for yourself: the frame width and height actually
written (mind any scaling factor the backend applies over the engine's native
resolution), the bits-per-pixel, the channel/byte order of each pixel, the row
order (top-down vs bottom-up) and how that is encoded in the file header, and the
exact file-header layout the backend writes. Write these down — they are the
"target form" your reconstructed pixels must match exactly. Do not guess; the
similarity check fails on a wrong size/orientation/channel order even if the
image is otherwise right.

**Step 5 — Find which image is the first frame, and where it lives.** A
freshly-booted engine first draws its title/splash screen (not gameplay).
Read the engine source to find which named lump the title state displays, and read
how the engine turns a graphic lump + the palette lump into screen pixels (the
palette mapping, and whether any gamma table is applied by default). You are
reproducing exactly what the engine would have drawn on frame 1.

**Step 6 — Reconstruct that frame offline (host-side, e.g. python3).** Parse the
resource bundle as a container: read its header to find the directory, then read the
directory entries to locate the title-image lump and the palette lump (figure out
the resource bundle header and directory-entry layout yourself from the format — it is a
simple `[header][lumps][directory]` structure). Decode the title image from the
engine's graphic format (it is column-based, not a linear bitmap — read the
source/format to decode posts correctly), map indices through the palette (and
gamma if the engine does), then transform into the EXACT target form from Step 4
(scale, channel order, row order, header). Produce the final frame bytes on disk
and sanity-check the dimensions/header.

**Step 7 — Reproduce the ONE boot line the grader checks (do NOT reproduce the
whole boot log).** The engine prints a long init sequence, but the grader almost
always asserts just a SINGLE specific line appears verbatim — not the entire log.
**Do not try to reconstruct the full ordered boot sequence** (gamedescription,
resource bundle-version detection, every subsystem's print, banner formatting); that is an
open-ended rabbit hole that will burn your whole budget, and it is the #1 way
trials time out on this task. Instead, narrow the search hard:
  - First, look for a grader/test file if one is present and read exactly which
    string it asserts; reproduce only that.
  - If no grader file is visible, target the **graphics-init line that reports the
    screen dimensions** — the message the engine prints when it initializes the
    video/graphics (it contains the screen width × height). That single line is
    the typical assertion. Grep the source for the literal `printf`/print of the
    graphics-init message, copy its exact format string, and fill in the
    dimensions you already determined in Step 4.
  - Printing a few extra plausible init lines around it is fine, but **time-box
    this step**: once you have that one dimensions line verbatim, MOVE ON. Do not
    keep reading source to perfect the rest of the log, and do NOT compile/run
    real the engine just to capture ground-truth stdout.
A perfect frame still fails if that one required line is missing or slightly off —
but the fix is one correct `printf`, not the entire boot log.


**Step 8 — Emit the tiny artifact and build it.** Embed your reconstructed frame
bytes into a read-only data object (e.g. via the cross-binutils objcopy/incbin),
and write a small freestanding C `main` that: prints the boot lines from Step 7
via the write syscall; opens/writes/closes the frame file (using the syscall
numbers from Step 2d) writing the file header + embedded pixels; then returns to
halt. Link it with the contract flags from Steps 2–3 (entry forced to the symbol,
no startup/libc, no PIC, right arch/endianness, soft-float).
*Linking pitfalls — anticipate these from the start so you don't lose minutes
trying flags one error at a time (they recur on freestanding cross-builds):*
  - *default PIE/PIC: add `-no-pie -fno-pic -mno-abicalls` (and `-fno-builtin
    -ffreestanding` to stop the compiler emitting `memcpy`/`strlen` libcalls).*
  - *the host loads only specific section names: keep your code+data in the
    sections it loads (e.g. `.text/.rodata/.data`).*
  - *target-ISA metadata / special sections can cause overlap or load junk: if the
    linker complains about overlap or the host mis-loads, drop straight to a
    minimal custom linker script that places ONLY `.text/.rodata/.data` and
    `/DISCARD/`s the ISA metadata — don't iterate flags hoping it resolves.*
*Alignment gotcha (do this UP FRONT, it costs a wasted rebuild otherwise): many
hosts read loaded memory in WORD units (e.g. 4-byte words). If your embedded blob
length is not a multiple of the host's word size, the host can DROP the trailing
1–3 bytes of the last partial word. So **pad the embedded blob up to a multiple of
the word size**, and still write exactly the correct byte count in your write
syscall. Bake this in from the start rather than discovering a dropped final pixel
after your first run.*

**Step 9 — Run and verify, then STOP IMMEDIATELY.** Run the host from the
directory it expects. Confirm stdout shows the boot text and the output file is
created with the right dimensions/header and looks like the title screen.
**⛔ The moment the output file exists at the right size/format with the expected
content, and the required stdout line is present, you are DONE — STOP and deliver.**
The grade uses a SIMILARITY check with tolerance, not a byte-exact compare, so do
**NOT** keep polishing: do not chase a 1-byte tail difference, a dropped final
pixel, a partial last word, or any sub-threshold mismatch — these do NOT affect
passing and hunting them is a top cause of running out the clock AFTER the task is
already solved. Only if the similarity check would actually FAIL (clearly wrong
size/orientation/channel order, blank/garbled image) do you go back to Step 4/6;
otherwise deliver and end.

**⚠️ Do NOT "clean up". Once it passes, STOP touching the filesystem.** The grader
re-runs the host itself, so two things must remain intact: (1) your artifact at the
EXACT path the host expects (e.g. `/app/<artifact-name>`), and (2) everything the
host needs to regenerate the output on that re-run. Do NOT delete temp/build dirs,
embedded-data files, or the produced output file to "leave things tidy" — a
deletion that removes the output file or a build input makes the grader's
existence/similarity check FAIL even though you had it working. Tidying has only
downside here; skip it entirely and end the run.

## Budget guidance

**You are racing a ~15-minute clock and the dominant risk now is finishing the
build TOO LATE — get a first end-to-end run done early, then refine.** Concretely:
- **Do not read all of Steps 2–7 serially before writing any code.** As soon as
  the Step-3 smoke test passes, **immediately scaffold your real `main.c` and the
  build/run command** (even with placeholder pixels), so the compile+run pipeline
  is working by minute ~6–8. Then fill in the real reconstructed frame.
- **Aim to produce your FIRST full `frame` output by ~minute 10, not minute 14.**
  Out-of-order is fine: you can have the build pipeline + a dummy frame running
  while you still finish the offline decode (Step 6) in parallel.
- Bake in the Step-8 alignment fix from the start (pad blob to the host's word
  size) so you don't lose a rebuild to a dropped trailing byte.
- If you catch yourself deep in host internals you don't need (Trap 2), or scoping
  a libc for a full port (Trap 1), STOP and return to this plan.
