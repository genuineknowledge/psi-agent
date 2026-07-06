---
name: digital-circuit-construction
description: "Building a computation out of low-level primitives that run on a provided simulator: logic-gate netlists (AND/OR/XOR/NOT/assign), HDL, cellular automata, or any 'emit a circuit/gate file that computes f(input) under sim.c' task. Covers learning the simulator's timing/evaluation model first, generating the netlist with a high-level builder, incremental closed-loop verification against the real simulator, and gate/step budget management. Use for gate-level / circuit-synthesis tasks (adders, comparators, isqrt, fib, arithmetic in gates)."
---
### Learn the simulator's timing/evaluation model BEFORE designing
A gate file runs under a specific simulator whose update semantics decide whether
your circuit even computes what you think. Reverse-engineer it empirically first.
*   **Read sim.c and run tiny experiments.** How are signals evaluated within a step (index order? event queue? min-heap)? Does a gate see this-step or previous-step values of its inputs? How many steps does a feed-forward chain take to settle? Build a 3-gate chain and a carry chain and observe, rather than assuming combinational instant propagation.
*   **Understand state/feedback across steps.** A signal defined by a "backward" reference (an output used as an input of an earlier line) behaves like a register latching the previous step's value — this is how you get sequential logic / iteration across the simulator's fixed step count. Confirm exactly how a value persists: e.g. you often must declare `out_i = out_i` (copy-self) to HOLD an input/state, or it gets overwritten.
*   **Map the I/O contract:** which lines are the input (e.g. first 32 = stdin bits), which are read as output (e.g. last 32 → integer), and how many simulation steps you get. Your whole computation must converge within that step budget.

### Build with a high-level generator + a reference model
*   **Never hand-write thousands of gate lines; write a builder script** (Python) with a gate allocator, constant caching (one shared 0/1), and reusable sub-circuit functions (full-adder, ripple/carry adder, mux, comparator, equality, shifter). Compose buses (lists of bit-signals) and emit the netlist.
*   **Build a netlist SIMULATOR in the high-level language too**, mirroring sim.c's exact timing, so you can verify your circuit's logic in Python (fast, debuggable) BEFORE/alongside running the real sim. This catches logic bugs without the slow round-trip.

### Verify INCREMENTALLY against the real simulator — never open-loop generate
This is the decisive failure split: the trials that pass run the real `./sim`
dozens of times on known inputs; the trials that fail generate a big netlist and
NEVER run it, then confidently declare done on an unverified circuit.
*   **Test each sub-circuit on known input/output pairs with the real simulator** as you build it (adder adds, comparator compares, isqrt of a few N, fib of a few i). A sub-circuit that isn't individually verified will silently corrupt the whole result.
*   **Run the provided examples through `./sim` and diff against the expected outputs** continuously; a circuit you "reasoned is correct" but never executed is almost always wrong. If you've generated the file but run the simulator zero times, STOP and run it — that is the single most common way these tasks score 0.
*   Decompose the target (e.g. `fib(isqrt(N))`) into stages (isqrt, then fib, then mod), verify each stage's bus output independently, then the composition.

### Respect the gate/step budget
*   **If there's a hard cap (e.g. < N gates or < M steps), count as you build and treat it as a first-class constraint** — a correct circuit that exceeds the cap scores zero. Measure the gate count after each component; if you're over, optimize the hot spot (multiplication is the usual blowup — a 32×32 multiply via summed partial products is huge; prefer iterative/shift-add with shared adders, or fewer wider adds).
*   Pick gate-efficient algorithms: bit-by-bit/restoring isqrt and iterative fib with a couple of registers are far cheaper than unrolled or table approaches.
