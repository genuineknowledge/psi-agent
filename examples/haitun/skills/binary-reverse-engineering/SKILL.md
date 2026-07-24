---
name: binary-reverse-engineering
description: "Reverse-engineering binaries and recovering secrets: ELF/format probing, hash cracking, password/secret recovery, patching vulnerabilities, PARSING an ELF to extract memory/section/symbol values by virtual address (e.g. a node/python script that reads .text/.data/.rodata and emits address→value), and REPRODUCING a compiled program as equivalent C source (decompile a `mystery` binary into `mystery.c` that behaves identically, often under a size cap). Use when inspecting a compiled binary, parsing/decoding an ELF or other binary format, extracting memory values from a binary, breaking a hash, or rewriting a binary's behavior as source."
---
### Environment & Tooling Fallbacks
- Expect minimal container images missing `xxd`, `file`, or `gdb`.
- Substitute `xxd` with `od -An -tx1`, `od -c`, or `python3 -c "print(open(f,'rb').read().hex())"`.
- Substitute `file` with `head -c 4 | od -c` to check for standard magics (e.g., ELF).
- Use `grep -aob` to find byte offsets of strings or signatures in raw binaries or disk images.
- Install missing system dependencies via `apt-get` (e.g., `p7zip-full`, Perl LZMA modules) rather than fighting Python environment restrictions.

### ELF Analysis & Memory Mapping
- Prefer static analysis (`objdump`, `readelf`, `nm`, Python `struct`/`pyelftools`) over dynamic execution to bypass anti-debugging and obfuscation.
- Confirm what's actually being asked before computing anything: read the task's own checker/instructions to pin down whether it wants on-disk (pre-relocation) bytes, runtime/loaded values, symbol addresses, etc. Different tasks want different things; guessing the contract is a common failure.
- An ELF exposes the same bytes through two views; choose based on the task's conditions rather than habit:
  - **Section view** (`readelf -S`: `.text`/`.data`/`.rodata`/… each with its own `sh_addr`+`sh_offset`). Strengths: gives a precise, per-region address for every named piece of content, so each chunk maps to exactly the address its header declares — best when you need fine-grained or per-symbol/per-region address↔value fidelity, or when the expected addresses look like distinct section starts. Weakness: gone if the binary is stripped of section headers.
  - **Segment / program-header view** (`readelf -l`: `PT_LOAD` with `p_vaddr`+`p_offset`, mapping `file_offset = VA - p_vaddr + p_offset`). Strengths: reflects how the loader actually lays memory out, survives section stripping, fewer/coarser ranges. Weakness: a `PT_LOAD` lumps several sections together, so you reason about one big base+range and it's easy to slip on the base/PIE assumptions — coarser, and more error-prone when the task wants exact per-region addresses.
  - Decision: if the task needs exact addresses for individual regions/symbols and section headers are present, the section view is usually the cleaner fit; if sections are stripped or you only need the loaded image, use segments. When unsure, build both and cross-check they agree on overlapping addresses.
