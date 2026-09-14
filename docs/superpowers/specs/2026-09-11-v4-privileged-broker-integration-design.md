# V4 → Privileged Broker Integration Design

## Goal
Integrate Privileged Broker V1 into GPT-Native Executor V4 without widening the broker allowlist or granting V4 arbitrary elevated shell/process access.

The integrated path is:
`GPT-5.6 Sol -> Orchestrator -> V4 executor -> V4 runner -> ScorpPrivilegedBroker -> Windows`.

V4 remains authoritative for task/action/generation identity, cloud claim/result lifecycle, authorization, and global task/action idempotency. The broker is authoritative only for the exactly-once privileged effect.

## Scope
Add one explicit V4 action kind, `privileged_broker`, whose payload is exactly `operation` plus `params`. Existing `powershell`, `process`, `git`, file, and health kinds keep their current execution semantics and are never silently elevated.

`privileged_broker` requires `safety_class=approved_admin` and `authorization=USER_APPROVED_FULL_CONTROL`. The runner may invoke only the fixed installed broker client/runtime; it must not accept an executable path, script text, command line, or alternate pipe/secret path from the envelope.

The broker V1 operation allowlist remains unchanged: `identity.get`, `service.get`, `service.restart`, `task.get`, `task.run`, `file.write`, and `registry.set`.

## Alternatives Considered
### A. Recommended: explicit broker action kind
Add `privileged_broker` and route only that kind through the broker. This makes privilege use visible in the envelope and preserves the current non-elevated meanings of all existing action kinds.

### B. Auto-elevate existing `approved_admin` actions
Rejected. Mapping existing PowerShell/process actions to elevation would bypass the broker's narrow operation model and turn `approved_admin` into an implicit arbitrary LocalSystem shell surface.

### C. Run the entire V4 executor as LocalSystem
Rejected. This collapses the privilege boundary, enlarges blast radius, and makes every V4 action privileged by default.

## Identity Binding
The broker request id is deterministic from the V4 logical task/action/generation identity, not from process identity or a random UUID.

Generation token derivation is explicit:
- if `task_id` ends in `/gN`, use `gN`;
- otherwise use the fixed token `direct` for direct/non-orchestrated V4 actions.

The request id is:

`v4.<sha256("scorp.exec/v4\n" + task_id + "\n" + action_id + "\n" + generation_token)>`

The envelope SHA-256 remains bound independently in the existing V4 action ledger. Therefore reusing the same task/action with changed envelope content is rejected by V4 before execution. If a changed privileged payload somehow reaches the broker under the same deterministic request id, the broker request-hash conflict independently fails closed.

The broker request itself must be durably persisted before the first pipe write. A runner retry normally reuses the exact same signed request bytes rather than rebuilding timestamps/HMAC, so broker replay matches the original canonical request hash.

