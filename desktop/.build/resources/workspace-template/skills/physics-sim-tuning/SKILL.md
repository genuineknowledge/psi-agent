---
name: physics-sim-tuning
description: "Speeding up a physics-simulation model (MuJoCo/MJCF, Bullet, other rigid-body engines) to hit a runtime target while keeping the simulated trajectory correct within a tolerance: tuning solver/integrator/timestep/contact settings that trade speed for accuracy, profiling per-step cost, and validating state-equivalence with the provided eval script. Use for 'tune this model so it simulates N% faster but reaches the same state (atol=...)' tasks. Do NOT change physical properties (masses, geometry) — that breaks correctness."
---
### What you may tune vs. what you must not touch
The target is to cut wall-clock per simulated second WITHOUT changing the physics
the model represents.
*   **Tune SOLVER/INTEGRATION settings, never PHYSICAL properties.** Changing masses, inertias, geometry sizes, joint ranges, gravity, or contact stiffness (`solref`/`solimp`) alters the actual dynamics → the trajectory diverges and the correctness check fails. The speed knobs that preserve dynamics are the integrator choice, the timestep, and the constraint-solver effort.
*   **Keep the reference model unchanged** and write the tuned model to the exact required output path; the grader compares your tuned run against the reference run.

### The high-leverage speed knobs (MuJoCo/MJCF)
*   **Solver iterations / tolerance:** the biggest free lunch. The default `iterations` (and `ls_iterations`) is often far more than needed for a settled scene. Reduce them and check the trajectory still matches — fewer constraint iterations is pure speed if accuracy holds.
*   **Solver algorithm:** `solver="PGS"|"CG"|"Newton"` have very different per-step costs; the cheapest one that stays within tolerance for THIS scene wins. Test each.
*   **Contact model:** `cone="pyramidal"` is cheaper than `elliptic`; reducing `condim` (contact dimensionality) where the scene allows cuts constraint count. `jacobian="sparse"` vs `dense` matters for larger models.
*   **Integrator:** `Euler` is cheapest per step; `implicitfast`/`implicit` can allow a LARGER timestep stably (net faster) for stiff systems; `RK4` is accurate but 4× the dynamics cost. Pick by what keeps the state within tolerance.
*   **Timestep:** a larger `timestep` is the most direct speedup (fewer steps for the same sim time) but is the first thing to cause divergence or NaN — raise it only as far as the correctness check still passes, often in concert with a more stable integrator.

### Profile, don't guess
*   **Find where the per-step time goes before tuning.** Use the engine's internal timers (MuJoCo exposes per-step timing for forward dynamics, constraint solve, collision) to see whether the cost is in the solver, collision, or integration — then attack that. Blindly twiddling knobs wastes the budget.
*   **Change ONE knob at a time and measure both speed and correctness** against the provided eval/timing script, so you know which change bought the speedup and which broke accuracy.

### Validate state-equivalence, and watch for NaN/Inf
*   **Run the provided eval script the way the grader will** — it typically times the tuned model over a fixed sim duration AND checks the full physics state matches the reference within `atol` (no NaN/Inf). Both must pass: a faster model that drifts past tolerance, or that produces NaN at a too-large timestep, scores zero.
*   **A too-aggressive timestep/iteration cut shows up as NaN/Inf or state drift** — back off to the largest step / fewest iterations that still passes the tolerance check. The winning configuration is the speed/accuracy boundary, found by bisecting, not by one guess.
*   **Confirm the speed target with margin** (e.g. if you need ≤60% of baseline, aim comfortably under) since timing is noisy, and re-verify the final saved model from a clean run before finishing.
