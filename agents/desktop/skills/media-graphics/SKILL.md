---
name: media-graphics
description: "Image/video/graphics processing: ray tracing, rendering, segmentation, video frames, gcode, physics (MuJoCo). Use when the task involves pixels, frames, 3D scenes, or visual output."
---
# Environment & Tooling
* Bypass strict package managers using `uv pip install --system --break-system-packages` for missing dependencies (e.g., OpenCV, Pillow, numpy).
* Assume standard inspection tools (`file`, `xxd`, `gdb`) are absent. Substitute with `od`, `readelf`, `nm`, `objdump`, `strings`, and Python scripts.
* Always run scripts end-to-end to catch runtime import errors; syntax checks alone are insufficient.

# Reverse Engineering Graphics Binaries
* **First, hunt for a leftover source or binary before reconstructing from pixels.** Tasks that say "reproduce this rendered image with a program" often leave the original renderer in the container even after a Dockerfile `rm orig.c` — the *compiled* `orig` binary, an `orig` ELF, a `.s`, or strings usually survive. `ls -la`, `file *`, `strings`. If a binary exists, disassembling it for exact scene constants is FAR faster and more accurate than reverse-engineering geometry/lighting from the image, which can take dozens of fitting iterations. Reconstructing from pixels alone is the fallback when no source/binary remains, not the default.
* **Batch the extraction — do NOT read the disassembly one function per command.** The classic time-sink (and a timeout death when the LLM endpoint is flaky) is dumping `objdump -d` once, then spending 15+ turns paging through it function by function. Instead, in ONE or TWO commands: dump `.rodata` (`objdump -s -j .rodata`) AND the full disassembly to a file, decode every rodata float with one `struct.unpack` loop, and slice out every function you need (`main`, `trace`, both `*_intersect`, `sky_color`, `is_in_shadow`, the writer) in a single `sed`/`awk` pass. Aim to have all scene constants + control flow in hand within ~5 turns, not 20.
* **Time-box the analysis and START WRITING the C program early.** A correct-but-unfinished reconstruction scores ZERO. Reading the binary perfectly but never emitting `image.c` is the worst outcome — it's what loses these tasks. Once you have the scene constants and the shading/intersection logic, write a first complete `image.c`, compile it with the grader's exact command, and render — even if a few constants are still guesses. Iterate from a working draft; do not perfect your understanding before producing any deliverable. Treat "compiles + runs + writes the output file" as a milestone to hit fast, then refine similarity.
* Extract scene constants directly from `.rodata` using `objdump` and decode hex to floats via Python `struct.unpack` rather than guessing numerically.
* Replicate the binary's exact arithmetic: use single-precision (`sqrtf`), preserve operation order, and match float-to-byte quantization exactly (e.g., `int(scale * c)`) to avoid off-by-one pixel errors. Note the original may render at a supersampled resolution (e.g. `800*SS x 600*SS`) and the similarity check compares whatever the original wrote — match its actual output dimensions, not the nominal target.
* Debug pixel mismatches by running `cmp -l` on outputs, mapping byte offsets to (row, col) coordinates, and disassembling the specific pixel's execution path.
* Minimize C source size by inlining vector math, omitting non-essential headers, and continuously checking the compressed size against the cap (`cat image.c | gzip | wc -c`). When a size limit is given, it exists to force an algorithmic solution — do NOT embed pixel data or large constant tables.
* **Match the scorer's invocation and output path exactly.** Re-read the verifier's contract: it may compile with specific flags (`gcc -static ... -lm`), run the binary in a `chroot`/jail with the source image removed (so the program must NOT read the original), and expect the output at a precise filename in the cwd (e.g. `reconstructed.ppm`). A program that reads the reference image, or writes the wrong path, fails regardless of pixel accuracy.

# Video Analysis & Computer Vision
* Never trust container metadata for frame counts; iterate until `cap.read()` returns False.
* Establish a robust background reference using the first frame, a running median, or MOG2 background subtraction.
* Isolate subjects using grayscale differencing, blurring, thresholding, and morphological operations (open/dilate).
* Filter false positives by enforcing plausible bounding box dimensions and temporal centroid continuity across frames.
* Detect vertical state changes (e.g., jumping) by tracking the bounding box bottom-y coordinate, smoothing the signal, and identifying sustained deviations from a grounded baseline.

# Video OCR & Text Extraction
* Install downloaders via pip to avoid HTTP 403 errors from outdated package managers. Cap downloads at 720p to optimize processing speed without losing legibility.
* Crop video frames to the exact text region using `ffmpeg` before applying OCR to reduce noise and processing time.
* Use Tesseract with appropriate page segmentation modes (e.g., PSM 6 for uniform blocks, PSM 4 for mixed content). Avoid aggressive thresholding on clean terminal text.
* Deduplicate scrolling text using sliding-window line comparisons. Preserve all typos and parser artifacts verbatim.
* Process frames in fixed-size chunks and persist intermediate results to survive session timeouts.

# 3D Data & Physics Optimization
* Parse 3D toolpaths by filtering for positive relative extrusion deltas rather than absolute coordinates.
* Project unaligned 3D point clouds onto a readable 2D plane using PCA/SVD (top two principal components).
* Optimize physics simulations by profiling bottlenecks first. Reduce per-iteration costs (e.g., switching solver types or matrix density) rather than altering physical parameters (timestep, integrator) which breaks strict tolerances.
* Validate performance optimizations by averaging multiple runs to smooth out timing outliers.
