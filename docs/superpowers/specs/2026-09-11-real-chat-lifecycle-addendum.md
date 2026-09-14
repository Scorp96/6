# Real Chat Lifecycle Addendum

Date: 2026-09-11
Status: User-corrected authoritative addendum to `2026-09-11-multi-agent-orchestrator-v2-design.md`.

## 1. Physical conversation invariant
A, B and C are not conceptual labels. A role is considered instantiated only when the single GUI Bridge has created or adopted a real canonical ChatGPT conversation URL of the form `https://chatgpt.com/c/<id>` and persisted it in the mission-role conversation registry. For a three-role mission, A, B and C must have three distinct canonical conversation URLs. `worker_slot=B` or `worker_slot=C` without such a URL does not mean that worker exists.

## 2. Bootstrap
The bridge creates A first from a bound SYSTEM mission bootstrap turn when no A URL exists. When A dispatches B or C and that worker has no saved URL, the bridge opens `https://chatgpt.com/`, submits the bound worker assignment, captures the newly created canonical `/c/<id>` URL, persists it, and only then considers the worker instantiated. Later turns must reuse the exact saved URL. Failure to reopen a saved role conversation fails closed; the bridge must not silently replace it.

## 3. Lifecycle budget
The default maximum continuous reasoning/execution budget for one role turn is approximately 25 minutes (`role_turn_budget_seconds=1500`). This is a maximum, not a minimum delay. A may dispatch B/C immediately when decomposition is ready. For a long-running turn, the prompt instructs the role to stop opening large new work around 20 minutes (`handoff_reserve_seconds=300`) and emit a durable DISPATCH/HANDOFF/BLOCKER/TERMINAL response by about 25 minutes. The bridge hard timeout may be slightly larger only to allow UI response capture; timeout is not evidence of completed work.

## 4. Alternating control
Normal sequence is `A(real chat) -> B(real chat) -> A(same real chat) -> B(same real chat)` or `A -> B/C -> A`, with GUI turns serialized by the one Bridge writer. Worker handoffs are durably persisted before A is reopened. A validates drift, generation and scope before sending another generation. B/C never dispatch to each other and never publish production actions directly.

## 5. No hourly wake dependency
The relay must not depend on a ChatGPT hourly automation or on the user typing `continue`. The already-running Windows GUI Bridge poll loop is the lifecycle driver, with its normal short poll interval (currently 10 seconds). Scheduled hourly ChatGPT reminders are not part of this architecture.

## 6. Controller conversation and user chat
The production A controller is a dedicated persistent ChatGPT conversation owned by the relay registry. It may be visible to the user, but autonomous continuity must not require the user to type into it. The interactive chat in which the system was initially designed is not automatically considered the production A conversation unless its canonical URL is explicitly adopted and persisted by the relay.

## 7. Acceptance proof
Production acceptance requires visible evidence of distinct persisted A and B canonical URLs for the two-role canary, and distinct A/B/C URLs for the three-role canary; automatic A->B->A message flow with no user input; URL reuse on the second B turn; a role-turn duration/checkpoint record; and proof that exactly one GUI Bridge process drove all Chrome mutations.
