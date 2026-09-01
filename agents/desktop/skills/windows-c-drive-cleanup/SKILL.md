---
name: windows-c-drive-cleanup
description: "Safely scan and clean reclaimable space on the Windows C drive. LOAD when the user asks to 清理C盘, scan disk space, free Windows system-drive space, find duplicate or large files, inspect stale Downloads, remove temporary/cache files, or empty the Recycle Bin. Uses c_drive_cleanup with first-scan confirmation per Session, presents results before deletion, and cleans only unchanged candidates after the user confirms. Never use generic shell tools to delete files for this workflow."
category: system-administration
---

# Windows C-drive cleanup

Use `c_drive_cleanup`; do not construct deletion commands with `powershell`,
`bash`, `write`, or `edit`.

## Required workflow

1. When the user asks to scan, inspect, or clean C:, call
   `c_drive_cleanup(action="scan")`.
2. If the tool returns `requires_scan_confirmation=true`, explain that scanning
   reads file paths, sizes, and timestamps on C:, and ask whether to proceed.
   A separate user turn is not technically required: same-turn model approval
   is allowed when the Agent treats the current context as sufficient approval.
3. After approval, retry with `c_drive_cleanup(action="scan",
   scan_approved=true)`. This records scan permission for the current Session.
4. After that first confirmation, later scan requests in the same Session may
   proceed without a separate confirmation turn. Follow the normal workflow
   unless the user objects, withdraws permission, or asks to change scope.
5. Present:
   - current total/free space;
   - candidate count and bytes by category;
   - any scan truncation or tool errors;
   - large files as **report-only**;
   - exact duplicate groups and potential reclaimable bytes as **report-only**;
   - stale Downloads, including installer/archive labels, as **report-only**;
   - whether Recycle Bin cleanup is included.
6. Explain that approved cache/temp candidates are permanently deleted because
   moving them to the C-drive Recycle Bin would not free C-drive space. Ask
   whether to execute the displayed cleanup.
7. End the turn. Do not clean before the scan result has been shown.
8. If the user's next reply is affirmative in context (for example “可以”,
   “确认”, “执行”, “清理吧”, or another clear positive response), call
   `c_drive_cleanup(action="clean", cleanup_approved=true)`. No fixed approval
   phrase is required.
9. If the reply is negative, ambiguous, changes the requested scope, or asks a
   question, do not clean. Answer or run a new scan with the changed options.
10. Report measured free-space delta, deleted count/bytes, changed or unsafe
   skips, failures, and Recycle Bin result. Never equate planned bytes with
   actual freed space.

Only the most recent scan in the current Session is eligible for cleanup. A new
scan replaces the previous pending scan, and a successful cleanup consumes it.
First-scan permission is also scoped to the current Session; a new Session asks
once again.

## Safety boundaries

- Default categories are user temp, Windows temp, crash dumps, Windows error
  reports, shader cache, and thumbnail cache.
- Category age floors apply even if the caller asks for a shorter age.
- `Documents`, `Desktop`, `Downloads`, media, source code, databases, models,
  virtual machines, and other user content are never cleanup candidates.
- Large files outside disposable roots are report-only. Ask separately before
  any later user-directed file operation.
- Duplicate detection compares same-size files with sampled and then complete
  SHA-256 hashes. Hard links are not duplicate copies. Results are report-only.
- Stale Downloads use modification age and are report-only; an old installer,
  archive, or download is not automatically safe to delete.
- Symlinks, junctions/reparse points, changed files, locked files, and paths
  outside approved roots are skipped.
- Do not stop services, edit the registry, clean WinSxS, manipulate Windows
  Update, uninstall applications, or elevate privileges.

## Recycle Bin

Recycle Bin emptying is separate and defaults off. If the user explicitly asks
for it, mention that it permanently removes already-deleted items and scan with
`include_recycle_bin=true`. At clean time pass `empty_recycle_bin=true`.

If the user approves ordinary cleanup but not Recycle Bin emptying, run a new
scan without Recycle Bin rather than altering the stored scan.
