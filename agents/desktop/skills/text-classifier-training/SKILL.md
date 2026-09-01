---
name: text-classifier-training
description: "Training a text classifier to pass an accuracy threshold on a held-out set (fastText, scikit-learn, small transformers): mirroring the grader's exact preprocessing, sizing training to the machine's resources, and saving a passing model before optimizing. Use for 'train a model that scores above THRESHOLD on a private test set' tasks where you control training but the grader controls inference."
---
### Mirror the grader's inference preprocessing EXACTLY
*   **The grader feeds RAW input straight into `model.predict()` — so train on text preprocessed the SAME way the grader will feed it, not the way you'd clean it.** If the grader does no normalization (just reads a line and predicts), then train on raw, case-preserved text and only collapse whitespace. Lowercasing, splitting punctuation, stemming, or any train-time cleaning the grader does NOT replicate silently tanks the held-out score even when your locally-cleaned validation looks great.
*   **Read the grader's inference code first** (how it reads a line, what it splits on, whether it lowercases) and make your training tokenization identical. fastText tokenizes on whitespace and is case-sensitive; a label/text format mismatch (`__label__` prefix, separator) also breaks it silently.
*   **Validate against the grader's protocol, not your own** — hold out data and score it through the exact predict path the grader uses, so your local accuracy actually predicts the private-set accuracy.

### Save a PASSING model first, then improve (budget discipline)
*   **Detect your resources before sizing the run:** check CPU count, RAM, and whether a GPU exists; pick hyperparameters (epochs, dims, n-grams, data subset) that finish comfortably inside the time budget on THIS machine, not a guessed config that may run for hours.
*   **Train a small, fast model that clears the threshold and SAVE it to the deliverable path early.** A saved model that passes is worth infinitely more than a better one that's still training when the budget ends. Only after a passing artifact is on disk should you scale up (more epochs/dims/data) — and re-save after each improvement so the best *passing* model is always the one on disk.
*   **Prefer the framework's own tuning/autotune** (e.g. fastText autotune against a validation file) over hand-rolling a training loop, when available and time permits — but still keep a known-passing baseline saved first.

### Knobs and what they trade
*   More epochs / higher dim / word n-grams raise accuracy but cost time and memory; learning rate and bucket size matter for fastText. Calibrate on a small subset to estimate per-config runtime before committing to a full run.
*   If accuracy is short of the threshold, first suspect a preprocessing mismatch with the grader (the highest-leverage fix), then data quantity/quality, then capacity — in that order.
