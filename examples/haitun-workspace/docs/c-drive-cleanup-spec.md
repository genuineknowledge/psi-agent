# C-drive cleanup specification

## Scope

This feature belongs entirely to `examples/haitun-workspace`. It adds one
public workspace tool, `c_drive_cleanup`, plus one on-demand skill that governs
the conversation flow. It does not change the psi-agent core.

The feature targets the Windows system drive. Test-only root overrides may use
an isolated temporary tree on other platforms; they are not part of the public
tool interface.

## User flow

### First scan in a Session

1. The Agent calls `c_drive_cleanup(action="scan")`.
2. If the Session has no stored scan confirmation, the tool returns
   `requires_scan_confirmation=true` without reading the drive.
3. The Agent explains that the scan reads paths, sizes, and timestamps, asks
   whether to proceed, and ends the turn.
4. After approval, the Agent retries with `scan_approved=true`. A separate user
   turn is not technically required; same-turn model approval is allowed.
5. Scan confirmation is stored only after the scan and its cleanup snapshot
   have been written successfully.

### Later scans in the same Session

Later scans may proceed without a separate hard-confirmation turn. The Agent
must not scan when the user objects to that scan.

Confirmation is isolated by `get_session_id()`. A new Session asks once again.
There is no process-wide approval flag.

### Cleanup

1. The Agent presents the latest scan result and explains that selected
   temporary/cache files will be permanently deleted.
2. The Agent asks whether to execute that cleanup and ends the turn.
3. An affirmative contextual reply is sufficient; no fixed phrase is required.
4. The Agent calls `c_drive_cleanup(action="clean",
   cleanup_approved=true)`.
5. Only the latest scan in that Session is eligible. A new scan replaces the
   previous snapshot; a completed cleanup consumes the snapshot.

The design intentionally has no public `plan_id` and no 24-hour expiration.

## Public tool contract

`c_drive_cleanup` supports:

- `action="scan"`: scan allowlisted disposable locations and store the latest
  cleanup snapshot;
- `action="status"`: return the latest scan summary without candidate paths;
- `action="clean"`: revalidate and delete candidates from the latest snapshot.

Important parameters:

- `scan_approved`: records the user's first-scan confirmation for this Session;
- `cleanup_approved`: states that the user affirmed the displayed cleanup;
- `categories`: optional subset of the safe categories;
- `min_age_days`: requested minimum age, subject to category safety floors;
- `include_large_files`: report large user files without selecting them;
- `include_recycle_bin` / `empty_recycle_bin`: separate, matching scan and
  cleanup choices.

All tool results are JSON strings. Operational failures use `ok=false`.

## Candidate allowlist

Automatic deletion is limited to:

- user temporary files;
- Windows temporary files;
- crash dumps;
- Windows Error Reporting archives/queues;
- Direct3D shader cache;
- Explorer thumbnail cache files named `thumbcache_*`.

Category-specific age floors apply. Ordinary user content—Documents, Desktop,
Downloads, media, source code, databases, model files and virtual machines—is
never automatically selected. Large user files are report-only.

## Filesystem safety

- Directory symlinks, file symlinks, junctions and other reparse points are
  skipped.
- Scanning never follows a reparse point.
- A file reached through overlapping category roots is included at most once.
- The tool's own state directory is excluded from every scan.
- Each candidate snapshot records path, category, size, modification time,
  device and inode.
- Cleanup resolves each path again, confirms it remains under the current
  allowlisted root on the same volume, and requires all snapshot metadata to
  match.
- Cleanup rejects allowlisted roots that have become symlinks, junctions or
  other reparse points, and binds production cleanup to the current
  `SYSTEMDRIVE` rather than trusting the persisted drive root.
- Changed, missing, locked, malformed or out-of-root entries are skipped.
- Persisted JSON is schema-checked before cleanup.
- Planned bytes are never reported as measured freed space. Cleanup reports
  disk free space before and after execution.

## State

Pending cleanup snapshots and first-scan confirmation markers are stored under
`haitun-c-drive-cleanup-plans` in the system temporary directory. Filenames use
a truncated SHA-256 of the Session ID; the raw Session ID is not written into
the filename or payload.

This is operational state, not configuration. The directory is explicitly
excluded from scan candidates. The snapshot uses atomic replace and is removed
after cleanup. Scan confirmation intentionally has no time limit because the
product requirement is “once per Session”.

## Recycle Bin

Recycle Bin emptying defaults off. It is included only when requested during
scan, and `empty_recycle_bin` must match that stored choice at cleanup time.
The result reports Windows API failure separately.

## Non-goals

The tool does not:

- clean WinSxS or Windows Update;
- stop services or edit the registry;
- uninstall applications;
- elevate privileges;
- automatically delete reported large user files;
- provide arbitrary path deletion.