- Use addresses exactly as the headers report them (`sh_addr` / `p_vaddr`); don't invent or hardcode a load base, and don't treat raw file offset 0 as the lowest address (that's just the ELF magic). If a value at the lowest address comes out as the ELF magic, your offset/base mapping is wrong.
- Account for zero-fill: bytes that exist in memory but not in the file (`SHT_NOBITS`/`.bss`, or `p_memsz > p_filesz`) are zero.
- Read multi-byte values with explicit endianness and width taken from the ELF header (don't assume).
- Validate against any ground truth the task provides: if it gives example address→value pairs, treat reproducing them exactly as a hard pre-submission test, and if your output doesn't match, fix the mapping rather than submitting.
- **An "example output" in the prompt shows FORMAT, not your required addresses — do NOT hardcode a base to match its literal numbers.** A prompt like `Example output format: {"4194304": 1784774249, ...}` is illustrating the JSON shape (string-keyed integer addresses), often taken from a DIFFERENT binary (e.g. a non-PIE one based at `0x400000`). If your target is PIE/based at 0, its real addresses are the raw `sh_addr`/`p_vaddr` from ITS headers — adding `0x400000` to chase the example's first key shifts EVERY address and scores 0% against a reference that used raw vaddr. Real failure: a trial added `0x400000+p_vaddr` because the example started at 4194304 → 0.00% match, while the passing trial used raw `p_vaddr` and matched. Key off the actual header values; the example's numbers are not a constraint.
- **For "extract memory values by address" tasks, mirror the reference's region choice and word layout.** These graders typically load specific named sections (`.text`/`.data`/`.rodata`) — not all PT_LOAD content — and emit one entry per 4-byte little-endian word keyed by `section.addr + offset`. A segment/PT_LOAD walk can over- or under-cover relative to a section-based reference; when the task hints at sections (or you can read the checker), load exactly those sections, read `readUInt32LE`/`<I` words at `vaddr+i`, and output integer (not string) values. Coverage thresholds (e.g. ≥75%) mean you must hit the SAME address set, so the region selection must match.

### Archive Cracking & File Carving
- Run `*2john` scripts to extract hashes, strictly redirecting stderr to `/dev/null` to prevent warning banners from corrupting the hash file.
- Try bundled wordlists with `john` before attempting brute force. Use `--show` or delete `john.pot` when re-reading known cracks.
- Carve fragmented ZIPs manually by searching for standard signatures (LFH, CDH, EOCD). Do not rely on standard extraction tools if EOCD-relative offsets are broken.
- Use the uncompressed CRC32 field in archive headers as a deterministic oracle to validate reconstructed plaintexts.
- Extract archives into scratch directories (`/tmp`) to avoid clobbering the working environment.

### Vulnerability Remediation & Patching
- Apply the "smallest-fix principle": patch the lowest-level shared chokepoint (e.g., a normalization helper) rather than every individual call site.
- Let the failing test define the exact contract (e.g., specific exception types to raise, exact characters to reject).
- Always run the full test suite after patching a shared chokepoint to catch regressions in unrelated features.

### Reproducing a compiled binary as equivalent C source
For "write `mystery.c` that behaves identically to this compiled `/app/mystery`" tasks (often with a `<2KB gzipped` cap and a "must be fully independent / must not invoke the original" rule). The dominant failure here is NOT getting the logic wrong — it is spending the entire budget reading the disassembly and NEVER writing the source. A correct-but-unwritten `mystery.c` scores ZERO (the grader's first check is `mystery.c` exists + compiles).
- **First, observe behavior cheaply.** Run the binary, capture its exact output (and the output FILE it writes, e.g. `image.ppm`), check determinism, dimensions, and `strings`/`readelf -s` for the function names and messages. This often reveals the whole structure (e.g. a path-tracer: spheres, sky gradient, shadow, write_image) in 2-3 commands.
- **BATCH the disassembly extraction — never page it one range per turn.** In one or two commands: `objdump -d -Mintel <bin> > /tmp/dis.asm` and `objdump -s -j .rodata <bin>` (or address ranges) to FILES, then read several `sed -n` ranges per command and decode every `.rodata` float with one `struct.unpack` loop. The losing pattern (real failure: 34 turns paging `main` lines 1-60, 70-160, 120-230… across separate turns) burns the whole budget and — on a flaky endpoint — dies with nothing on disk. Aim to have constants + control flow in hand within ~5-8 turns.
- **TIME-BOX analysis and WRITE `mystery.c` EARLY from a working draft.** Once you have the scene/algorithm and the key constants, write a first complete `mystery.c`, compile with the grader's exact command (`gcc -static -o reversed mystery.c -lm`), run it, and compare its output to the original's — even if a few constants are still guesses. Iterate from a compiling, running draft; do NOT perfect your understanding of every instruction before emitting any source. Treat "exists + compiles + runs + output close" as a milestone to hit by roughly the midpoint, then refine similarity.
- **Match the exact contract:** single-precision vs double, operation order, float→byte quantization (`(int)(255.99*c)`), the exact output path and format the original writes, and the original's actual output dimensions (it may supersample). Keep the source small (inline vector math, minimal headers) and check `cat mystery.c | gzip | wc -c` against the cap. The C program must be self-contained and must NOT exec the original binary.
- See [[media-graphics]] for the same discipline applied to graphics/renderer binaries specifically.

### Precision Deliverables
- Ensure byte-exact outputs. Prevent trailing newlines using `printf '%s'` or programmatic file writes.
- Verify final output length and exact bytes using `wc -c` and `od -c` before submitting.
- When relying on OCR or transcribed hints, brute-force ambiguous characters (e.g., `0` vs `O`) against a known hash prefix rather than trusting the transcription.
