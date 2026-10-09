# SCORP P0 Privileged Broker fail-closed containment (candidate only)

Date: 2026-10-10. Baseline: `988c0e8466d85f42eb33822eee84a1d4f3f055ac` (R2 experimental branch). This change is NOT installed on Windows and MUST NOT be promoted as a production-ready repair.

## Verified source-level problem

* `install-broker.ps1` grants the interactive installer SID read access to `secret.key`. The same shared HMAC secret is used by `broker_client.py` for signing and by `broker_service.py` for accepting requests.
* Existing `runner-v4.1.ps1` tests a literal `USER_APPROVED_FULL_CONTROL` value for `approved_admin`. That value is not independent, operation-specific user approval.
* The service runs as LocalSystem and allows the following mutating operations: `service.restart`, `task.run`, `file.write`, `registry.set`. The names/paths are restricted, but the mutable operations remain high trust.
* The installer protects state/secret ACLs. It does not establish verified ACL and ownership protection of its code and Python runtime directories and their ancestors. Actual Windows ACLs have NOT been read.
* `file.write` performs lexical path normalization before Python `os.replace`, which does not by itself prove reparse-point safety. Runtime symlink/junction exploitation remains unverified, but no write should be authorized while this remains unclosed.

## Candidate behavior

`BrokerHandler.handle()` refuses NEW requests for mutating operations with `BROKER_MUTATION_DISABLED_PENDING_TASK_APPROVAL`. This denial occurs before `RequestLedger.mark_inflight`, so a denied request is never reserved as started. New read-only `identity.get`, `service.get`, and `task.get` remain available.

If an **identical, already DONE** request from a previous deployment is present in the immutable ledger, the broker may return the cached receipt without running the backend again. A previous `INFLIGHT` request still returns `REQUEST_INFLIGHT`, and MUST NOT be retried automatically. A bad HMAC remains rejected before the mutation gate.

There is deliberately NO environment flag, API field, or string constant that a remote task can set to re-enable mutating operations. An independent approval credential must be designed before reopening those operations.

## Explicit limitations

1. **Fail-closed is intentional and not drop-in compatible:** current privileged operational workflows that actually need restart/task-run/file-write/registry changes will be BLOCKED; do not deploy blindly.
2. This does NOT fix standard `powershell` or `process` executor privilege boundaries. Windows scheduled-task identity and actual token must be audited separately; an elevated generic runner bypasses any broker-only restrictions.
3. Current HMAC shared-secret trust architecture remains inadequate for independent human authorization. This patch contains, rather than removes, that problem.
4. Offline GitHub CI is not physical Windows production verification or two live GPT Worker verification.
5. No secret rotation, privileged service restart, live browser action, production file write or recovery reconciliation is performed by this candidate.

## Acceptance before any rollout

- Pin the exact Windows installed service source commit and examine service ProcessPath/token, the executor task principal, the secret-read ACL, and **effective write/rename rights** on `C:\ScorpAgent`, `C:\ScorpAgent\privileged-broker`, `C:\ScorpAgent\privileged-broker-runtime`, Python executable, Python package search paths, and their parent directories. Include inherited and owner rights, not only raw ACL text.
- Independently determine a trusted human-approval principal, signed one-task/one-operation capability with exact payload digest and expiry, local revocation, replay-prevention and crash ambiguity semantics. Do not use task JSON strings or the executor-readable HMAC as the approval proof.
- Remove generic privileged code execution from the executor's `standard` path, using allowlisted operations and a low-privilege token; ensure the broker client principal cannot arbitrarily write privileged source code or obtain admin token rights.
- Run the full Broker offline suite and a Windows PowerShell 5.1 syntax/behavior gate, then dedicated negative security cases on a sacrificial isolated Windows host. Prove denied operations cause zero side effects and no new ledger reservation.
- Regression-check already DONE replay, INFLIGHT ambiguity, key rotation/recovery, read-only health, and offline rollback.
- Only then run a staged R2 pilot. Never auto-merge this draft or switch production from CI alone.

## Evidence boundary

This patch changes Python Broker handler + tests and adds a Windows offline test workflow. It does not modify `executor-v4.1.ps1`, `runner-v4.1.ps1`, `install-broker.ps1`, the Windows host or the R1 active queue. Those remain tracked P0 work.
