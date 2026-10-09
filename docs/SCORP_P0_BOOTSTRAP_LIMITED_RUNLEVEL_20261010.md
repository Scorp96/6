# P0 bootstrap: fail closed on interactive Highest executor task

2026-10-10. Source-only proposal based on integrated R2 P0 draft. No production task modified.

## Verified legacy installation behavior

- `scorp-agent/install.ps1` registers `ScorpComputerAgent` with `New-ScheduledTaskPrincipal ... -RunLevel Highest`.
- `scorp-agent/bootstrap-v4.ps1` had allowed both Highest and Limited, preserving the previous task's run level.
- A generic `powershell`/`process` task could therefore run inside an elevated account even if Broker mutating operations were temporarily disabled. Broker-only containment is insufficient.

## Changes

- Bootstrap checks the currently installed task RunLevel equals `Limited` immediately after verifying interactive principal and **before** `Export-ScheduledTask`, `Stop-ScheduledTask`, task re-registration or install-dir switch.
- Bootstrap refuses any non-Limited principal at the re-registration stage and checks the reinstalled Scheduled Task again before marking switch successful.
- No code automatically rewrites an existing Highest task to Limited: auto-downgrading or modifying production scheduler rights needs an explicit reviewed migration and rollback.

## Deliberate installation conflict

This candidate will BLOCK the old default installation configured at Highest. That is safer than silently preserving admin-level arbitrary PowerShell, but it is intentionally incompatible and NOT production-ready.

The following must be completed before any release:

1. Have the human owner review local host authority output from the separate read-only inspector PR #11.
2. Design an explicit operator-approved migration from Highest to a dedicated low-privilege account with ACL controls, including broker source/python runtime and signing key isolation.
3. Backup and verify the complete original task XML, use a sacrificial Windows host, demonstrate rollback, and forbid automatic attempts to 'fix' an elevated production task.
4. Check whether the account's `Limited` token still has effective write/rename authority to privileged source and state paths; RunLevel is not sufficient alone.
5. Reconcile version-pinned release-manifest blob hashes only through an approved build/release procedure. Do not turn green offline CI into automatic deployment.
6. Revalidate actual Task Scheduler reboot/no-login behavior. Interactive logon remains required and current bootstrap does not prove unattended no-login startup.

## CI boundary

PowerShell 5.1 AST/syntax and static ordering guard + full existing R2 Windows offline regression. No real task registration or on-machine token verification in CI.
