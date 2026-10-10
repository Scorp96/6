# P0 isolated generation-bound Worker authorization — NOT a host upgrade

Source-only candidate based on [PR #45](https://github.com/Scorp96/6/pull/45); no Windows production schema has changed. Schema v4 adds monotonic operator_generation and objective_generation to project_state (default 0), and issued snapshots on assignments (default -1 for **all legacy rows**). A previously claimed Worker is NOT silently granted new operator authorization after database upgrade. New claims snapshot both counters in the durable task assignment. Gateway checks counters before preparing a Worker browser intent, Scheduler checks at result admission, and StateStore checks the counters embedded in an exact persisted LOCAL_EXECUTION intent against the original assignment and current project before marking any new possible side effect. Local execution rechecks before Git worktree/executor invocation.

**Security limits:** This patch binds generation values but does not provide an independent human-signed operation approval method or a trusted source for incrementing counters. SQLite fields alone do not authorize privileged actions. Concurrent revocation during an external process cannot be atomically interrupted. Old R1 state and ambiguous GPT browser intent remain frozen.

Test scope: real temporary SQLite fixtures, operator/objective generation changes, rejection before browser send, result rejection, and legacy schema-v3 re-open with old claims retaining generation -1. Windows Actions never touches the user's database, task, SYSTEM service, Chrome, cookies, keys or files. Production cutover NOT AUTHORIZED until independent operator approval, reversible migration/backup, live host authority proof, two physical authenticated GPT Workers and actual 24-hour soak.

## Current Worker browser-intent state gate

The direct Gateway also rechecks current project ACTIVE status, graph state_version, RUNNING task state, original task objective, worker identity and slot identity before storing a new Worker browser intent. This layer is independent of generation revocation; a paused project or stale task must fail even before a trusted controller increments generations. The guarded path does not send browser messages. Five more temp SQLite negative tests cover these cases.

## Durable Worker browser submission revalidation

A prepared `CHATGPT_WORKER_SUBMIT` action may remain queued while operator/objective generations, project ACTIVE state, task RUNNING state, original objective or Master version changes. The SQLite StateStore now rechecks the exact persisted assignment and its *locally held lease* (never the model-visible prompt) in the same transaction as `PREPARED → MAY_HAVE_SUBMITTED`. Five isolated negative tests prove operator/objective revocations, pauses and task/version changes cannot reserve a browser send. No ambiguous historical MAY_HAVE_SUBMITTED requests are retried.
