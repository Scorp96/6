# P0 Final Broker early capability fence and exact-source review artifact

Date: 2026-10-10. This remains an **unmerged source-only Draft**, not an authorized production release.

## Why an additional permission boundary matters

PR #20 correctly defaults new Broker operations to three read-only names only: `identity.get`, `service.get`, and `task.get`. However its initial implementation called `validate_operation` **before** enforcing that positive read-only allowlist. That validator is currently pure. A future new backend operation with inadvertent effects in its validator would therefore run some code in the privileged service before the deny decision.

This candidate enforces:

1. Verify the existing executor-shared HMAC and read the durable ledger only (no backend operation invoked).
2. If an exact prior `DONE` receipt exists, return the immutable cached result without re-executing.
3. For any new request outside the three read-only operation names, reject before calling `validate_operation` or `dispatch_operation`, and do not reserve an execution ledger entry.
4. For new read-only requests, perform ordinary parameter validation, freshness and idempotency before invoking the corresponding read-only backend.

The negative test simulates a newly added future operation and asserts **both** backend validation and dispatch remain uncalled.

## Release review artifact

The exact commit-bound `scripts/scorp_p0_release_pin_plan.py` now writes a seven-file source pin proposal to a temporary GitHub Actions artifact `scorp-p0-review-pins-<commit>` (retained for seven days). It contains only Git file names, SHA-1/SHA-256 values, the candidate commit identity, and fail-closed review flags. No cookies, passwords, HMAC keys, personal browser URLs, Windows profile directories or raw prompts belong in it.

The artifact is an **audit convenience**, not a signed source release. The original `scorp-agent/release-manifest-v4.json` still has four mismatching Git blob hashes and seven SHA-256 placeholders; it remains `BLOCKED`. A reviewer must independently verify the exact commit before choosing to publish new pins.

## Remaining STOP conditions

- No independently held human approval key or per-task authorization for Broker writes
- No real Windows host token/ACL proof or verified backup/migration for the existing R1
- No two distinct, authenticated GPT Worker physical browser sessions or trusted terminal event issuer
- No confirmed keepalive/safe-pause/no-send restart cycle through a 24-hour host soak
- No user authorization to merge, replace Windows services, downgrade tasks, kill Chrome processes, or send browser prompts

This PR keeps the entire privilege Broker write path disabled and preserves the original ambiguous browser intent.
