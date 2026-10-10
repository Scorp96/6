# P0: repeat live Worker lease check before LocalExecution

Date 2026-10-10. Source-only Draft on production `main@79610ae3f3864e133f750f0c9dfde40b17a91def`.

## Risk

Main already validates Worker leases when they are claimed and when work results are recorded, but a Worker-originated `LOCAL_EXECUTION` request may be admitted or replayed later, after its lease has expired, Master epoch has changed, its task graph has advanced, or project has been paused. The previous `MasterAController._execute_request` does not re-read the live SQLite lease immediately before preparing its local worktree or using the execution adapter.

## Bounded source change

- Add a `StateStore.assert_local_execution_lease(intent_id)` read-only check that uses the **persisted exact intent identity** and joins immutable assignment, task, project, lease and the original token; it does not accept a fresh caller-supplied token as authority.
- Also call its underlying validator *inside the same SQLite transaction* that transitions a `LOCAL_EXECUTION` intent from PREPARED to MAY_HAVE_SUBMITTED. Stale authorization therefore cannot reserve execution under a new Master epoch, expired/revoked lease, invalid task state, graph version change, paused project, identity mismatch or changed task objective.
- Recheck immediately after marking MAY_HAVE_SUBMITTED and **before** detached Git worktree preparation or local execution adapter invocation.
- Preserve cached RESPONSE_CAPTURED receipt replay without restarting any local side effect; preserve old unsubmitted browser-intent behavior. An ambiguous MAY_HAVE_SUBMITTED intent remains fenced.
- Eleven true SQLite temp-fixture tests cover expiry, revocation, identity spoof, task graph/version/epoch changes, project pause, late revocation, non-execution compatibility. No external browser or process side effects.

## Explicit limits

The second check is immediately before local action, but it does not hold a SQLite transaction lock for the entire process execution; revocation during the external process is not continuously enforced. No new `operator_generation` or `objective_generation` durable schema is added in this deliberately small mainline fix. It does not independently certify human approval, Worker browser origin, R1 migration or 24-hour soak.

Actual host read-only evidence found the SYSTEM Broker Running with unpinned on-disk code lacking reviewed positive-only operation guards. This Draft remains unmerged, the legacy R1 and browser untouched, and **production cutover is not authorized**.
