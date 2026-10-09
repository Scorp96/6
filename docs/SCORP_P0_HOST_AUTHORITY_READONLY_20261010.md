# Read-only host authority snapshot — P0 evidence collector

Created 2026-10-10 on isolated candidate R2 SHA `988c0e8466d85f42eb33822eee84a1d4f3f055ac`.

## Objective

Collect actual Windows identity and selected ACL evidence before making changes to the LocalSystem Broker or Executor. Current source-only analysis cannot establish installed ACLs, process tokens, trusted binary provenance or effective parent-directory rename permissions.

## Usage (operator-run, review stdout locally)

From a PowerShell 5.1 terminal, after independently reviewing the exact pinned source:

```powershell
powershell.exe -NoProfile -NonInteractive -File .\scorp-agent\privileged-broker\audit-host-authority-readonly.ps1
```

The script invokes only read operations: `Get-Item`, `Get-Acl`, `Get-FileHash`, `Get-CimInstance`, `Get-ScheduledTask` and JSON formatting. It does not install, restart, kill, write to disk, edit GitHub, launch a browser, send GPT messages or extract the signing-key bytes. Output includes SIDs, file paths and service identity; treat as private host diagnostic evidence.

## Interpret carefully

- `conclusion=REVIEW_REQUIRED_NOT_A_CERTIFICATION` always; a clear finding list is not proof of safety.
- Non-SYSTEM/non-Administrators write ACEs or ownership for privileged code, runtime, or agent root are suspicious and require direct effective-access analysis.
- Broker signing key readability by a non-trusted SID is flagged; current protocol intentionally allows installer user to read the shared HMAC key. This cannot serve as independent human approval.
- The collector enumerates selected files, not the full Python import/dependency graph, parent DELETE_CHILD rights above `C:\ScorpAgent`, service executable provenance, actual current tokens, or reparse-point replacement races.
- Absence of services/paths or inability to read ACL is `MISSING` / `ACL_UNVERIFIED`, not a proof that the system is absent or compromised.
- Do NOT automatically paste raw diagnostic stdout to an external public issue. Validate / redact local user SIDs and paths first.

## Promotion gate

No service change until service StartName and installed path are verified; code/runtime ancestor ACLs and owner rights are manually reviewed; generic executor actual token is confirmed low-privilege; per-operation consent mechanism exists; immutable binary hashes match an approved release; and a Windows isolated negative test proves an untrusted caller cannot change the Broker's privileged runtime.