## Durable Request Artifact
For `privileged_broker`, the runner uses the fixed root `C:\ScorpAgent\state-v4\broker-requests\` and names the request artifact from the deterministic generation-bound broker request id.

Before contacting the pipe, the broker client performs get-or-create behavior:
1. If the request artifact exists, authenticate it and verify that request id, operation, and params match the current envelope; reuse the artifact unchanged.
2. If absent, build/sign the request, atomically persist it, then send that exact request.
3. Never overwrite an existing nonmatching or unauthenticated artifact.

The request artifact contains an HMAC but not the shared secret. The secret stays only in the broker state root. The runner cannot choose an alternate pipe, secret, executable, or client path from envelope data.

## Expiry and Replay Semantics
First-execution freshness and replay identity are intentionally separate:
1. Verify schema, protocol, HMAC, operation policy, and canonical request identity/hash.
2. Check the ledger for the exact request.
3. If a matching `DONE` entry exists, return the cached result even when the request is now expired. No privileged effect is executed.
4. If a matching `INFLIGHT` entry exists, return `REQUEST_INFLIGHT`; V4 fails closed because outcome is unresolved.
5. If no ledger entry exists, enforce issued/expiry time before any privileged effect.

This preserves short authorization freshness for first execution while permitting safe, effect-free reconciliation after restart or long outage.

If the exact persisted request is expired and the broker proves it was never accepted by returning the correctly bound `REQUEST_EXPIRED`, the client may regenerate the signed request exactly once with the same deterministic request id and fresh timestamps, atomically replacing only that proven-never-accepted artifact. A second `REQUEST_EXPIRED` fails. `REQUEST_INFLIGHT`, `REQUEST_ID_CONFLICT`, response identity mismatch, or any other broker error never permits regeneration.

## V4 Recovery Semantics
Ordinary V4 actions remain fail-closed when a runner disappears after the start gate exists and no durable V4 result exists, because shell/process/file effects can be ambiguous.

For `privileged_broker` only, V4 may perform a bounded reconciliation relaunch because the durable broker request plus broker ledger provide the second exactly-once boundary. The executor:
1. uses authoritative runner discovery before launching anything;
2. adopts a matching live runner if one exists;
3. permits at most one ambiguous broker reconciliation relaunch;
4. increments and durably saves `broker_reconcile_attempts` before that relaunch;
5. retains the original ambiguous-child failure path unchanged for every non-broker action.

A relaunched broker runner reuses the same generation-bound request artifact. It can only receive a cached completed result, execute safely if the broker proves the prior request was never accepted, or fail closed on unresolved/conflicting state. If the single reconciliation allowance has already been consumed and no durable result exists, V4 blocks rather than launching again.

## Runner Result Evidence
A successful privileged result includes:
- deterministic generation-bound broker request id;
- generation token;
- broker operation;
- `replayed` boolean;
- broker result object;
- SHA-256 of the persisted broker request artifact;
- broker request artifact path.

No secret material or raw shared key is included in result/log evidence.

## Files and Contract Changes
Implementation surface:
- `scorp-agent/executor-v4.schema.json`: explicit `privileged_broker` action kind.
- `scorp-agent/executor-v4.1.ps1`: narrow privileged envelope validation plus broker-only bounded reconciliation.
- `scorp-agent/runner-v4.1.ps1`: generation-bound request identity, fixed-path broker invocation, durable request evidence.
- `scorp-agent/privileged-broker/broker_core.py` and `broker_service.py`: authenticate identity separately from first-execution freshness and replay exact `DONE` requests after expiry.
- `scorp-agent/privileged-broker/broker_client.py`: atomic request persistence/reuse and one-shot refresh only after bound `REQUEST_EXPIRED` proof.
- focused broker/V4 hardening tests plus live acceptance.

No orchestrator schema change is required: generation remains encoded by the authoritative orchestrator task id suffix `/gN`; direct V4 actions use the explicit `direct` generation domain.

## Failure Handling
Unknown broker operations, malformed params, auth failure, request conflicts, inflight ambiguity, response identity mismatch, unavailable broker, or exhausted reconciliation all fail the V4 action without fallback to elevated PowerShell/process execution.

A broker outage is never bypassed. A privileged action cannot silently degrade to the ordinary runner.

## Acceptance
The integration is GREEN only when all existing broker and V4 suites remain green and live Scorp acceptance proves:
1. A V4 `privileged_broker` action executes under `NT AUTHORITY\SYSTEM`.
2. The broker request id is deterministic and bound to task/action/generation identity.
3. Killing/restarting the V4 runner after broker execution but before V4 result persistence does not repeat the privileged effect.
4. Restarting `ScorpPrivilegedBroker` and V4 still reconciles to the original cached result.
5. Reusing task/action with changed envelope is rejected before a new privileged effect.
6. Reusing broker request id with changed broker payload is rejected by the broker.
7. Existing non-privileged V4 action kinds remain non-elevated and retain existing ambiguous-child fail-closed behavior.
8. Broker ambiguous-runner reconciliation is bounded to one durable relaunch attempt.
9. No Codex/local-model invocation is introduced anywhere in executor, runner, client, or broker.

## Stop Conditions
Do not ship if exact DONE replay after expiry cannot be distinguished from never-accepted expiry, if the runner can construct arbitrary broker transport paths, if any existing action kind becomes implicitly elevated, if generation is not part of broker request identity, or if recovery can cause an unbounded or second ambiguous privileged relaunch.