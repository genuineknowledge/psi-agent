---
name: data-text-processing
description: "Parsing and transforming structured data/text: CSV/JSON, logs, dates, dataset merging, document extraction, LaTeX, SPARQL, retrieval. Use for ETL, text wrangling, or document-processing tasks."
---
### Environment & Dependency Management
*   **Assume bare base images:** Explicitly install missing system binaries (e.g., OCR tools, Git, PDF utilities) via `apt-get` before assuming they exist.
*   **Bypass PEP 668:** Use `pip install --break-system-packages` on modern Linux distributions (e.g., Ubuntu 24.04+) to install Python libraries when standard pip is blocked.
*   **Work around missing CLI tools:** If standard utilities (`ps`, `file`, `jq`) are absent, use built-in alternatives like `/proc` for process management, `od -c` or `xxd` for byte inspection, and Python one-liners for JSON.
*   **Short-circuit installations:** Check if a module is importable (`python3 -c "import X"`) before running slow pip installs.

### Data Ingestion & Extraction
*   **Verify dataset configurations:** Always read dataset documentation/READMEs to select the correct configuration subset; default configs often omit required metadata columns.
*   **Resolve semantic categories:** Map high-level category labels to their constituent sub-categories based on documentation, rather than expecting literal matches in the data.
*   **Tokenize fields independently:** When counting tokens across multiple text fields, tokenize each independently and sum the results. Concatenating strings alters BPE boundaries and yields incorrect totals.
*   **Anchor extractions:** Extract metadata (like dates) from authoritative structural locations (e.g., filename prefixes) rather than noisy, line-level text.
*   **Use strict regex boundaries:** Use anchored regex (e.g., bracket-anchored severities) to prevent false positive matches inside free-text messages.
*   **Tune OCR segmentation:** Adjust page segmentation modes (e.g., Tesseract `--psm 6`) when processing columnar or tabular image data to prevent text fragmentation.
*   **Process a whole DIRECTORY of documents in ONE script that loops over all files end-to-end (OCR → classify → extract → move → write output), do NOT OCR files one-by-one across many turns.** OCR'ing each JPG/PDF separately and polling between each burns the turn budget before you reach classification/output — the common failure where the input dir is left non-empty and no summary is written. Write a single Python script that: lists every file, runs OCR/`pdftotext` on each (parallelize across CPUs if many), classifies (invoice vs other by keyword presence), extracts the required fields, MOVES each file to its target dir, and writes the output file — then run that script once. For PDFs prefer a text layer (`pdftotext`/`pdfplumber`) and only OCR if it's image-only.
*   **OCR is the slow step — minimize and parallelize it, or you run out of budget.** Two concrete rules that separate passing from timing-out runs: (1) For PDFs, ALWAYS extract the text layer first with `pdfplumber`/`pymupdf`/`pdftotext`; only fall back to rasterize+OCR for genuinely image-only pages — never OCR a PDF that has selectable text. (2) For the images that DO need OCR, run them concurrently in ONE Python script via `multiprocessing.Pool`/`ProcessPoolExecutor`, NOT a shell `for f in *.jpg; do tesseract ...; done` serial loop (serial Tesseract over a whole directory is the classic budget-killer). Downscale/grayscale only if needed for speed; keep one script doing all of it so there's no per-file turn overhead.
*   **Honor every extraction rule and the directory contract literally:** apply tie-break rules exactly (e.g. "if both Total and Amount Due differ, use Total"), default missing fields as the spec says (e.g. VAT absent → 0/""), match the output schema's exact column names/order, add any required aggregate/total row, and ensure the SOURCE directory ends up EMPTY (all files moved, not copied) — a leftover file or missing summary fails the directory checks even if extraction was right.

