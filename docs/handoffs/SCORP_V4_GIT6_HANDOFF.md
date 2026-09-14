# SCORP V4 GitHub repository handoff

This file adapts the local candidate handoff to repository `Scorp96/6`.

## Source of truth for this repository

- Repository: `Scorp96/6`
- Start file: `GPT_START_HERE.md`
- V4 package: `scorp-agent/master_a_dynamic_v4/`
- Browser bridge: `scorp-agent/chatgpt-gui-bridge/`
- Offline validation: `scripts/run-candidate-validation.ps1`
- Default queue: `Scorp96/666`
- Legacy queue: `Scorp96/scorp-control-plane`, migration-only
- Current candidate: `e6612a994c0b094e00669a9e5854c3dfa2df4edb`
- Reasoning controller: **GPT-5.6 Sol**

The original local candidate was developed in `Scorp96/666`. This repository is
a source-and-runbook copy for another GPT to inspect and run. It does not carry
the local Windows runtime, browser profile, cookies, SQLite state, or scheduled
task registration.

## Runtime boundary

The existing bridge can control an already logged-in ChatGPT web session through
Windows MCP or Chrome Use. The V4 gateway adds SQLite state, dynamic two-slot
Worker scheduling and browser ambiguity recovery. The gateway is candidate code;
the production scheduled task is not changed by this repository.

The current candidate also validates structured, version-bound Worker results and
the real CSV workload fixture: T1 and T2 run in parallel, T3 waits for both, and
the independent completion validator returns `PASS`. Broker recovery and the
bridge functional heartbeat are covered by the candidate validation script.

## Acceptance language

Offline tests demonstrate code behavior only. Real browser evidence must be
collected from a fresh, harmless canary with a logged-in session. A CAPTCHA,
expired login, unavailable Windows interactive session, or ambiguous submit is
`BLOCKED`, not `PASS`. Historical files under
`docs/handoffs/historical-2026-09-14/` are retained for provenance and do not
prove this GitHub copy has been installed or run on a particular machine.

The fresh machine-readable record is
`docs/handoffs/SCORP_V4_GIT6_VALIDATION.json`. It is bound to the candidate
commit above. It explicitly records browser canary, GitHub write, production
cutover, and long-duration stability as unverified where no current evidence
exists.
