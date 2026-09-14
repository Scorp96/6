# V4 completion gate facade

## Goal
Expose the deterministic acceptance validator through the V4 gateway as a read-only completion check.

## Steps
- [x] Add a failing test proving incomplete evidence returns BLOCKED and does not mutate project state.
- [x] Implement `evaluate_completion()` on the gateway.
- [x] Document that GPT cannot self-declare COMPLETE and must report blockers.
- [x] Run focused, full V4, full bridge, compilation, and diff checks.
- [x] Commit and push.

## Boundary
This facade does not fabricate evidence, mark production complete, or replace the independent validator. It only routes the check through the explicit V4 control surface.

