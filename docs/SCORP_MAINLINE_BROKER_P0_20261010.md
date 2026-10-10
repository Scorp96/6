# SCORP P0 Broker on *current main* — narrowly scoped source-only review

Date: 2026-10-10. Parent Git SHA: `79610ae3f3864e133f750f0c9dfde40b17a91def`.

## Evidence from the failed aggregate release path

The earlier squashed PR #26 was based on an isolated P0 containment branch, not production `main`. GitHub comparison `main...46036709910cbc2784a0e926bbfd35ae892ec64e` returned `ahead_by=716`, and the two recursive Git trees revealed 488 existing `main` files versus 910 candidate blobs: **422 candidate-only files**. This makes direct production merge unacceptable even though the old isolated Windows CI passed.

## This draft is a genuine narrow main-based port

We intentionally port **only** the Broker positive read-only capability boundary to the current `main`, without copying the 422 candidate-only files or modifying master/worker, executor, startup services, or the active release manifest.

1. New Broker commands may only invoke `identity.get`, `service.get`, or `task.get`, with an explicit `frozenset` capability list checked **before** operation-specific validation or dispatch.
2. All requests outside that list fail with `BROKER_MUTATION_DISABLED_PENDING_TASK_APPROVAL`. A valid shared HMAC cannot authorize SYSTEM service restarts, task launches, writes or registry changes.
3. Durable ledger `DONE` records from legacy mutations may be replayed exactly as cached responses, with no new backend call; `INFLIGHT` stays blocked and must not be retried.
4. Six isolated fake-backend negative/positive tests ensure future unknown operations don't even enter the backend validator; zero external system services/tasks or credentials are touched.

## CI and release boundary

A branch-scoped Windows CI checks the diff against `origin/main` and allows only four specified review paths, rejecting mainline drift or unrelated changes. It installs `pywin32` **on the ephemeral runner only**, runs all existing Broker tests, full mainline R2/GUI Bridge offline regression, and produces a machine-verifiable no-deploy conclusion.

Even if CI is green, this is a Draft review only. We have **not** installed it on the user's Windows host. The interactive executor still holds the Broker HMAC secret; independently signed per-task human approval is absent. The original R1 remains unchanged. Neither a live second GPT Worker nor 24-hour runtime proof has been produced.

## Subsequent bounded release stages

- Independently port fresh-only Broker installer protections without touching current service, then run an isolated Windows install failure-path rehearsal.
- Port and test the R1-safe Task Scheduler installer / pre-write Bootstrap gates in their own mainline PR(s).
- Carry over Master→Worker original lease generations only after confirming the precise current main schema/runtime dependencies.
- Assemble reviewed exact-source release pins from a deliberately approved base. Do not auto-update `release-manifest-v4.json`.
- Secure independent Windows authority/ACL proof; resolve old ambiguous browser send without replay; verify two real authenticated GPT sessions and 24-hour soak before production cutover.

**Status:** `NARROW_MAINLINE_BROKER_DRAFT`; `PRODUCTION_CUTOVER_NOT_AUTHORIZED`.
