---
name: video-motion-analysis
description: "Analyzing video to locate an EVENT in time or read on-screen content: detecting when an action happens (takeoff/landing, onset/offset, a transition) by tracking ONE robust per-frame signal against a baseline and reporting frame indices within a tight tolerance, or transcribing on-screen text/commands. Covers adaptive background subtraction, largest-component tracking, percentile-baseline + apex-anchored boundary detection, calibrating against the provided example before the held-out test, and matching strict similarity graders. Use for 'find the frame where X happens in the video' or 'extract the sequence shown in the video' tasks (cv2/numpy-only)."
---

# Video motion & event analysis (category skill)

Two task shapes share this skill. **Part 1 (the common, harder one): locate an
event in time** — output frame index/indices marking when something happens
(takeoff/land, onset/offset, a transition), graded by index within a **tight
tolerance** on a **held-out** video you calibrate from one example. **Part 2:
read text/commands** shown on screen, graded by a similarity threshold.

## Part 1 — Locate an event by a tracked signal (frame-index output)

### When this applies
Output is a **frame index** (or a pair: start/end) marking an event in a temporal
stream; the capture setup is fixed/known; the event is **transient and embedded
in ongoing motion** (running strides, chatter, drift); there is a **small
tolerance** (±a few frames) and a **held-out** instance you must generalize to
from one provided example. Library constraint is often **cv2/numpy/toml only**.

### The single most important insight
**Reduce each frame to ONE robust scalar SIGNAL, set a BASELINE for "resting" from
a robust statistic, find the APEX (extreme deviation), then walk OUTWARD from the
apex to the frames where the signal returns to baseline — those return points are
the event boundaries.** Do NOT classify the event directly or threshold raw motion
magnitude: ongoing normal motion produces blips of the same gross size as the
event. What separates the event is a *sustained* deviation of the *right feature*.
Example: for a jump, the signal is the **bottom-y of the runner's bounding box
(the feet)** — grounded feet sit at a stable baseline; only the real jump lifts
them far and for **several consecutive frames** (~7 @ 30fps), unlike the 1–2 frame
dips of a stride.

### Pin these per instance — the details that decide pass/fail
These are exactly what separates "right strategy, wrong frame" (the common
failure: a boundary that lands one frame outside the tolerance, or tens of frames
off on the held-out clip because the detector locked onto the wrong segment) from
a pass. Each is a discover-and-pin action, not a baked-in value:

1. **Calibrate on the PROVIDED example first — it is your only ground truth.**
   The spec ships an example clip whose event you can read frame-by-frame. Dump
   frames, find the true takeoff/land by eye, and tune until your detector
   reproduces them, BEFORE trusting the held-out video. Skipping this is why runs
   land one frame off or lock onto the wrong block.
2. **Use an adaptive background subtractor, not frame-0 differencing.**
   `cv2.createBackgroundSubtractorMOG2(history=10, varThreshold=50,
   detectShadows=False)` (or consecutive-frame `cv2.absdiff`). Backgrounds drift
   (wind, light, shake) and MJPEG compression adds artifacts that a fixed frame-0
   subtract reads as huge false motion.
3. **Skip frame 0's mask.** MOG2 reports the ENTIRE first frame as foreground on
   its first `apply()`; the prompt typically guarantees no subject in frame 0.
4. **Keep only the LARGEST connected component as the subject** (after open+close
   morphology), and filter tiny blobs (`area < ~500`). This kills late-video
   reflections/noise that otherwise look like a second subject. Use
   `cv2.connectedComponentsWithStats`; don't over-erode.
5. **Isolate the FIRST contiguous present-block before measuring anything.** The
   subject enters, crosses, exits; *later* detections are artifacts. Tolerate
   ≤~4-frame gaps to bridge a momentary miss. Computing baseline/apex over the
   whole video lets post-exit noise corrupt them (a frequent cause of a
   wildly-wrong boundary on a held-out clip).
