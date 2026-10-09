# SCORP P0 — release preflight, authority mismatch and blocking evidence

Date: 2026-10-10. Source-only Draft. Windows production not touched.

## What was verified at pinned source candidate

- Source candidate base: `4dca2b1163c06ef570f1d5819c1710919c0fe166` (PR #16).
- `scorp-agent/release-manifest-v4.json` lists exactly seven pinned artifacts.
- Four pinned `git_blob_sha` values differ from the source file blobs at this candidate: `executor-v4.1.ps1`, `runner-v4.1.ps1`, `executor-v4.schema.json` and `bootstrap-v4.ps1`.
- The manifest's SHA-256 entries are `computed-at-bootstrap`, which are not independent pre-reviewed cryptographic pins. Even if the Git blob SHA matches, release review must independently pin both digests.
- Historical `install.ps1` uses `-RunLevel Highest`, unregisters any existing task before re-registering, and automatically starts the relay. Current P0 Bootstrap candidate requires `Limited`. This is an operator migration blocker, not a deploy script bug to bypass.
- `bootstrap-v4.ps1` creates a staging directory at process start before its later task RunLevel gate. Before any production staging, the installer/dispatcher itself must be independently authorized and isolated; a green test here is not proof of zero file writes.

## This Draft implements (no deployment)

- `scripts/scorp_p0_release_verify.py` (stdlib-only, no-write, no GitHub/network access) verifies the exact expected set of seven manifest entries, rejects duplicate JSON keys, missing/unexpected entries, out-of-repository files and symlinked artifacts, then compares Git blob SHA-1 and full-file SHA-256.
- `computed-at-bootstrap` is rejected for release preflight. Successful dual matches yield only `READY_FOR_MANUAL_RELEASE_REVIEW`, never `release_authorized=true`.
- Nine isolated positive/negative tests on temporary files and one Windows candidate workflow that demands the actual candidate is `BLOCKED`. A green **safety-test** workflow is not a green **release**.
- No manifest hashes are rewritten. Pin updates require an explicit human-reviewed tagged release with full exact-commit source review and OS/token/ACL verification.

## Blocking acceptance matrix

| Requirement | Candidate assessment |
|---|---|
| P0 Broker, read-only executor, typed diagnostics, Limited bootstrap, Worker original-generation fencing | Windows offline CI PR #15/#16 passed |
| Current release manifest cryptographically matches candidate files | **BLOCKED**: 4 of 7 Git blob mismatches, all 7 SHA-256 unpinned |
| Old Highest interactive task safely migrates to least privilege | **NOT DONE** |
| Real SCORP local Windows account permissions, protected broker signing key | **NOT ATTESTED** |
| Existing R1 state/ambiguous browser intent remains unmodified | Required; no local live changes in this Draft |
| Two independent authenticated ChatGPT Worker sessions with trusted completion event | **NOT ATTESTED** |
| Master keepalive, pause/reboot and 24-hour duration soak on real host | **NOT ATTESTED** |
| Operator-authorized deployment and rollback | **NOT GIVEN** |

## Safe sequence

1. Fix initial installer/upgrade path to refuse destructive Highest-task migration; test on a sacrificial Windows principal, not the existing R1.
2. Independently obtain read-only real Windows task, ACL and identity evidence; never publish raw secrets, cookies or profile paths to GitHub.
3. Build one approved release candidate with exact SHA-1/SHA-256 manifest, Windows CI and one-shot rollback rehearsal.
4. Launch two independently authorized ChatGPT sessions without relaunching the failed Chrome profile or repeating the old ambiguous Master turn.
5. Perform actual 24-hour host soak with persistent SQLite observations and named operator acceptance; require explicit human sign-off before cutover.

No paid API, extra cloud, privilege escalation, browser authentication bypass, indiscriminate Chrome process kill, or production cutover is needed for this source-only gate.
