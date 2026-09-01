---
name: image-segmentation
description: "Refining or producing object segmentation masks with a promptable segmenter (SAM / MobileSAM) on CPU: bringing up the runtime, prompting per object from seed boxes/points, converting masks to polygon contours, and post-processing for exact-zero overlap, single connected component, in-bounds masks, then writing contour coordinates in the grader's exact schema. Covers the CPU-dependency bring-up traps (CUDA wheel disk blowout, hidden timm dep, libGL), the low-res-logits shape crash, deterministic claim-map overlap resolution, the repr-vs-join coordinate serialization trap, file-or-directory output paths, and end-to-end full-input verification. Use for cell/object segmentation tasks that refine seed annotations into non-overlapping polylines or masks."
---

# Promptable-segmenter mask refinement (abstract)

This class: you are given seed annotations (boxes / rough masks / polylines) and
must refine them into clean masks or polygon contours using a promptable
segmenter — typically **SAM or its distilled variant MobileSAM** — running on
**CPU**, then write the result back in a strict schema. The grader runs your
script on a hidden input and checks geometry (real polylines, in-bounds,
single-component, **zero overlap**) and coordinate **type**.

Match signals: `convert_masks.py` / a script that takes weights+image+csv+output
args; "MobileSAM" / `mobile_sam` / `ChaoningZhang/MobileSAM` / "Segment Anything";
"convert ... to polylines", "no overlap between masks", "each cell / object must
have only one contiguous mask", `coords_x`/`coords_y` columns, "must run on CPU /
no GPU", "do not modify the model source". Match by *shape*, not task name.

## The five things that actually decide pass/fail

These are mined from real failing trajectories on this class. Four of five
failures were **environment bring-up** or **output serialization** — not
segmentation quality. Get these right and the geometry is the easy part.

### 1. Bring up the CPU runtime and PROVE it imports — before writing the script
The most common dead end: the script is never able to run because deps aren't
installed, so no output file is produced and *every* downstream test fails with
`FileNotFoundError` / non-zero exit.
*   Install torch/torchvision from the **CPU index**
    (`--extra-index-url https://download.pytorch.org/whl/cpu`, e.g.
    `torch==...+cpu`). **Never** let pip pull the default CUDA wheel — it drags
    ~1 GB of `nvidia-*` packages and dies with `No space left on device`.
*   Install the segmenter's **hidden deps**. MobileSAM imports `timm` at module
    load (`from timm.models.layers import ...`) — without it you get
    `ModuleNotFoundError: No module named 'timm'` the instant you `import
    mobile_sam`. Install the package itself (e.g.
    `pip install git+https://github.com/ChaoningZhang/MobileSAM.git` or the
    declared wheel) — do not assume `import mobile_sam` works out of the box.
*   `import cv2` needs `libGL.so.1`: `apt-get install -y libgl1` (and `git`,
    `curl` if you fetch weights).
*   **Verification gate:** run a one-liner
    `python -c "import torch, cv2, timm, mobile_sam; print('ok')"` and confirm it
    prints before you write a single line of `convert_masks.py`. If it errors,
    fix deps first. Do not start coding against an unproven runtime.

### 2. Write coordinate lists as a real list literal — `repr` / `json`, never `join`
This single line turned an otherwise-perfect 8/9 solution into reward 0, twice.
The grader does `ast.literal_eval(value)` on each coord cell and asserts the
result `isinstance(..., list)`.
*   `",".join(str(v) for v in xs)` produces `"72, 72, 80"`. `ast.literal_eval`
    parses a bare comma-separated string as a **tuple** → assertion fails.
*   Write `repr(list_of_ints)` → `"[72, 72, 80]"`, or `json.dumps(list)`. Both
    round-trip through `ast.literal_eval` to a Python `list`. Use plain Python
    `int` elements (call `.tolist()` / `int(...)`, not numpy scalars), and write
    with `to_csv(..., index=False)`.
*   Self-check before submitting: `ast.literal_eval(df.loc[0,'coords_x'])` must be
    a `list` whose items are all `int`/`float`.

### 3. Overlap must be EXACTLY zero — resolve with a deterministic claim-map
Overlap checks on this class are strict (intersection / area < 1e-3, effectively
0). "Roughly disjoint" fails: one trial reported `max overlap count: 2`.
*   Build an integer **claim-map** the size of the image, initialized to -1.
*   Process objects in **descending segmenter-score order**. For each object take
    its boolean mask, intersect with the still-free area (`claim == -1`), keep the
    **largest connected component** of that free region (so it stays contiguous
    after pixels are taken away), then stamp those pixels with the object's index.
*   First (highest-score) claimer wins every contested pixel; later objects only
    get unclaimed pixels. This guarantees zero shared pixels *and* one component
    each, in one pass.
*   Verify by rasterizing every final polygon with `cv2.fillPoly` into an int
    accumulator and asserting `accumulator.max() <= 1`.

