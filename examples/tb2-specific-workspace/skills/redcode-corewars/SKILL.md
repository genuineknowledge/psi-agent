---
name: redcode-corewars
description: "Writing a Core War (Redcode) warrior to beat a fixed panel of opponents under pMARS: choosing a robust archetype (core-clear / bomber / paper / scanner), tuning its constants, and validating offline against the exact opponents and scoring the grader uses. Use for 'write a warrior that wins ≥X% vs these opponents' Core War tasks."
---
### One robust archetype usually beats a per-opponent switcher
*   **A single well-tuned core-clear ("G2-style") warrior clears a broad opponent panel — you rarely need opponent detection or a switch3.** A decrementing-pointer MOV-clear that sweeps the whole core, overwriting every opponent with bomb values, beats aggressive bombers/trappers decisively and ties/wins enough against slippery self-copiers to clear lower thresholds. Reach for this archetype first; only specialize if it can't clear one specific opponent.
*   **Core-clear anatomy:** a `mov *bptr, >gate` loop driven by `djn.f clear, }bomb` — two `mov`s copy a bomb through memory via the auto-incrementing `>gate` pointer, and `djn.f` decrements both fields of the bomb pointer and loops so the sweep walks the entire core and terminates cleanly.
*   **Make the bomb an `spl #dec, …`** so an enemy process landing on it forks into a useless immediate-loop (`#dec` is data, not a jump target), neutralizing replicators (paper/snake).
*   **Place the gate a few cells *behind* the clear** (`gate equ clear-4`) so an enemy bomb hitting the gate gets harmlessly swept forward as your own ammo instead of killing your loop — this is what survives stones and rival core-clears.

### Tune constants offline against the real panel
*   **The key constants must be tuned, not guessed** (e.g. the bomb/decrement step sits near `CORESIZE/3`; the clear step and gate offset interact). Sweep candidate values and keep the one that maximizes the worst-case margin across all opponents.
*   **pMARS placement is deterministic from the assembled code** (no random seed with a fixed `-r` round count), so a given warrior scores the *same* every run — you can tune offline and trust the numbers; don't chase phantom variance.
*   **Pin the core parameters the task fixes** (`;assert CORESIZE == ... && MAXCYCLES == ...`) in the file, and make sure the warrior **assembles cleanly** — a pMARS assembly error means returncode≠0 and an instant fail regardless of strategy.

### Validate the grader's exact way
*   **Run the grader's actual command** (`pmars -b -r N -f my.red warriors/<opp>.red`) against EACH opponent file the task ships, and parse wins the same way the grader does (e.g. the 2nd integer of the last stdout line). Confirm every per-opponent threshold is cleared with margin before submitting — the weakest matchup is what fails you.
*   Iterate on the lowest-margin opponent: adjust the archetype/constants until even the hardest opponent clears its bar, rather than over-optimizing an already-winning matchup.
