# SCORP typed read-only diagnostics proposal (P0 migration path)

Source: follow-on candidate to isolated integrated P0 branch; no production deployment. The last 100 GitHub executor task envelopes inspected from the control-plane issue search were all `powershell` tasks. All 100 asserted `no_production_writes=true` and `no_browser_submit=true`; these are only declarations, not OS isolation. A safety gate which rejects false read-only claims would block them. A typed alternative is required before rollout.

## Added task type

`action_kind=diagnostic_readonly`, payload contains EXACTLY `probe` as a string with no arbitrary command, path, shell or argv. Parent validates before reserving action identity; Runner validates independently.

Three allowlisted probes:

1. `chrome_resource_summary`: Win32_Process WMI query of Chrome process count and aggregate working set, with `process_owner_attested=false`. No browser connection, profile, command line, PID, URL, force kill, launch or ownership conclusion. CIM failure is not converted into zero.
2. `broker_service_status`: Get-CimInstance Win32_Service for `ScorpPrivilegedBroker`, return only state, start mode, LocalSystem boolean; missing service is error, not healthy.
3. `executor_task_status`: Get-ScheduledTask for `ScorpComputerAgent`, return state, run level, logon type; missing task is error, not healthy.

## Example envelope

```json
{
  "protocol_version": "scorp.exec/v4",
  "task_id": "safe-chrome-resource-pulse-1",
  "action_id": "safe-chrome-pulse-00001",
  "action_kind": "diagnostic_readonly",
  "timeout_seconds": 20,
  "safety_class": "standard",
  "expected_preconditions": {
    "read_only": true,
    "no_production_writes": true,
    "no_browser_submit": true
  },
  "payload": {"probe": "chrome_resource_summary"}
}
```

## Critical boundaries

- This provides only three static read-only observations; it does NOT replace arbitrary experiments, code mutation, SQLite inspection or every past forensic script.
- Windows task identity can still run Highest until separately remediated. Typed, restricted read code is a code-level reduction, not proof of least OS privilege.
- Raw counts cannot establish that Chrome processes are owned by SCORP. Do not use to authorize termination or profile deletion.
- The current V4 release manifest hashes MUST be regenerated only under deliberate reviewed release process; this proposal is NOT a deployable manifest and MUST fail version-pin-based bootstrap.
- Sender/author trust and genuine GPT Worker2 host attestation remain unresolved.
- No silent migration of previous task/action IDs or rerun of ambiguous requests.

## Candidate verification

Windows PowerShell 5.1 syntax, four isolated Runner cases including injected `script`/`path`, full existing Windows R2 offline test suite. Human review of data privacy and actual Windows privilege/account separation required before pilot.
