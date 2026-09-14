# V4 scheduler capacity hardening

## Goal
Make the first-release two-worker capacity invariant hold at the scheduler core, not only at the gateway facade.

## Steps
- [x] Add a failing direct-Scheduler regression test for `limit > max_workers`.
- [x] Reject over-capacity claims in `Scheduler.claim_runnable` before any recovery or transaction work.
- [x] Run focused, full V4, full bridge, compilation, and diff checks.
- [x] Commit and push the core hardening.

## Boundary
Legacy V3 pools remain compatibility-only and are not silently resized in this change.

