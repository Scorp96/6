# Scorp GPT-Native Executor V4

## Decision

GPT-5.6 Sol is the sole reasoning, planning, coding and debugging controller. Windows is a deterministic executor and evidence collector. V4 MUST NOT invoke Codex, `codex exec`, Codex CLI, or the retired natural-language relay.

## Authoritative runtime

The production candidate is the hardened V4.1 line:

- parent: `scorp-agent/executor-v4.1.ps1`
- child: `scorp-agent/runner-v4.1.ps1`
- envelope schema: `scorp-agent/executor-v4.schema.json`
- release bootstrap: `scorp-agent/bootstrap-v4.ps1`
- release manifest: `scorp-agent/release-manifest-v4.json`
- regression gates: `scorp-agent/tests/v4-critical-hardening.ps1` and `scorp-agent/tests/v4-production-hardening.ps1`

The legacy `executor-v4.ps1`, `runner-v4.ps1`, and Codex relay are not production V4.1 entrypoints.

A parallel `V4.2` implementation was created earlier from the pre-hardening `c42d2f4` baseline. Static review showed that it reintroduced weak child identity, CIM-discovery fail-open behavior, incomplete result identity, unbounded native output, and unbounded `file_read`. Its useful ideas were forward-ported into hardened V4.1: trusted-comment author verification, envelope-hash ledger binding, and exact `GO` start-gate semantics. The V4.2 working-tree files were removed; their Git history remains available as evidence. The numeric `4.2` label therefore does not supersede this hardened release candidate.

## Queue protocol

One GitHub issue represents one deterministic action.

- queued: `[SCORP_EXEC] <task_id> <action_id>`
- running: `[SCORP_EXEC_RUNNING] <task_id> <action_id>`
- success: `[SCORP_EXEC_DONE] <task_id> <action_id>`
- deterministic failure / timeout / failed precondition: `[SCORP_EXEC_FAILED] <task_id> <action_id>`
- blocked / ambiguous: `[SCORP_EXEC_BLOCKED] <task_id> <action_id>`
- issue body: pure JSON matching `executor-v4.schema.json`
- trusted issue author: `Scorp96`

Before a queued issue is changed to RUNNING, the parent persists durable PREPARED state locally. The remote title transition is expected-title guarded and verified. Restart recovery resumes the durable stage rather than reconstructing state from chat memory.

## Identity and exactly-once contract

The execution identity is the tuple:

`(task_id, issue_number, action_id, claim_token)`

Both claim and result lifecycle records carry the full tuple. A result begins with:

```text
SCORP_EXEC_RESULT
Protocol: scorp.exec/v4
Issue: <number>
TaskId: <task_id>
ActionId: <action_id>
ClaimToken: <claim_token>
Status: <status>
```

The parent performs authoritative paginated issue-comment reads before and after publication. Only lifecycle comments authored by the trusted GitHub identity `Scorp96` are eligible as claim/result/block/rejection evidence. Local finalization and remote result matching verify the full identity tuple. Duplicate claims/results fail closed.

A durable global action ledger is keyed by `task_id + action_id` and additionally binds the SHA-256 of the exact queued issue body. Reusing the same task/action identity from another issue, or changing the envelope under an already-reserved task/action identity, is rejected instead of executing twice. Terminal ledger state is retained after active state is cleared.

## Execution model

The parent never interprets natural-language tasks. It validates the structured envelope, hashes and reserves the action identity, persists PREPARED state, verifies the GitHub RUNNING transition, publishes exactly one claim, and only then permits the child to execute through a local start gate.

The child proceeds only when the gate file contains exactly `GO`; mere file existence is not authorization.

Child adoption is fail-closed. A recorded/adopted child must match authoritative `Win32_Process` evidence for PID, PowerShell executable, `runner-v4.1.ps1`, gate path, envelope path, result path and recorded start time. CIM/process-discovery failure is an ambiguity incident, not evidence that zero children exist. The runner records its PID/start time in result evidence, and normal result finalization cross-checks the returned runner PID against the recorded child PID.

Timeout kill is permitted only after the same authoritative child-identity check. The runner writes a durable JSON result before the parent publishes a lifecycle result.

## Initial action kinds

- `powershell`: exact script supplied by GPT.
- `process`: exact executable plus argv.
- `file_read`: bounded streaming read plus size/hash metadata.
- `file_write`: atomic exact-content write with optional SHA precondition.
- `file_replace_exact`: exact old/new replacement with match-count and optional SHA precondition; files larger than 16 MiB fail precondition instead of being loaded wholesale.
- `git`: exact Git argv in an explicit approved repository directory.
- `health`: deterministic local telemetry.

Native stdout/stderr are captured to local files and only bounded tails are returned in result evidence. Large `file_read` actions do not load the whole target file into memory.

## Blocked and rejection recovery

Blocked execution remains in durable `BLOCKED_PENDING_REMOTE` state until a structured block incident, blocked GitHub title, and terminal issue state are authoritatively verified. Active state is not cleared on a failed remote transition.

Malformed queued issues use a durable rejection ledger. Ambiguous comment publication is never blindly reposted. Rejection is not complete until its remote terminal state is verified. If active execution state already exists for an issue, executor failures are handled by active-state recovery/blocking and are not converted into a normal queued-issue rejection.

## Safety

The executor accepts only known action kinds and required fields. Unknown/stale actions, failed preconditions, corrupt ledgers and ambiguous remote/local identity fail closed. `approved_admin` requires `authorization: USER_APPROVED_FULL_CONTROL`.

Higher-level policy remains GPT-side: purchases/transfers, account-security changes, destructive deletion, external outreach sending and irreversible production changes require task-specific authorization.

## Release and deployment

Development remains isolated on `scorp-gpt-native-v4` until Windows acceptance is GREEN.

`bootstrap-v4.ps1` requires an exact 40-character commit SHA. It fetches the release manifest and runtime files from that pinned commit, verifies Git blob identity and records SHA-256 evidence, parses all PowerShell with Windows PowerShell 5.1, runs executor/self/regression/runner-health gates, verifies that `ScorpComputerAgent` belongs to the current interactive user, backs up the complete Scheduled Task XML, then performs the switch.

If any post-stop/switch verification fails, bootstrap restores the prior task XML and prior install directory. Successful switching additionally requires the Scheduled Task to be Running, its action to point to the pinned V4.1 executor, the interactive principal to remain unchanged, the production named mutex to deny a second executor, and no new Codex process to appear.

If no direct GPT-to-Windows execution channel is healthy, the user should receive at most one pinned bootstrap command once the GitHub release candidate is ready.

## Acceptance gate

V4 is production-usable only after fresh Windows PowerShell 5.1 evidence proves all of the following:

1. release bootstrap/self/regression/health gates pass against one pinned commit;
2. no Codex process is launched by V4;
3. three sequential GPT-directed deterministic actions complete without user `继续`;
4. exactly one trusted-author claim and one trusted-author result exist per action;
5. restart during RUNNING does not rerun an already-started action;
6. a second executor instance is denied by the production named mutex;
7. timeout terminates only the authoritatively identified recorded child tree;
8. at least 30 seconds of quiet observation after completion creates no duplicate result;
9. active local state is cleared only after terminal GitHub state is verified;
10. rollback is demonstrated or otherwise verified safe before #16 is closed GREEN.

Only after this gate is GREEN may #16 be closed and #14 Full Auto Orchestrator become active. #15 remains the master backlog.
