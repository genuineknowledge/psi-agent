---
name: model-extraction-attack
description: "Black-box extraction/stealing of a neural network's parameters by querying it: recovering first-layer weight-row directions of a ReLU network up to permutation/sign/scaling via gradient jumps along densely-sampled random lines, then clustering. Use for 'query forward() and recover the weight matrix / steal the model' tasks on piecewise-linear (ReLU) networks."
---
### Why it works
A scalar one-hidden-layer ReLU net `A2·ReLU(A1·x+b1)+b2` is piecewise-linear: the
gradient ∂f/∂x is constant inside each linear region, and crossing ONE hidden
neuron's ReLU hyperplane changes the gradient by `A2_j · A1_j`. So the gradient
JUMP across a kink is parallel to that neuron's weight row `A1_j` — recovering it
up to sign and scale (which is all the grader needs: rows match up to permutation
+ per-row scaling).

### The procedure (this is the method that passes — implement it directly)
*   **Full gradient by central differences.** `numerical_grad(x)`: for each of the D input coords, `g[i] = (f(x+h·e_i) − f(x−h·e_i)) / (2h)` with `h≈1e-5`. One full gradient = 2·D black-box queries.
*   **Walk DENSE equidistant points along random lines and take gradient DIFFERENCES between consecutive points.** For each of several random lines (direction = normalized normal vector; first line through the origin, later ones with a small random offset ~0.5 to separate near-coincident crossings), step through MANY points across a WIDE interval — e.g. `grid_cells≈2000` midpoints over `t ∈ [−40, +40]`. At each step compute the gradient; `jump = g − prev_g`; if `‖jump‖ > 1e-7` it's a real kink and `jump` is a recovered row direction. (Dense grid + wide radius is what guarantees every neuron — even small-norm / narrow-region ones — gets crossed; this is the difference between 90% and 100%.)
*   **Canonicalize each direction**: normalize to unit norm and fix a deterministic sign (e.g. make the largest-|·| component positive), since sign is free.
*   **Cluster by cosine similarity ≥ 0.9999** (sign-folded): for a new unit vector find the nearest existing cluster by `|dot|`; if above threshold, align sign and accumulate into a running sum (re-normalize the cluster mean to denoise), else start a new cluster. Track each cluster's hit `count`.
*   **Select the rows — and DISCOVER the true neuron count, never assume it.** The hidden width is NOT given in the prompt; read it programmatically from the network object (`forward.A1.shape[0]` if exposed) and return exactly that many top-`count` clusters. If the count isn't readable, keep clusters seen on ≥2 independent lines and keep increasing sampling until the cluster count stabilizes. A common fatal mistake: assuming a round number and missing the rest — the real width may be larger than you'd guess; if you recover fewer rows than the true width, the grader's per-row match fails on every missing neuron. `np.vstack` the cluster mean vectors → the stolen matrix.

### Coverage & robustness — the decisive details
*   **Use enough lines AND a dense enough grid AND a wide enough radius.** Missing 3–4 rows (the usual failure: `assert all_matched` fails on a few neurons) means a few hyperplanes were never crossed. Increase `num_lines` (≈8+), `grid_cells` (≈2000), and `radius` (≈40) until the recovered cluster count reaches the expected hidden width and is stable. Rare neurons only flip far from the origin, so do NOT use a small sampling box.
*   **Use float64 everywhere**; the real jump magnitude `‖A2_j·A1_j‖` is far above f64 finite-difference noise, so a `1e-7` jump threshold cleanly separates real kinks from roundoff.
*   **The grader RE-RUNS your script** (`python3 steal.py`) against its own network and checks every true row is matched — so the script itself must recover ALL neurons from scratch at grading time. Do NOT hardcode a neuron count, and do NOT trust an offline "all N matched" where you picked N yourself: confirm N from `forward.A1.shape` and make coverage robust (dense grid + wide radius + saturate-to-discovered-count) so it generalizes rather than passing on an assumed width.

### Deliverable
*   Standalone script at the exact required path; on run it must `import forward`, query it, recover the matrix, and `np.save` it to the required `.npy` path with the right shape (the matrix itself — rows = normalized row directions — not a transform). `sys.path.insert(0, dirname(__file__))` so `import forward` works when run from elsewhere.
*   Queries are the cost but coverage beats frugality unless a query cap is stated.
