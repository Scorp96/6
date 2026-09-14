# Scorp Multi-Agent Orchestrator V2 Design

Date: 2026-09-11
Status: Approved design specification

## 1. Goal
Upgrade the existing durable Scorp Orchestrator so one GPT-5.6 Sol controller (A) can decompose a user mission, dispatch independent bounded work to worker slots B and C, collect durable checkpoints before worker context windows close, detect drift from the original mission, and issue the next generation of work without requiring the user to repeatedly type continue. The system must preserve the user's original objective; workers may report alternatives or blockers but may not redefine the root objective, acceptance criteria, or safety boundaries.

## 2. Roles and authority
### A 闁?Mission Controller
A is the sole planning and dispatch authority. A owns root_objective, acceptance_criteria, invariants, work_breakdown, resource allocation, dependency graph, branch plan, drift decisions, continuation decisions, merge/reconciliation decisions, and the terminal decision. Only A may create or mutate worker assignments or publish executable work to the production control plane.

### B and C 闁?Worker Slots
B and C are replaceable execution slots. They may execute only the assignment bound to their current generation and may return WORK_RESULT, CHECKPOINT, BLOCKER, or HANDOFF. They may not mutate mission authority fields, publish production actions directly, or delegate work to another worker.

## 3. Single-writer rule
User -> A -> durable Orchestrator -> single Dispatch Writer -> B/C assignments -> V4 Executor -> deterministic adapters -> Windows. B and C never write directly to the production execution queue. All worker output returns to A through durable handoff records. The existing V4 executor remains the single execution writer on the machine. Multi-agent V2 adds planning concurrency, not multiple uncontrolled production writers.

## 4. Mission identity and anti-drift binding
A mission record contains A-only or immutable mission_id, project_id, root_objective, root_objective_sha256, acceptance_criteria, acceptance_sha256, invariants, safety_class, and created_at. Each worker assignment binds assignment_id, mission_id, parent_task_id, worker_slot, branch_id, objective, objective_sha256, root_objective_sha256, acceptance_sha256, generation, resource_scope, dependencies, success_criteria, checkpoint_policy, and lease. A continuation is valid only if mission identity, assignment identity, generation, prior result/checkpoint hash, and authoritative registry sequence all match. Stale or mismatched work fails closed.

## 5. Drift Guard
Before A continues, merges, or terminates a worker branch, A evaluates the handoff. Verdicts are ALIGNED, DEVIATED, SCOPE_VIOLATION, STALE, CONFLICT, or NEEDS_REPLAN. Only ALIGNED may auto-continue. Workers may recommend changing direction, but recommendations are evidence for A and never self-authorizing mutations.

## 6. Parallel work and resource isolation
A may run B and C concurrently only when dependencies are satisfied, parallel_safety is true, resource_scope intersection is empty or explicitly read-only/shared, neither assignment requires an A-only mutation, and branch outputs can be reconciled deterministically. If scopes overlap, A serializes work or creates a merge/reconciliation task. Existing Orchestrator dependency and resource_scope behavior remains authoritative.

## 7. Execution-window checkpoint policy
Default policy is execution_budget_ratio: 0.75 and checkpoint_reserve_ratio: 0.25. At the checkpoint threshold a worker stops opening large new work unless needed to leave a safe state and emits a durable HANDOFF. The handoff contains root objective hash, assignment objective/generation, completed/in-progress/remaining work, resources changed, tests/evidence, blockers, deviations considered, resource-scope assessment, recommended next action, safe resume point, and result/checkpoint hash. Structured data is authoritative; prose is supplemental.

## 8. Durable protocol objects
V2 introduces mission-v2, worker-assignment-v1, worker-checkpoint-v1, handoff-v1, drift-verdict-v1, and branch-plan-v1. Existing task-v1 and continuation-v1 remain supported during migration. V2 fields may initially use task-v1 additionalProperties, but production autonomous dispatch requires explicit validation.