6. **Baseline = a robust PERCENTILE of the signal in that block** (~90th pct of
   bottom-y for a jump), not the mean — the airborne frames bias the mean. A frame
   is "resting/grounded" if it is within `GROUND_TOL` (~40 px) of baseline.
7. **Anchor at the apex, then walk to the boundary, and pin the off-by-one
   convention.** `apex` = the extreme-deviation frame (smallest bottom-y).
   `takeoff` = last resting frame *before* apex; `land` = first resting frame
   *after* apex. Whether the boundary is the last-resting frame or its successor is
   exactly the ±1 that fails a tight grader — verify the convention against the
   example's known answer and pick the one that matches.
8. **Stay scale-free / example-independent.** Use the percentile baseline + apex-
   relative walk, NOT absolute pixel constants tuned to one clip — the held-out
   video has a different timeline (the event can occur at a very different frame
   offset and the clip can be much longer), so any hard-coded frame window or
   absolute y-threshold overfits and fails it.
9. **Match the output contract exactly.** Write the required output file at the
   exact path, with the exact field names and types the grader reads (discover
   them from the prompt/grader — e.g. integer frame numbers serialized via the
   required format); run the script end-to-end on the example before finishing.
   Respect the library allow-list (often cv2/numpy/toml only — a 4th import fails
   an import check).

### Decision procedure
1. Read the grader: how is the script invoked, what file/fields must it emit, what
   is the tolerance, and is there an import allow-list? Pin all four.
2. Choose the ONE discriminating signal for this event (foot height for a jump,
   energy for an audio onset, a channel value for telemetry).
3. Build the per-frame signal robustly (steps 2–4 above): adaptive FG → morphology
   → largest component → the scalar.
4. Isolate the first contiguous active block (step 5), compute the percentile
   baseline (step 6), find the apex, walk outward to boundaries (step 7).
5. Calibrate on the example until boundaries match the eyeballed truth (step 1 of
   the pin-list), confirm scale-free, emit the output, run end-to-end.

### Reference scaffold (inline — the apex-anchored detector)
The spine works for any frame-index event task: build a per-frame signal, isolate
the active block, percentile baseline, apex, walk outward. Swap `per_frame_signal`
for your event's discriminating feature; keep the boundary logic.

```python
import cv2, numpy as np

def per_frame_signal(video_path):
    """Return list of (frame_idx, signal, present). signal=bottom-y of largest
    blob (the feet) for a jump; present=False when no real subject (incl. frame 0)."""
    cap = cv2.VideoCapture(video_path)
    fgbg = cv2.createBackgroundSubtractorMOG2(history=10, varThreshold=50,
                                              detectShadows=False)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    out, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        m = fgbg.apply(cv2.GaussianBlur(frame, (11, 11), 0))
        if i == 0:                                   # MOG2 1st frame = all-FG
            out.append((i, None, False)); i += 1; continue
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
        n, lab, stats, _ = cv2.connectedComponentsWithStats((m > 0).astype(np.uint8), 8)
        present, sig = False, None
        if n > 1:
            j = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            if stats[j, cv2.CC_STAT_AREA] >= 500:    # filter reflections/noise
                ys = np.nonzero(lab == j)[0]
                sig = int(ys.max()); present = True   # bottom-y = feet
        out.append((i, sig, present)); i += 1
    cap.release(); return out

def first_block(sig, max_gap=4):
    idx = [i for i, s, p in sig if p]
    if not idx: return []
    block, gap = [idx[0]], 0
    for a, b in zip(idx, idx[1:]):
        if b - a - 1 <= max_gap: block.append(b)
        else: break
    return block

def detect_event(sig, ground_tol=40):
    blk = first_block(sig)
    vals = {i: s for i, s, p in sig if p}
    ys = np.array([vals[i] for i in blk])
    baseline = np.percentile(ys, 90)                 # robust resting level
    apex = blk[int(np.argmin(ys))]                   # feet highest = smallest y
    grounded = [i for i in blk if vals[i] >= baseline - ground_tol]
    takeoff = max([i for i in grounded if i <= apex], default=apex)
    land    = min([i for i in grounded if i >= apex], default=apex)
    return takeoff, land                             # verify ±1 convention on example
```

