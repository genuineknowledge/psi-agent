---
name: leaderboard-snapshot-query
description: "Answering 'best model/entry on leaderboard X as of date Y' by reconstructing a point-in-time ranking from the underlying results repo: snapshotting the data to the right date, reproducing the leaderboard's exact aggregation/coverage rule, and emitting the answer in the required format. Use for tasks that ask for a top entry on a benchmark/leaderboard as of a past date, where the live data has since changed."
---
### "As of date Y" means SNAPSHOT the data, not read it live
*   **A dated leaderboard question is a point-in-time reconstruction, not a live lookup.** Leaderboards are computed from an underlying results repo/dataset that keeps growing; by the time you run (the container clock may be much later), HEAD contains entries added *after* the target date that would outrank the true answer. You must evaluate the repo **at a commit from the target date**.
*   **Check out a commit at the cutoff:** clone the results repo (use `GIT_LFS_SKIP_SMUDGE=1` and `git fetch --unshallow` if it's shallow), then `git rev-list -1 --before="<cutoff date>" HEAD` and `git checkout` that commit before scoring anything. If your HEAD ranking and your snapshot ranking differ (newer entries on top), you forgot the checkout.

### Reproduce the leaderboard's EXACT aggregation and coverage rule
*   **The headline metric usually has a specific aggregation that excludes incomplete entries — get it exactly right or you pick the wrong winner.** A common rule: the "Mean" is computed with `pandas.mean(..., skipna=False)`, so an entry missing ANY required sub-task is NaN and **excluded entirely** — a high partial-coverage entry does NOT win. Treating it as `skipna=True` ("most-covered wins", or averaging only present tasks) silently ranks a partial entry first.
*   **Derive the metric the leaderboard's way, don't scrape a displayed number.** Read the leaderboard's source (its table-building code) to confirm the aggregation (mean vs Borda vs rescaled) and the coverage requirement; then recompute the *relative ranking* yourself rather than trusting one displayed figure that may be aggregated differently.
*   **Get the per-entry scoring right:** discover the required sub-tasks/subsets programmatically (don't hardcode a guessed list), average over the correct subsets per task, then apply the cross-task aggregation. Taking only the first subset of a multilingual/multi-part task is a classic wrong-scoring bug.

### Emit the answer in the exact required format
*   **The grader is typically an exact string match** — produce precisely the required form (e.g. `organization/model_name`, one line, single trailing newline, no extra whitespace or second line). The on-disk directory form (`org__model`) or a stray comment fails the match.
*   Sanity-check the winner has full coverage and beats #2 by the metric you computed, then write exactly the required string.
