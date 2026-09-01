---
name: compressor-from-decompressor
description: "Write a compressor/encoder that exactly inverts a GIVEN decompressor (arithmetic/range coder + LZ77 back-references) to hit a byte budget. Use when a task hands you a decomp source (C/Python/Rust) reading stdin->stdout and asks for a compressed file that decodes to a target under a size cap."
---
# Writing a compressor to match a provided decompressor

You are given a *decompressor* and must produce a compressed blob that, fed
through it, reproduces a target file under a hard byte budget. The decompressor
IS the spec. Do not invent a format — reverse the one in front of you and build
its exact inverse. These tasks are usually an **adaptive binary range/arithmetic
coder driving an LZ77 token stream**; the recipe below is for that family, but
the discipline (mirror the decoder bit-for-bit, then verify against the real
binary) applies to any "write the encoder" task.

## Step 1 — read the decoder and name every piece
Read the whole decomp source first. Pin down, by reading the code (constants are
usually literals like `OFF1`, `OFF2`, `LITSIZE`, `radix`, `INTOFF` — use the
ACTUAL values from THIS file, never assume):
- **Byte<->digit mapping & normalization.** A range coder pulls an input byte
  only when `range < radix` (renormalization), often as `fraction += getchar()-1`
  with `radix=255`. That `-1` means output bytes are `digit+1` in `1..255` (byte
  0 is avoided — it's EOF/NUL). Your encoder MUST emit `digit+1` and renormalize
  on the identical `range < radix` condition, or the streams desync.
- **The bit model.** Adaptive binary coder: per-context counts `c0,c1` with
  `split = range * (c0+1) / (c0+c1+2)`; `bit = fraction >= split`; update
  `range` to `split` (bit 0) or `range-split` (bit 1); then `counts[bit]++`.
  Copy this formula EXACTLY, including integer-division truncation order.
- **The integer code.** A `get_integer(off, ctx)` typically reads a unary prefix
  of 0-bits terminated by a 1 (each under context `ctx_base+position`), then
  `length` magnitude bits under the base context, returning
  `(1<<length | bits) - (1<<off)`. Note any `ctx *= 99` (or similar) — context
  numbers are part of the contract; mirror the multiplier and the per-call ctx.
- **The token grammar (main loop).** Usually: header integer = number of
  tokens/output length; then per token a flag bit selects **match** (offset =
  `get_integer(OFF1,ctx)+1`, length = `get_integer(OFF2,ctx)+1`, copy from
  `pos-offset-1`) vs **literal** (optional sign bit, then magnitude integer).
  Write down each context id and bias (`+1`, `-(1<<off)`, `NN`) — off-by-one
  here is the #1 cause of a stream that decodes to garbage.

## Step 2 — build the encoder as the exact inverse
- Re-implement `encode_bit(bit, ctx)` with the SAME split formula and SAME
  adaptive counts. The clean approach: keep `low, range, k` (k = digits emitted);
  on renorm do `low *= radix; range *= radix; k += 1`; then for bit 0 set
  `range = split`, for bit 1 set `low += split; range -= split`. At the end
  serialize `low` into `k` base-`radix` digits (most-significant first) and emit
  each as `digit+1`. This lands `low` inside the final interval, which is all the
  decoder's `fraction >= split` comparisons need — no carry propagation required.
- Mirror `encode_int` to match `get_integer` byte-for-byte: emit the unary prefix
  bits under `ctx_base+i`, the terminator 1, then the magnitude bits under
  `ctx_base`. Sanity-check by writing a pure-Python decoder twin and asserting
  `encode` then `decode` round-trips on random bit streams BEFORE touching real
  data.
- Guard the coder: if `split <= 0` or `split >= range` you are about to emit an
  impossible bit (interval collapsed) — raise loudly with the index/context so
  you catch a model mismatch instead of silently shipping a broken blob.

## Step 3 — tokenize to fit the budget
- LZ77 over the target: greedy (or lazy) longest-match using a hash of short
  (3-byte) prefixes -> list of prior positions. Allow overlapping copies
  (`src` may be within the just-emitted region — that's how runs compress).
- Respect the decoder's limits: min match length, offset/length field widths
  implied by `OFF1/OFF2` (a match only pays off when it's cheaper than literals).
- Sweep the knob (e.g. minimum match length 2..9), encode each, and keep the
  smallest output that still decodes. Verify token simulation reproduces the
  target before encoding. If you're over budget, a stronger DP/optimal-parse
  tokenizer (cost = bits each token actually costs under the adaptive model)
  squeezes out the last bytes — but try greedy first; it often already fits.

## Step 4 — verify against the REAL binary, not just your model
- A passing Python round-trip is necessary but NOT sufficient. The grader
  recompiles the provided source (`gcc -o decomp2 decomp.c`) and runs
  `cat data.comp | ./decomp2`. Always do the same:
  `cat /app/data.comp | /app/decomp | cmp - /app/data.txt && echo OK`.
- Match the output contract exactly. If the decoder ends with `printf("%s", buf)`
  the output is a NUL-terminated string: it stops at the first `\0` and emits no
  trailing newline. Encode exactly the right token/length count, watch for an
  embedded NUL in the data, and don't add or drop a trailing byte.
- Write the deliverable to the EXACT path named (`/app/data.comp`), confirm
  `wc -c` is within the cap, and re-run the real-binary check from that path
  before finishing. See [[_universal]] on contract/path fidelity and
  [[binary-reverse-engineering]] for reading the source/format.

## Failure modes that score 0
- Encoder renorm condition or split rounding differs from the decoder by one →
  decodes to garbage after the first few bytes. Round-trip-test the bit layer in
  isolation first.
- Wrong context ids / integer bias → header or first token mis-decodes.
- Emitting a `0` byte (forgot the `digit+1` mapping) → decoder reads EOF early.
- Verified only the Python twin, never `cat data.comp | /app/decomp`.
- Over budget because tokenization wasn't swept/optimized — iterate, don't ship
  the first size.
