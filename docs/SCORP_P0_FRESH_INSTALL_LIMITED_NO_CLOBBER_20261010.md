# SCORP fresh-only Limited task installer — Draft, not a migration tool

2026-10-10. Source-only, no Windows task created or changed by this work.

## Verified old installer risks

`scorp-agent/install.ps1` originally auto-elevated, installed Codex through npm if missing, initiated Codex login, unregistered any old `ScorpComputerAgent` task, registered with `RunLevel Highest`, and immediately started the relay. This combination is incompatible with the P0 Limited-bootstrap policy and could destroy the original R1 executor state.

## Source-only changes

- **Default is BLOCKED**, including a plain `install.ps1` invocation. There is no implicit migration or automatic elevated task replacement.
- The only opt-in path is explicitly `-InstallFreshLimited` with a user-provided, reviewed 64-digit `-PinnedRelaySha256` and exact acknowledgment `FRESH_LIMITED_ONLY_NO_EXISTING_TASK`.
- Refuse if the existing task is present. Check again after UAC elevation to prevent accidental replacement when another task appeared.
- Reject SYSTEM and built-in Administrator identities, UAC disabled, incorrect relay hash before and after elevation, and missing authenticated GitHub/Codex CLI. **Never automatically install CLI packages or open login windows**.
- New task uses `New-ScheduledTaskPrincipal -LogonType Interactive -RunLevel Limited` and `New-ScheduledTaskSettingsSet -Disable`. Never `-Force` replacement or `Start-ScheduledTask`. Verify the registered task remains **Disabled**, `Limited`, Interactive, and bound to the same identity. The operator must explicitly review ACLs and re-enable later.
- No claims of supported pre-logon headless operation: Interactive still requires a signed-in user.

**The switch is not authorization to apply this to the existing machine.** The user's installed task must not be replaced by this path. If the task is already registered, it is deliberately refused; an operator must design and approve a separate backup/rollback migration of R1 to R2.

## Remaining limitations and gates

- `Limited` in Task Scheduler alone does **not** prove the process cannot write privileged files or read Broker signing material. Inspect actual parent directory ACLs and effective token under the intended Windows account.
- The relay path under `C:\ScorpAgent` remains a legacy, unversioned source path until its manually reviewed SHA-256 is provided. This installer is NOT the release-manifest bootstrap procedure and must not be used as a substitute.
- A race in Task Scheduler registration requires a live no-clobber test; source-only checks cannot prove atomic task name ownership.
- Rollback is not automated because this installer intentionally does not mutate an existing task; a failed fresh registration may leave a disabled new task requiring human inspection.
- No existing R1 stop, process kill, browser send, paid API, new cloud resource or production cutover. Actual two GPT Worker session admission and 24-hour operation remain unverified.

## Microsoft command semantics

- [New-ScheduledTaskSettingsSet -Disable](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps) creates a disabled task setting.
- [Register-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask?view=windowsserver2025-ps) has optional `-Trigger` and `-Force`; this source retains a logon trigger **but keeps the task disabled** until explicit operator action.

## CI scope

Windows PowerShell 5.1 source/parser/order tests, the parent Bootstrap pre-first-write check, parent dual-hash release blocker proof, complete R2 Python + GUI Bridge offline suites. It must report pass on safety guards while the actual release manifest remains BLOCKED.
