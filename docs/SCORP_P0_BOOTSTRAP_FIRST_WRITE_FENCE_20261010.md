# P0 bootstrap first-side-effect fence — isolated Draft

2026-10-10. No SCORP production files, Windows scheduled tasks, Chrome profiles, cookies, messages or credentials were touched.

## Root cause

The prior Bootstrap implementation created `C:\ScorpAgent\state-v4` and a unique staging directory immediately after variable initialization. It downloaded and executed the pinned candidate's self-tests **before** checking whether the existing task was `Limited`, the principal represented built-in SYSTEM/Administrator, or UAC was enabled. Rejecting an unsafe task only before `Stop-ScheduledTask` protected the task switch but still permitted pre-authorization filesystem changes and candidate test executions.

## Change

- Move the actual installed-task principal check, `Assert-InteractivePrincipal`, `RunLevel=Limited`, built-in authority SID guard, and UAC `EnableLUA=1` check to the beginning of the deployment try block.
- Only after all checks succeed, set the `preflightPassed` marker and create state/staging directories. Download, test, export/backup and task switch remain after this fence.
- If preflight failed, catch/finally do not create failure evidence or remove staging paths. If preflight succeeded and a later operation failed, retain the original rollback and diagnostic behavior.
- No automatic `Highest`→`Limited` task downgrade, no service role reassignment, no evidence of a real host ACL review, no R1 migration, no release-manifest hash rewriting.

## Verification

Windows PowerShell 5.1 parser and source ordering regression checks plus original `v4-bootstrap-limited-runlevel.ps1`, source CI and full R2 offline regression. These are source-only tests on a GitHub Windows runner. The active Windows host and its effective access token have not been observed. An administrator invoking this source may still have administrator authority during the self-test/staging phase after the preflight; a dedicated low-privilege execution account/ACL authorization review is required before release.

## Remaining release blockers

- Parent PR #17 currently reports a release-manifest integrity failure: four SHA-1 mismatches and seven SHA-256 placeholder values.
- Old `install.ps1` still defaults to `Highest` and unregisters an existing task. Do not use it for a live production migration.
- The persisted Master has unresolved browser ambiguity and there is no trusted second ChatGPT Worker session attestation.
- No unattended full 24-hour real-host soak, nor operator-approved rollback review.

The intended acceptance is fail-closed **before the first filesystem write when the authority precondition is false**. This does not mean the candidate proves complete OS privilege isolation or no-write execution under an authorized `Limited` task.
