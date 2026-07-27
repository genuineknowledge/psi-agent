# C-drive cleanup implementation plan

This plan records the final PR scope and decisions. The normative behavior is
defined in [c-drive-cleanup-spec.md](c-drive-cleanup-spec.md).

## 1. Public interface

- [x] Add `tools/c_drive_cleanup.py` with one public async tool.
- [x] Support `scan`, `status`, and `clean`.
- [x] Return JSON strings compatible with the workspace tool contract.
- [x] Keep test-only root/state overrides out of the public signature.
- [x] Remove public `plan_id`, fixed approval phrases and plan expiration.

## 2. First-scan confirmation

- [x] Scope confirmation to `get_session_id()`.
- [x] Refuse the first unapproved scan before filesystem inspection.
- [x] Allow same-turn model approval; do not require a later user-message count.
- [x] Record confirmation only after the first successful scan snapshot write.
- [x] Allow later scans in the same Session without another hard confirmation.
- [x] Keep different Sessions isolated.

## 3. Safe scanning

- [x] Restrict automatic candidates to known temporary/cache categories.
- [x] Enforce category age floors.
- [x] Report large user files without making them deletable.
- [x] Avoid following symlinks, junctions and file/directory reparse points.
- [x] Deduplicate files reached through overlapping category roots.
- [x] Exclude the tool's own state directory.
- [x] Cap candidate and large-file result counts.
- [x] Tolerate inaccessible files and directories.

## 4. Confirmed cleanup

- [x] Require an affirmative user reply after the scan summary.
- [x] Require `cleanup_approved=true` at the tool boundary.
- [x] Use the latest per-Session snapshot without exposing an ID.
- [x] Validate persisted JSON before use.
- [x] Re-resolve paths and revalidate root, volume and snapshot metadata.
- [x] Reject reparse-point allowlist roots and rebind production cleanup to the
  current Windows system drive.
- [x] Skip changed, unsafe or inaccessible files.
- [x] Consume the snapshot after cleanup.
- [x] Report actual free-space delta and individual failures.

## 5. Recycle Bin

- [x] Default to excluded.
- [x] Require scan-time and cleanup-time choices to match.
- [x] Use the Windows shell API without privilege elevation.
- [x] Report API failure separately.

## 6. Agent guidance and documentation

- [x] Add `skills/windows-c-drive-cleanup/SKILL.md`.
- [x] Document the first-confirmation and later-scan flow.
- [x] Document contextual cleanup confirmation with no fixed phrase.
- [x] Update `AGENTS.md` tool and skill indexes.
- [x] Update `README.md`.
- [x] Add this plan and the feature specification.

## 7. Verification

- [x] Isolate filesystem tests under `tmp_path`.
- [x] Cover first confirmation, later scans and Session isolation.
- [x] Cover failed first scans not granting confirmation.
- [x] Cover allowlist behavior and user-document exclusion.
- [x] Cover state-directory exclusion.
- [x] Cover overlapping-root deduplication.
- [x] Cover snapshot replacement and revalidation.
- [x] Cover reparse allowlist roots and system-drive snapshot mismatch.
- [x] Cover malformed persisted state.
- [x] Cover Recycle Bin option mismatch.
- [x] Run Ruff lint and formatting checks.
- [x] Run the focused C-drive cleanup tests (`15 passed`).
- [x] Run all Haitun tests except the two known MCP cache-sensitive modules
  (`510 passed`).
- [x] Run the full Haitun workspace test suite. Result on 2026-07-27:
  `529 passed, 9 failed`; all nine failures are in the pre-existing
  `test_browser.py` / `test_canvas.py` MCP schema-cache tests. Earlier tests
  create `.mcp_cache` entries, causing later monkeypatched-discovery tests to
  consume cache instead of their mocks. The focused C-drive suite is independent
  and passes.
