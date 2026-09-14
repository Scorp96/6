# GPT-Native Scorp Executor V4 Design

## Purpose

Replace the Codex-based Windows relay with a deterministic executor controlled entirely by GPT-5.6 Sol. GPT is the only reasoning, planning, coding, debugging, and continuation layer. The Windows host executes explicit structured actions and returns durable evidence; it does not delegate reasoning to another model.

## Architecture

`GPT-5.6 Sol -> GitHub control issue -> Scorp Executor V4 -> deterministic runner -> durable result/evidence -> GPT-5.6 Sol`

The current bootstrap repository remains `Scorp96/666` until V4 and the follow-on Full Auto Orchestrator pass acceptance. Production code is isolated on branch `scorp-gpt-native-v4`; `main` is not modified by V4 development.

## Control Protocol

A runnable issue title starts with `[SCORP_EXEC]`. Its body is a single JSON action envelope with protocol version `scorp.exec/v4`.

Required identity:
- `task_id`
- `action_id`
- GitHub issue number injected by executor
- unique `claim_token` injected by executor

Initial deterministic action kinds:
- `powershell`
- `process`
- `file_read`
- `file_write`
- `file_replace_exact`
- `git`
- `health`

No action kind may invoke Codex. Unknown action kinds fail closed.

## Durable Execution State

The executor stores state under `C:\ScorpAgent\state-v4` and logs under `C:\ScorpAgent\logs-v4`.

State transitions:

`PREPARED -> CLAIM_VERIFIED -> RUNNING -> RESULT_VERIFIED -> TERMINAL`

Before any externally visible or locally side-effecting step, enough durable state must exist to recover after process or machine interruption.

Important ordering invariant:
1. Validate envelope and persist the prepared state.
2. Durably/authoritatively transition the GitHub issue to running.
3. Publish exactly one identity-bound claim and verify it authoritatively.
4. Launch a gated child that cannot execute the action until parent state includes the child identity.
5. Release the start gate only after child PID/start time are durably recorded.
6. The runner writes a durable result before exiting.
7. Executor publishes exactly one identity-bound result, verifies it authoritatively, terminalizes the issue, then clears active state.

## Exactly-Once Rules

- A claim is identified by issue + action_id + claim_token.
- A result is identified by issue + action_id + claim_token.
- Result and claim comment reads use paginated GitHub REST data as authority.
- Comment bodies are written UTF-8 without BOM.
- Publication phase is persisted before a network write.
- After a success or ambiguous network error, the executor searches authoritatively before any retry.
- If publication cannot be resolved authoritatively, block/fail closed instead of reposting blindly.
- Duplicate matching claims/results are incidents and fail closed.
- A new issue must not be able to execute an action_id already proven completed in the durable local action ledger.

## Crash Recovery

- A named per-user/per-machine mutex prevents two executor loops.
- If a recorded child is alive with matching PID/start time, adopt and monitor it.
- If the parent crashed after spawning a gated child but before recording PID, recover the child by its unique gate path, record it, then release the gate.
- If a durable result exists, finalize it without rerunning the action.
- If action outcome is ambiguous and no safe proof exists, block the task and preserve an incident record.
- Transient GitHub failures leave durable state intact for later recovery; they must not convert an already-prepared/running action into a rejected queue item.

## Action Idempotency Ledger

Maintain `C:\ScorpAgent\state-v4\actions\<action-id>.json` for every prepared action. The ledger records action_id, task_id, issue, envelope hash, claim token, phase, result path/hash, and terminal status.

A queued action is rejected if the same action_id already exists with a different envelope hash. If the same action_id is already terminal with the same envelope hash, the executor must not rerun it; it returns/links the existing terminal evidence instead.

## Safety

- `standard` actions are deterministic operations inside approved local roots and normal tooling.
- `approved_admin` requires exact authorization token `USER_APPROVED_FULL_CONTROL`.
- External outreach sends, payments/transfers, account-security changes, destructive deletion, or irreversible production actions remain task-specific approval boundaries outside this executor protocol.
- GPT may create exact PowerShell/process actions only when those actions comply with the task's authorization boundary.

## Deployment

`bootstrap-v4.ps1` is the one-time migration path. It must:
1. Resolve the branch ref once to an immutable commit SHA and fetch all install files from that exact SHA.
2. Verify syntax and self-tests before touching the Scheduled Task.
3. Capture the prior task action and create rollback evidence.
4. Stop the old task only after tests pass.
5. Switch the existing interactive-user Scheduled Task to `executor-v4.1.ps1` without changing its principal/triggers/settings.
6. Verify the new executor process/task is healthy and no Codex process was launched by the switch.
7. Roll back the previous task action if post-switch verification fails.

## Acceptance

V4 is GREEN only when fresh Windows evidence proves:
- syntax check passes for executor and runner;
- all V4 self-tests pass;
- named mutex rejects a second executor instance;
- 3 sequential GPT-authored deterministic actions complete without user `继续`;
- exactly one claim and one result per action;
- restart/recovery does not duplicate an action;
- a repeated action_id does not rerun completed work;
- >=30 seconds quiet after terminalization produces no duplicate result;
- no Codex process is launched by V4.

After V4 GREEN, issue #14 Full Auto Orchestrator becomes the next priority and issue #15 remains the master backlog.