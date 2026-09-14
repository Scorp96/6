# V4 browser binding mismatch guard

## Goal
Reject a browser snapshot whose canonical ChatGPT conversation URL differs from the intent binding.

## Steps
- [x] Add failing reconcile mismatch regression test.
- [x] Return an ambiguous result on URL mismatch for submit and reconcile.
- [x] Run focused, full bridge, compilation, and diff checks.
- [x] Commit and push.

## Boundary
This does not retry or launch another conversation; it preserves the ambiguity for deterministic recovery.

