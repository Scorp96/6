# V4 Master A proposal facade

## Goal
Expose a narrow gateway API for the logical Master A to acquire a fenced epoch and submit validated state proposals without direct SQLite manipulation.

## Steps
- [x] Add failing tests for epoch acquisition, proposal commit, replay idempotency, and stale epoch fencing.
- [x] Implement gateway methods that read the current state and delegate to `StateStore.commit`.
- [x] Document the Master A call sequence for ordinary GPT handoff.
- [x] Run focused, full V4, full bridge, compilation, and diff checks.
- [x] Commit and push the change.

## Boundary
This does not make GPT reasoning autonomous and does not create browser conversations. It only makes the deterministic Master A control surface explicit.