### 4. Do per-pixel math on full-res BOOLEAN masks, not raw logits
A promptable segmenter's `predict()` often returns **low-resolution logits**
(e.g. a fixed 256×256 internal size) alongside the upsampled boolean `masks`.
Combining raw logits with a full-image H×W canvas raises
`operands could not be broadcast together with shapes (256,256) (H,W)` and the
script dies before writing output. Always run overlap/claim/contour logic on the
**upsampled boolean `masks[k]`** at full image resolution; if you must touch
logits, resize them to H×W first.

### 5. Treat `output_path` as possibly a file OR a directory
The arg name is ambiguous and graders differ. If it ends in `.csv` (or has a
`.csv` suffix), write that file; otherwise `os.makedirs` it and write a `.csv`
inside. Always `os.makedirs(os.path.dirname(path), exist_ok=True)` for the
parent. Do not hardcode a filename that the grader won't read.

### 6. Match the grader's CLI signature EXACTLY — named args, not positional
The grader invokes your script with a fixed `subprocess` command; on this class
it is **named/optional args**, e.g.
`python /app/convert_masks.py --csv_path ... --rgb_path ... --output_path ... --weights_path ...`.
A script whose argparse declares **positional** args
(`ap.add_argument("weights_path")`) cannot parse `--weights_path` → argparse
prints `unrecognized arguments` and exits with **code 2**, so the grader's
`subprocess.run(check=True)` raises and *every* downstream test fails — even
though the file exists and imports fine. This is a silent, high-confidence
reward-0: the script "works" when you call it your own way.
*   Declare the args the grader uses: `ap.add_argument("--weights_path")`,
    `--output_path`, `--rgb_path`, `--csv_path` (exact spellings from the task
    text / the test file's command list). For robustness, also accept the same
    four as trailing positionals and map them in declared order, so both
    invocation styles work.
*   **Open the grader/test file and copy its literal `command = [...]` argv.** Do
    not guess the flag names or rely on the prose — read the array the test
    actually passes to `subprocess`.
*   **Self-test with that exact argv.** Running
    `python convert_masks.py /tmp/w.pt /tmp/o.csv img.png meta.csv` (positional)
    "passing" proves nothing if the grader calls it with `--flags`. Reproduce the
    grader's command verbatim — same flags, same order — before `task_complete`.
    A self-test in a different invocation style is the #1 false-positive here.

## The method (segmentation core)

1.  Load the image once; `set_image(rgb)`. Load the segmenter with the correct
    registry key / checkpoint (for MobileSAM: `vit_t`), `.eval()`, on CPU.
2.  For each object: prompt with its **seed bounding box** (or seed points),
    request multiple candidates (`multimask_output=True`).
3.  Pick the best candidate by **score weighted by containment in the prompt box**
    (e.g. `score * (0.5 + 0.5 * fraction_inside_box)`) so the mask can't run away
    from the intended object — this protects the alignment check.
4.  **Clip** the chosen mask to the prompt bbox and keep its **largest connected
    component** (`cv2.connectedComponentsWithStats`).
5.  Resolve overlaps across all objects with the score-ordered claim-map (§3).
6.  `cv2.findContours(mask, RETR_EXTERNAL, CHAIN_APPROX_SIMPLE)` → largest contour
    → close the ring (first point == last). Reject degenerate (<3-point) contours;
    fall back to the seed box/coords for that row if the segmenter yields nothing.
7.  Recompute `xmin/ymin/xmax/ymax` from the final contour; write `coords_x` /
    `coords_y` with `repr(list)` (§2). Preserve every other column and the exact
    row count and column order of the input; flip a `type` column to `polyline`
    only if it exists.

## Verify on the FULL input, on the exact bytes you submit
*   Run the finished script **end-to-end on the entire demo/input CSV**, not a
    2-row slice. A 1-rectangle + 1-polyline mini-run never exercises the
    multi-object overlap path, so a "passing" tiny test proves nothing — the
    overlap and shape bugs only surface with several colliding objects.
*   Re-run the *actual file you are about to submit* (not a version you edited
    afterward), **using the grader's exact command line** (the `--flag` argv from
    the test file, §6 — not your own positional shortcut), confirm it exits 0,
    writes the output, and that programmatically:
    every row is a >4-vertex polyline with `len(xs)==len(ys)>=3`, the rasterized
    overlap accumulator max is `<= 1`, and `ast.literal_eval(coords_x)` returns a
    `list`. Only then declare done.

## Failure-mode checklist (each one sank a real run)
- `ModuleNotFoundError: torch` / `mobile_sam` — deps not installed; prove imports first (§1).
- `ModuleNotFoundError: timm` — hidden MobileSAM dep; install it (§1).
- `No space left on device` — CUDA torch wheel; use the CPU index (§1).
- `ImportError: libGL.so.1` — `apt-get install -y libgl1` (§1).
- `coords ... is not a list (got tuple)` — used `","join`; use `repr(list)` (§2).
- `OVERLAP detected` / overlap count > 1 — incomplete resolution; use the claim-map (§3).
- `operands could not be broadcast (256,256) (H,W)` — operated on raw logits; use boolean masks (§4).
- output file missing though script "worked" — wrote to a dir/filename the grader doesn't read (§5).
- script exits `code 2` / `unrecognized arguments` under the grader — argparse used positional args but the grader passes `--flags`; declare named args and self-test with the grader's exact argv (§6).
