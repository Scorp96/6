# P0 integrated containment — source/CI only

Target R2 isolated base: 988c0e8466d85f42eb33822eee84a1d4f3f055ac. This branch composes the three P0 Draft PRs, without merging any or writing to the production machine.

## Imported features

1. Broker new mutating operations are blocked until independent per-action human authorization exists; existing DONE replay and INFLIGHT ambiguity remain intact.
2. Executor and runner reject un-enforceable read-only/no-writes/no-kill/no-browser promises for unrestricted powershell/process/git tasks. Both parent and child enforce; the parent rejects before ledger reservation.
3. Read-only Windows authority observer available as a separate operator-reviewed script to collect ACL, owner, token-principal and service identity evidence without mutation.

## Combined CI gate

- Nine side-effect negative/positive Runner cases on Windows PowerShell 5.1.
- Entire existing Broker Python unittest suite on Windows.
- Observer parse and no-write sandbox proof.
- Entire existing R2 candidate offline regression (V4 + GUI bridge + Broker).
- Git diff hygiene.

## Material compatibility risks / reasons to stop

- Historical SCORP 'readonly' tasks often used unrestricted PowerShell. After this patch they will fail rather than silently bypass read-only claims. Requires a typed safe diagnostic collector before moving production.
- Existing bootstrap preserves Highest Scheduled Task principal; old installer requests Highest. Therefore these Broker/Runner safety patches are not enough to demonstrate OS least-privilege. Move Executor to a low-privilege account and prove the token cannot write Broker code, service binary, Python runtime, agent allowlisted dirs or signing key.
- Existing shared Broker key is readable by a user principal: this does not prove task-scoped consent. The Broker mutation block stays in place until an independent credential and approval ledger exist.
- Existing HMAC cannot authenticate a physical ChatGPT session or a human approval event. Worker2 remains unproven; R1 original pending ambiguity remains.
- CI must NOT treat synthetic GPT probe fixture as genuine ChatGPT live session proof, despite green test output.
- No true 24-hour runtime, reboot survival, no-login recovery, or authenticated real Worker2 proof has been collected here.

## No production rollout

Do not merge or deploy this integration PR without direct Windows read-only host authority evidence, sign-off for read-only diagnostic migration, actual non-admin Executor token proof, and live R2 functionality regressions in a sacrificial environment. Blocked/ambiguous historical jobs must not be replayed to 'recover' progress.