## 9. Orchestrator mutations
A-only mutations: CREATE_CHILD_TASKS, ASSIGN_WORKER, CONTINUE_WORKER, REPLAN_WORKER, COMPLETE_WORKER, MERGE_BRANCH_RESULT, SET_TERMINAL. Worker-originated durable events: WORK_RESULT, CHECKPOINT, BLOCKER, HANDOFF. CREATE_CHILD_TASKS already exists in continuation schema but is not implemented in Apply-ContinuationDecision; V2 must implement it with atomic registry/WAL semantics and generation checks.

## 10. State machine
Mission: STAGED -> ACTIVE -> VERIFYING -> DONE, with WAITING/BLOCKED/FAILED fail-closed alternatives. Assignment: STAGED -> READY -> RUNNING -> CHECKPOINTED -> READY for a continued generation, or RUNNING -> DONE, with WAITING/BLOCKED/FAILED alternatives. A checkpoint is a durable continuation boundary, not proof of success.

## 11. Continuation flow
A creates child assignments; Orchestrator validates safety; the single Dispatch Writer publishes eligible work; workers execute; workers emit HANDOFF before reserve expiry or on blocker/terminal result; A computes Drift Guard; A issues continuation/replan/merge/block/terminal; Orchestrator applies with CAS against registry sequence and generation. The cycle repeats without requiring user messages such as continue.

## 12. Failure handling
Missing handoff at lease/window expiry moves the assignment to WAITING/BLOCKED. Duplicate identical handoff is idempotent; conflicting content under the same identity fails closed. Stale continuation is rejected. Worker crash recovers from durable assignment/evidence without inventing progress. Controller interruption resumes from registry/WAL. Conflicting B/C changes require A reconciliation. Autonomous mode must block if more than the configured single Dispatch Writer authority is detected.

## 13. User interaction policy
Routine continuation is autonomous. The system must not require the user to send continue or next step. A interrupts only for materially ambiguous mission direction, external communications/publication requiring approval, payment/purchase, account/security changes, irreversible/destructive operations, or safety-policy boundaries. Progress reports may be surfaced without requiring a response.

## 14. Migration strategy
Phase 1: protocol validation and CREATE_CHILD_TASKS without parallel execution. Phase 2: B/C assignment, structured handoff, generation-bound continuation, Drift Guard. Phase 3: two-worker parallel scheduling only for disjoint resource scopes. Phase 4: production canaries for window handoff, stale generation, drift rejection, resource conflict, worker crash recovery, and single-dispatch-writer enforcement. Phase 5: enable autonomous A/B/C mode by default for approved project tasks. No phase may weaken V4 exactly-once, Broker exactly-once, generation leases, CAS, fail-closed ambiguity, or GUI Bridge persistent-conversation behavior.

## 15. Test requirements
Tests must prove CREATE_CHILD_TASKS atomic/idempotent; B/C cannot mutate A-only fields or publish directly; stale generation and changed mission hashes reject; DEVIATED cannot auto-continue; overlapping scopes do not run concurrently; disjoint scopes can; checkpoint reserve yields durable handoff; controller restart resumes without user continue; duplicate identical handoff replays; conflicting duplicate fails closed; worker crash does not duplicate privileged effects; only one Dispatch Writer authority is active; legacy single-worker tasks continue working.

## 16. Production acceptance criteria
Production-ready means A can create B/C assignments; workers receive bounded objectives/scopes; workers checkpoint before reserve boundary; A resumes from handoff without user continuation messages; intentional drift is rejected/corrected; overlapping scope is serialized/blocked; disjoint scope runs concurrently/reconciles; restart preserves identity/generation; V4/Broker/GUI regressions remain green; and exactly one Dispatch Writer authority is proven in production.

## 17. Non-goals
Initial V2 does not support more than two concurrent worker slots, worker-to-worker delegation, B/C direct production mutation, autonomous changes to root objective/acceptance criteria, replacement of V4/Broker/GUI Bridge, or unbounded free-form multi-agent chat as a control mechanism. The design favors durable state, explicit authority, and deterministic reconciliation over conversational freedom.