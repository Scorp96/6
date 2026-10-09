# P0 Worker capability: bind original generations to local side effects

2026-10-10. Draft and isolated CI only. Not applied to any SCORP Windows installation.

## Verified source behavior prior to this candidate

- Scheduler issues distinct random `lease_token` values for each Worker assignment and stores original `operator_generation`, `objective_generation`, `master_epoch`, access mode and resource scopes in SQLite.
- `StateStore._check_intent_authority_row` already verifies the durable lease token, Worker actor/slot, assignment state, project epoch/state, expiry and resource scope immediately before `LOCAL_EXECUTION`. This is a real code-level fence, not a Windows administrator token.
- Controller's `LOCAL_EXECUTION` envelope previously copied the **latest operator generations** from storage at intent creation, but did not include the original generations embedded in the issued Worker assignment. The existing root intent-generation check could therefore agree with fresh generations even when the Worker claim had older ones (if revocation failed or was bypassed).
- The Worker response itself is not authorized to forge a new task template; controller compares complete execution requests to the previously accepted `execution_request_template`.

## Change

1. Persist the scheduler-issued `operator_generation` and `objective_generation` in the controller's `worker_assignment` sub-envelope. Do not send a Windows elevation credential to GPT.
2. For `LOCAL_EXECUTION` intents only, the store rejects missing, boolean, malformed, or stale issued generations. Compare them with **both** persisted assignment generations and the current operator controls. The previous lease, actor, resource, access-mode and project epoch checks remain mandatory.
3. Reject before any local adapter invocation; keep read-only legacy/browser intent reconciliation separate.
4. Reject incomplete root operator/objective generation binding for any new LOCAL_EXECUTION side effect.\n5. Refuse a browser Worker prompt that contains its raw local bearer lease token, **before preparing or submitting an intent**.\n6. Add 11 isolated SQLite tests for active valid lease, forged/expired token, actor mismatch, stale operator/objective generations, missing/boolean fields and root-generation spoofing, plus a browser-prompt regression.

## Boundaries and required follow-up

- No change to live Windows tasks, process privileges, persistent browser sessions, Broker secrets, or production release manifest.
- An old prepared `LOCAL_EXECUTION` intent without original generations will **fail closed** on replay. Review and reconcile it explicitly. Do not silently regenerate or execute it.
- A Python/SQLite check just before adapter invocation does not grant OS process isolation, nor does it make lease validation and subprocess creation one indivisible atomic OS operation. This remaining time-of-check-to-use boundary should be tested under concurrent revocation before production.
- Offline CI cannot prove real two-GPT-Worker authentication, 24h unattended operation, Windows principal authority, or operator-approved privileged actions.
- Do not log/export raw `lease_token`, and do not pass it in plain-text Worker prompts. Only the trusted local scheduler/controller should hold the bearer secret.
- Do not deploy until PR #15's consolidated Windows workflow and this PR's own workflow both pass, reviewer validates offline negative tests, and a Windows-host human approves a migration/rollback plan.
