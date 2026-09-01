---
name: distributed-training-parallelism
description: "Implementing distributed/parallel TRAINING of an existing PyTorch/HF model: pipeline parallelism (AFAB/1F1B microbatch schedules), tensor/sequence/data parallelism, sharding an existing nn.Module across ranks, P2P stage communication, and matching reference forward/backward activations. Use when the task hands you a constructed model (e.g. LlamaForCausalLM) and asks you to parallelize its train step — NOT for writing a model's math from raw weights (use ml-inference-from-scratch for that)."
---
### When this skill applies
You are GIVEN a fully-constructed model object (e.g. `LlamaForCausalLM`) and an
initialized process group, and asked to run a parallel train step. The reference
is the *same* model run normally; a grader compares your per-rank forward/backward
activations (often via hooks it installs — so you usually must NOT add hooks).
This is an *integration* task (drive the existing module correctly across ranks),
NOT a from-scratch reimplementation. Relates to [[ml-inference-from-scratch]].

### Reuse the model's own forward — do not hand-reimplement layer internals
This is the single highest-leverage decision and the most common failure.
*   **Shard the module, then call its official `forward`.** To put a stage's
    layers on a rank, slice the existing layer container and neutralize the
    sub-modules this stage must skip, then call the model's normal forward:
    *   `base = model.model` (the inner decoder); `base.layers = nn.ModuleList(list(base.layers[start:end]))` for this rank's contiguous slice.
    *   Non-first stage: replace the token embedding with `nn.Identity()` so it accepts hidden states; non-last stage: replace the final norm with `nn.Identity()` so it doesn't normalize mid-pipeline. The LM head / loss lives only on the last stage.
    *   First stage calls `base(input_ids=...)`; later stages call `base(inputs_embeds=hidden, use_cache=False, return_dict=True)` and read `.last_hidden_state`.
*   **Do NOT call framework-internal helpers like `create_causal_mask`, rotary-embedding builders, or attention-mask factories yourself.** Their signatures and keyword names drift across library versions, and a single wrong keyword crashes the whole run. Let the model's `forward` build masks/positions/rotary internally — that is exactly what makes the activations match the reference. If you find yourself importing a private mask/rotary function, stop: you are rebuilding what the model already does correctly.
*   Only hand-roll a layer's internals when the task explicitly forbids using the model's forward, or there is no constructed model to call.

### Tensor-parallel linear layers (Column / Row parallel) — Megatron split
For `ColumnParallelLinear` / `RowParallelLinear` that shard a `master_weight`
across ranks, follow the standard Megatron split exactly:
*   **Column parallel splits the weight by OUTPUT rows (dim 0):** each rank keeps `master_weight[rank*local_out:(rank+1)*local_out, :]` where `local_out = out_features // world_size`; bias is SHARDED the same way (`local_out` per rank). Forward: `F.linear(x, weight, bias)` on the FULL input `x`, then **all_gather** the local outputs and concat along the last (feature) dim. (Backward of the gather = split the grad to this rank's slice.)
*   **Row parallel splits the weight by INPUT columns (dim 1):** each rank keeps `master_weight[:, rank*local_in:(rank+1)*local_in]` where `local_in = in_features // world_size`; bias is FULL (replicated) on every rank. Forward: `F.linear(x, weight, None)` then **all_reduce(SUM)** the partial outputs, and add the full bias AFTER the reduce (never before — bias must be added once, not summed world_size times).
*   **The #1 RowParallel bug: do NOT slice the input inside forward.** The input fed to RowParallelLinear is already this rank's input shard (or the column-parallel layer upstream produced the matching shard), so `F.linear(x, self.weight)` must use `x` directly — manually doing `x[..., start:end]` causes a shape mismatch (`F.linear` shape error) because `self.weight` is already `[out, local_in]` and `x`'s last dim already matches `local_in`. Match the weight's input dim to the input you actually receive; don't re-shard it.
*   **Wrap the collectives in custom `torch.autograd.Function`s for correct gradients** (the grader checks gradients too): the row-parallel all_reduce is "reduce in forward, IDENTITY in backward"; the column-parallel all_gather is "gather in forward, SPLIT (take this rank's slice) in backward". Plain `dist.all_reduce`/`all_gather` calls don't define backward, so a grader that checks grads will fail without these wrappers.
*   **Handle `world_size == 1`** as a real path (no collective; return the local result) since graders test it.

### Microbatch schedules (AFAB / 1F1B)
*   **AFAB = all-forward-all-backward:** run forward for *every* microbatch first, caching each microbatch's stage-input tensor and stage-output tensor in order; then run backward for every microbatch in the same order. Keep two parallel lists (inputs, outputs) and pop them FIFO in the backward phase.
*   **Scale the loss by the microbatch count** if the task says the microbatches together form one batch (`loss = cross_entropy(...) / num_microbatches`), so the accumulated gradient equals the full-batch gradient.
*   For 1F1B, interleave instead, but the per-microbatch send/recv/backward primitives are identical to AFAB.

### Stage-to-stage communication
*   **Use the P2P primitive the task names** (`torch.distributed.P2POp` + `dist.batch_isend_irecv`, then `req.wait()` on every returned handle). Build the op list conditionally on whether a prev/next rank exists.
*   **Pre-allocate the receive buffer with the exact shape and as a leaf.** Forward hidden states between stages are `[microbatch, seq_len, hidden_size]`; backward grads use the same shape. A receive tensor that must carry gradient is `torch.empty(shape, device=device, dtype=dtype, requires_grad=True)` allocated *before* the irecv. Send tensors should be `.contiguous()`.
*   **Connect the autograd graph across the cut:** non-last stage does `torch.autograd.backward(stage_output, grad_tensors=received_grad)`; the gradient to send upstream is the received input tensor's `.grad` (call `.retain_grad()` on it after forward so it survives). Last stage just backprops the scaled loss.

### Correctness discipline for activation-matching graders
*   **Cast at every boundary.** Move inputs, hidden states, and gradients to the given `device` and `dtype` at each stage edge; silent dtype/device drift is a top cause of "close but not matching" activation diffs.
*   **Handle `world_size == 1` as a real case.** Many graders test world_size 1 and 2; with one rank there is no P2P — the whole forward+backward must still run end-to-end on the single stage (don't gate the loss/backward behind `rank != 0` etc.).
*   **Partition layers in a roughly balanced, contiguous way** (`num_layers // world_size` with the remainder spread over the first ranks). Graders often check each rank runs a reasonable number of layers, so don't dump everything on one rank.
*   **Respect "no hooks in your implementation"** when stated — the grader installs its own hooks to read activations, and yours would collide or be flagged. Verify the module's forward gives matching activations by structure, not by instrumenting it.
*   **Make it idempotent.** A train step may be called more than once; guard one-time module surgery (layer slicing, Identity swaps) with a flag so re-entry doesn't re-slice an already-sliced model.
