# P0 Broker legacy installer containment — operator-first and fresh-only

Date: 2026-10-10. Source-only isolated candidate; no service, task or secret changed on the Windows host.

## Why the original installer was unsafe

`scorp-agent/privileged-broker/install-broker.ps1` contained a destructive upgrade path: it created new files, installed runtime dependencies, wrote a shared HMAC key and state, modified ACLs, stopped and deleted a matching Windows service, replaced installed code, and started a SYSTEM service. These side effects began before the existing-service ownership inspection and without any explicit project-specific approval.

The key ACL grants the interactive user read permission. That is required by the present HMAC-bearing client, but means HMAC is **not** an independent approval signature. This candidate does not claim to fix the key custody model. PR #20's strictly read-only Broker operations stay the safety barrier.

## New source-level refusal contract

- Default plain invocation aborts before any file/service change with `P0_BROKER_INSTALL_DEFAULT_DENY_USE_READONLY_AUDIT`.
- Explicit opt-in requires `-InstallFreshIsolated` plus exact `-OperatorAcknowledgement FRESH_ISOLATED_BROKER_NO_PRIOR_STATE`. This is only an operator acknowledgment; not cryptographically independently signed human approval.
- Before creating any file or installing dependencies, reject SYSTEM/built-in admin installer identity, existing Broker service, any existing installed files, runtime or state, or a prior signing secret.
- Refuse unless **all four** reviewed SHA-256 values for `broker_core.py`, `broker_ops.py`, `broker_service.py`, `broker_client.py` are provided and match the actual source.
- New isolated service setup retains existing key/ACL and LocalSystem creation mechanics only behind all these explicit preconditions. It is not a safe path to migrate the existing running Broker.
- No `-Force` migration, R1 task stop, Chrome operation or Windows production deployment is authorized in this Draft.

## Evidence and non-claims

The Windows CI will parse the PowerShell script and execute only **two guaranteed denied invocations**: no arguments, and a fresh-only switch without the required acknowledgment. It must observe explicit denial without creating `C:\ScorpAgent` on the clean CI fixture. It also runs all parent P0 and R2 regressions.

Static ordering tests do **not** prove the successful fresh installation path (including UAC, ACL propagation, Python dependency installation, named-pipe service identity, key owner and rollback). Live Windows authority remains unverified and Broker mutating actions remain globally disabled.

## Required before release

A dedicated account and ACL review, a reviewed fresh-source SHA-256 bill of materials, independent human authorization/key custody design for future writes, a controlled Windows throwaway install rehearsal, an operator-approved migration/rollback, and real separate GPT Worker attestations. The current release manifest still fails closed (four mismatching Git blobs, seven unpinned SHA-256 values).
