---
name: ml-inference-from-scratch
description: "Implementing model inference from raw checkpoints/weights: weight layout reasoning, tokenizers/BPE, numeric kernels (GELU), K/V cache, the math of a forward pass, model serving. Use when you must reconstruct or run a model's computation yourself (including in C/C++). For PARALLELIZING the training of an already-constructed PyTorch/HF model across ranks (pipeline/tensor/data parallelism, P2P stage comms), use distributed-training-parallelism instead."
---
### Checkpoint & Architecture Introspection
*   **Probe formats safely:** Do not assume standard serialization. Check for raw float32 binary dumps (e.g., file size modulo 4 == 0) before parsing as structured checkpoints. Do not waste the budget reverse-engineering a layout into a "readable" form when reading it in its native on-disk order is enough for a forward pass.
*   **Remap lexicographical layers:** Exported weights often sort layer names alphabetically (e.g., 1, 10, 11, 2). Explicitly remap these to numerical order for the forward pass.
*   **Inspect before building:** Use safe loaders (`torch.load(weights_only=True)`) to dump state_dict keys, shapes, and dtypes. Reconstruct the architecture directly from parameter names.
*   **Check weight tying:** Verify if input token embeddings are tied to the output projection head to save memory and parsing logic.

### Preprocessing & Tokenization
*   **Mirror training exactly:** Apply the exact same image normalization (e.g., scaling by `/255.0`) or text pre-tokenization (regex splits for contractions/punctuation) used during training.
*   **Reconstruct byte-encoders:** For BPE, ensure non-ASCII characters round-trip correctly by mapping raw bytes to the specific unicode ranges expected by the vocabulary.
*   **Buffer file reads:** Read large vocabulary files using buffered I/O to handle multi-byte unicode characters safely without truncation.

### C/C++ Inference Implementation
*   **Establish ground truth:** Always run a Python/PyTorch reference forward pass to capture expected logits/activations for a fixed input before porting to C/C++.
*   **Order linker flags:** When compiling with `gcc`/`g++`, place math libraries (`-lm`) *after* source files to prevent undefined reference errors for standard math functions.
*   **Match approximations:** Implement activation functions (like tanh-based GELU) using the exact mathematical approximations of the reference framework.
*   **Constraints are acceptance gates, not afterthoughts:** If the task states a hard limit (source byte-count, per-run timeout, memory cap), that limit is the FIRST grading check — a perfectly correct artifact that violates it scores zero just like a broken one. Do not adopt a "make it correct first, optimize/shrink later" plan: on a tight budget you will run out of turns mid-correctness-debugging and the over-limit artifact on disk is what gets graded. Instead:
    *   **Budget the skeleton first.** Before filling in logic, write the smallest compiling skeleton (arg parse + file open + stub output) and confirm it is already comfortably inside the size/time limit. If the skeleton alone is near the cap, your structure is wrong — rethink before adding more.
    *   **Re-check the limit after every edit.** After each write, immediately measure (`wc -c file` for a size cap) and run the grader's exact invocation under its time bound. Never let an over-limit or too-slow artifact sit on disk as "to be cleaned up later"; bring it back under the limit in the same step you noticed it.
    *   **For per-run timeouts on one CPU,** the cost is dominated by the big matmuls — keep inner loops cache-friendly (contiguous access, block/tile the GEMM), avoid recomputing per token (cache K/V), and prefer one preallocated scratch buffer over per-call allocation. A naive strided matmul over a full transformer can exceed a 60–90s bound by itself.

### Incremental Bring-Up & Debugging (the way a from-scratch impl actually converges)
A correct compact inference program is reached by writing a first version then fixing concrete bugs one at a time — NOT by emitting a finished implementation in one shot. Expect many compile→run→inspect→patch cycles, and isolate variables so each failure has a single cause:
*   **Get ONE token correct before generating many.** Decode a single greedy token first and confirm it is sane; only then extend to the full N-token loop. This separates correctness bugs (wrong logits) from performance/memory bugs (the multi-token loop being slow or OOMing) so you debug one at a time instead of both at once.
*   **Use a "varies-with-prompt" sanity signal for layout.** If different prompts produce the *same* output, the weight layout/layer order is almost certainly wrong (the model isn't really reading the prompt). Correct greedy output should change with the input and read like plausible continuations. Treat identical-across-prompts output as a layout bug, not a sampling bug.
*   **Map memory before mapping logic on big checkpoints.** A multi-hundred-MB checkpoint read into the heap can OOM-kill the process inside a memory-capped container. Prefer `mmap` of the file (or stream/seek to only the tensors a step needs) over loading the whole thing into allocated memory. If the process dies *as inference starts touching weights*, suspect memory pressure, not logic.
*   **Two layout traps beyond layer-name sorting:** (1) the on-disk order of tensors *within* a block often differs from the order the forward pass consumes them (e.g. norm params stored after attention params but needed first) — seek to each tensor by name/offset rather than reading strictly sequentially; (2) confirm endianness and that every per-block stride matches the real parameter count. Verify by checking that a known tensor's stats (mean/min/max) look right at the offset you computed.
*   **Size buffers to the real data.** Line/string buffers for vocab/merge files must fit the longest actual line; a fixed small buffer silently truncates and corrupts parsing (`strchr`/`strtok` then fail on a cut line). Check the max line length in the file, don't assume.

### Distributed Execution & Autograd
*   **Allocate P2P leaves:** Tensors receiving data over P2P that require gradients must be allocated as leaves (`torch.empty(..., requires_grad=True)`) *before* the receive operation.
*   **Reverse collectives:** Wrap distributed collectives in custom `torch.autograd.Function`s (e.g., gather forward requires scatter backward; reduce forward requires identity backward).
*   **Enforce dtype/device boundaries:** Explicitly cast tensors to the target device and dtype at every pipeline stage boundary to prevent silent activation drift.
*   **Provide single-process fallbacks:** Ensure distributed modules function correctly when `world_size == 1` and no process group is initialized.

### API Serving & Environment Resilience
*   **Bypass missing tools:** Assume `ps`, `netstat`, `git`, `file`, and the `python` alias are missing. Use `python3`, `od`, and the `/proc` filesystem (`/proc/net/tcp`, `/proc/<pid>/cmdline`) for diagnostics.
*   **Bind and poll:** Bind servers explicitly to `0.0.0.0`. Launch detached (`nohup ... &`) and poll the endpoint in a loop until it returns 200 OK before proceeding.
*   **Validate payloads strictly:** Return HTTP 400 for missing, empty, or mistyped JSON fields. Never let malformed input trigger an HTTP 500.
*   **Cache models locally:** Download and persist models/tokenizers to explicit local directories, then load from disk to guarantee offline availability.

### Optimization & Fine-Tuning
*   **Isolate fine-tuning:** Freeze all base parameters. Verify post-save checkpoints by asserting the state_dict diff contains only the tuned output head.
*   **Use closed-form solutions:** For linear heads with MSE loss on frozen features, prefer closed-form least-squares over iterative optimizers for deterministic convergence.
*   **Bucket by shape:** Pack batch inference requests into homogeneous buckets based on prompt and generation lengths to minimize padding and worst-case latency drag.