### Transformation & Aggregation
*   **Normalize international formats:** Replace comma decimals with dots and strip currency/formatting symbols before float conversion.
*   **Prevent float artifacts:** Explicitly round floating-point aggregations to avoid binary-float precision artifacts in final outputs.
*   **Define inclusive windows:** Make date-window calculations strictly inclusive (e.g., `start <= date <= end`).
*   **Resolve conflicts deterministically:** When merging multi-source data, establish a strict precedence order and only backfill missing/null fields.
*   **Enforce explicit schemas:** Cast data to explicit types (e.g., IDs to integers, dates to strings) before serializing to strict formats like Parquet.
*   **Stabilize query aggregations:** In SPARQL/SQL, stabilize string aggregations (like `GROUP_CONCAT`) by wrapping them in subqueries with explicit `ORDER BY` clauses.

### Scripting & Automation
*   **Bulletproof Vim macros:** In headless Vim, use double-quoted escapes for keystrokes (e.g., `\<CR>`, `\\`) and append the `e` flag to substitutions (`:s/.../ge`) so no-match lines do not abort the macro.
*   **Anchor Git commands:** Use `git -C <path>` or explicit `cd` chains, as test harnesses frequently reset the current working directory between shell invocations.
*   **Read before writing:** When resolving Git conflicts, always read the conflicted file state first, then write a generalized solution that satisfies all edge cases, not just the first example.
*   **Iterate compiled documents safely:** For LaTeX or compiled formats, iterate in small batches and validate by fully deleting build artifacts and recompiling twice to ensure convergence.

### Validation & Cleanup
*   **Use a sandbox:** Isolate intermediate work, backups, and build artifacts in a `/tmp` directory to avoid polluting the target evaluation directory.
*   **Cross-check aggregations:** Verify complex aggregations using an independent method (e.g., a Python script vs. a shell `awk`/`grep` pipeline).
*   **Verify byte-equality:** Use `cmp` or `md5sum` rather than visual diffs when strict output matching is required.

### Reshard / compress-decompress round-trip tasks
*   **The round trip must be EXACT and the inverse must clean up after itself.** For "reshard / compress then decompress back" tasks the grader typically: (a) runs compress, asserts every directory has ≤ N items (files+folders, NESTED dirs too) and every file ≤ some size; (b) deletes the original input; (c) runs decompress IN-PLACE and asserts the directory now contains EXACTLY the original fileset (set-equality on basenames) with matching content hashes. So decompress must reconstruct the original files at the right level AND **remove every intermediate artifact** (the tar/chunk files, any `data/` subdir, manifests) — a single leftover shard or a file one directory too deep fails the set-equality check even though content is correct.
*   **Respect the count limit by NESTING, the size limit by SPLITTING.** To keep ≤ N entries per directory with many outputs, bucket them into sub-directories (each itself ≤ N); to keep files under the size cap, concatenate then split into fixed-size chunks (a `tar` stream split into ≤size pieces round-trips cleanly and preserves names/order when chunks are sorted by an index in their basename). Store a manifest or rely on sorted basenames so decompress can reassemble deterministically.
*   **Honor the exact run contract.** If the task says scripts run under `uv` with a `pyproject.toml` (so `uv sync` installs deps and `uv run` adds nothing), create a minimal `pyproject.toml` + venv and prefer the STDLIB so `uv sync` has nothing to fetch — then test the deliverable exactly as the grader will (`uv run /app/compress.py IN OUT` then `uv run /app/decompress.py OUT`), not with a bare `python3`. A script that works under `python3` but isn't reachable via `uv run` (missing pyproject, wrong path) scores zero.
*   **Test the FULL round trip before finishing:** compress the sample → assert the count/size constraints with a walker → copy the output, decompress the copy in place → `diff -r` (or hash) against the original. Develop on the provided sample but keep the logic generic (don't hardcode the sample's file count, names, or directory shape) — the grader runs an unseen, similarly-structured slice.
*   **Leave no trace:** Remove all scratch scripts, backup files, and generated caches (e.g., `__pycache__`) before finalizing the task to satisfy strict directory-cleanliness checks.
