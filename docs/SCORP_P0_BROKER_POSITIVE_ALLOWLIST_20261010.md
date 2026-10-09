# SCORP P0 — Broker positive read-only operation allowlist

Date: 2026-10-10. Isolated Draft only; no Windows service installed/restarted.

## Verified threat

The previously deployed-style `BrokerHandler` used a **negative list** of four known mutating operations. Its signature key is also readable by the same interactive executor principal that can build a valid HMAC (see `install-broker.ps1: Set-SecretAcl` and `broker_client.py: load_secret`). An HMAC alone therefore cannot prove an independent human approved a privileged request.

If a future contributor added a mutating operation to `broker_ops.validate_operation/dispatch_operation` but forgot the Broker negative list, the handler could authorize new LocalSystem side effects merely because the operation was absent from that list.

## Patch

- Replace the negative mutation list with a strictly positive, immutable Python `frozenset` containing **only** `identity.get`, `service.get` and `task.get`.
- Before `mark_inflight`, refuse **every** operation not in that positive list with `BROKER_MUTATION_DISABLED_PENDING_TASK_APPROVAL`, regardless of whether it has a valid shared-key signature and passes the backend's operation validator.
- Retain lookup of **historically completed, exact-hash ledger entries** before checking the list, so historical `DONE` receipts can be returned without any new side effects.
- Keep ambiguous/inflight legacy mutation requests fenced, never retried.
- Negative-test a hypothetical newly introduced operation with a mocked successful validator and dispatch spy: no backend call, no ledger reservation, rejected response.

## Not solved

This change does **not** make the shared key confidential to the Worker. It does **not** implement independent task-scoped approval, a private operator signing key, peer-token binding, authorizer revocation, host-only session completion or a production Broker rollout. Every future privileged operation remains blocked until a separate human-approved authorization protocol, key-custody boundary and host security audit are actually implemented.

Real Windows service ACLs, interactive user principal and key access are still unverified. Existing R1 must remain intact.

## Acceptance

Windows offline CI must prove updated Broker negative tests, read-only service queries, cached historical replay, all prior Windows P0 release/Bootstrap/install checks, 787+ R2 tests and GUI Bridge regression. These are tests of source behavior, not production attestation or 24h runtime proof.