Calibrate `detect_event`'s boundary convention and `ground_tol` against the
example's known answer, confirm it's scale-free, then write the two boundary
frames to the grader's required output file under the exact field names it reads
(serialize with the required library, e.g. `toml.dump({...}, f)`).

## Part 2 — Read text/commands shown in the video (similarity-graded)
*   **DEFAULT TO A MULTIMODAL MODEL — do not hand-roll a per-frame OCR loop.**
    This environment injects a vision-capable model into the task sandbox via the
    env vars **`$GEMINI_API_KEY`, `$GEMINI_API_BASE`, `$GEMINI_MODEL`** (check them
    with `printenv`/`echo`); when present, a multimodal model IS available — use
    it to read the video (or sampled frames) directly. It is far more robust than
    OCR on stylized/noisy on-screen text, AND far faster. Running `tesseract` (or
    any OCR) over every frame of a clip with hundreds of frames is an
    O(frame-count) loop that **routinely eats the whole time budget and times out
    before producing any output** — this is the #1 way this task shape fails. So:
    confirm the env vars, then call the multimodal endpoint (a small `curl`/HTTP
    request from a helper script, passing the video file or a handful of sampled
    frames as image inputs) and let it transcribe. Treat local frame-OCR as a LAST
    RESORT, only when those env vars are absent.
    *Skeleton (discover the exact request shape from the model's API; this is the
    pattern, not a literal command):* sample a few stable frames with cv2 →
    base64-encode → POST to `$GEMINI_API_BASE` with `$GEMINI_API_KEY` asking
    `$GEMINI_MODEL` to transcribe the on-screen text in order → parse the reply.
    If passing the whole video is supported, prefer that over frame sampling.
*   **Sample frames where the text is stable** (dedupe near-identical consecutive
    frames), read each, and reconstruct the ordered sequence; order errors are
    especially costly under a whole-text similarity metric.
*   **A similarity threshold can be strict** (e.g. Levenshtein ≥ 90% over the whole
    file). "Mostly right" fails: every extra/missing/duplicated line and every
    misread character eats the small budget. **Get count and order right first**,
    then minimize per-character noise.
*   **Reproduce the source faithfully, including its own typos/terse spellings** —
    the reference came from the same video, so "correcting" it moves you away from
    the target. Match what is shown; do not normalize.

## Failure modes (mined from real runs)
- **Right strategy, wrong frame (the dominant failure).** The detector tracks the
  correct signal but lands ±1 outside the tolerance, or off by tens of frames on
  the held-out clip. Causes below — fix them, don't change strategy.
- **No calibration against the example.** Not reproducing the example's known
  takeoff/land before submitting → off-by-one ships. Calibrate first (Pin #1, #7).
- **Baseline/apex computed over the whole video.** Post-exit reflections corrupt
  the percentile and apex → wildly-wrong frames on held-out videos. Isolate the
  first contiguous block first (Pin #5).
- **Frame-0 background subtraction / forgetting MOG2's first-frame all-FG.** Huge
  false motion / garbage frame-0 mask (Pin #2, #3).
- **Detecting by motion magnitude or torso position.** Running strides false-
  positive; track the discriminating feature (feet) and require a sustained dip.
- **Absolute pixel/frame constants tuned to the example.** Overfits; the held-out
  timeline differs. Use percentile baseline + apex-relative walk (Pin #8).
- **A 4th library import** (mediapipe/scipy/skimage) → import-allowlist check fails.
- **Over-aggressive morphology** fragments/inflates the blob → wrong bottom-y.
