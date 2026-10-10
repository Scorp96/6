# P0: two logical Worker slots cannot share one ChatGPT conversation URL

Date 2026-10-10. Source-only Draft based directly on production `main`. No real Chrome or ChatGPT sends occurred.

## What is proved

Main originally allowed both `worker/worker-slot-1` and `worker/worker-slot-2` to return the **same** `conversation_url` while independently declaring work completed. The two-worker offline count alone was therefore insufficient evidence of distinct conversations.

Add a transactionally evaluated SQLite collision check to:
1. `StateStore.capture_response` for a Worker result,
2. `StateStore.confirm_submitted` for a submitted Worker intent,
3. `StateStore.rebind_browser` for a persisted logical Worker channel.

The guard checks *other Worker slots in the same project* across both stored action intents and existing bindings, and rejects the second identical URL with `WORKER_CONVERSATION_COLLISION` **before accepting its result**. The losing intent stays in its prior state for reconciliation, never silently marked successful.

Existing fake browser engines now assign distinct deterministic URLs to Worker slots. Eight isolated SQLite tests check collision at capture, confirm and bind; distinct URLs succeed; same-channel idempotence is preserved; other projects/master channels remain logically separate.

## Important limits

Distinct conversation URLs do **not** prove different authenticated ChatGPT accounts, physical Chrome profiles, local session owners, login state, trusted final-turn issuer or actual independent inference capacity. A browser could still point multiple URLs into one compromised process. An actual browser send has already happened before a remote-returned URL can be checked; this guard protects durable *admission*, not the remote transmission. It is not a substitute for two real authenticated Worker host attestations and 24-hour soak.

Do not merge, deploy, open browser sessions or replay historical ambiguous Master intent on this Draft alone. No R1 or SYSTEM Broker mutation.
