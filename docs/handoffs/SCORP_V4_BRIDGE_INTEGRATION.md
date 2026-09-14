# SCORP V4 bridge integration handoff

This candidate connects the existing ChatGPT GUI transport to the V4 SQLite
transaction core through an explicit seam. It is a candidate integration, not a
production switch. The current Windows scheduled task and its JSON state are
left untouched.

## Candidate location

- Repository: `Scorp96/6`
- Branch: `main`
- Candidate commit: `a135e0cf8c317dcd4f51326bf5b9b71914e007fa`
- Worktree: `C:\ScorpAgent\_publish_git6`
- V4 package: `scorp-agent/master_a_dynamic_v4`
- Bridge seam: `scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py`
- Browser seam: `scorp-agent/chatgpt-gui-bridge/gui_engine.py`

The authoritative state for this seam is a local SQLite database. Legacy JSON
files remain import or read-only compatibility inputs. The default queue is
`Scorp96/666`; the historical `Scorp96/scorp-control-plane` queue is rejected
unless a caller explicitly selects migration mode.

## How a caller uses the seam

Run from the candidate's `scorp-agent` directory with both module directories on
`PYTHONPATH`:

```powershell
$env:PYTHONPATH = 'C:\ScorpAgent\worktrees\v4-transaction-core\scorp-agent;C:\ScorpAgent\worktrees\v4-transaction-core\scorp-agent\chatgpt-gui-bridge'
```

Construct `V4BridgeGateway` with a database path, project ID, allowed worktree
roots, and a browser engine. The engine must provide `auth_state`, `submit`,
and `reconcile`; `ChatGptGuiEngine` adapts the existing async
`gui_transport.run_live_turn` function. The authentication probe is an
explicit dependency. If it is absent or does not report `AUTHENTICATED`, the
SQLite intent is blocked before browser I/O. This prevents a prompt from being
mistaken for proof that a logged-in session exists.

The normal sequence is:

1. `ensure_contract(root_contract, acceptance_contract)` creates or verifies the
   immutable root contract.
2. `enqueue_graph(tasks)` records the dependency graph and path scopes.
3. `claim_workers()` atomically leases at most two runnable assignments.
4. A Worker submits a version-bound structured `WORK_RESULT`; the Master
   independently verifies its identity, content hash and evidence before the
   task becomes `ACCEPTED`. Legacy `HANDOFF` results are migration-only and
   cannot satisfy the new completion gate.
5. `prepare_browser_intent()` persists the prompt intent before any browser
   action.
6. `submit_intent()` routes the intent through the browser adapter. A complete
   response is captured and the outbox is finalized in SQLite; an ambiguous
   submit remains recoverable and cannot be blindly resent.
7. `recover()` reconciles pending browser intents after restart.

The helper methods are intentionally small. They do not let a Worker mutate the
root contract, raise concurrency automatically, bypass authentication, or
declare completion. Completion remains the independent V4 acceptance gate.

## Quick simulated check

The gateway tests use a fake browser engine and verify the local path:
contract -> dependency graph -> two-slot Worker claim -> verified Worker result
-> persisted browser intent -> response capture -> completed outbox. The real
code fixture additionally executes T1/T2/T3 and the independent completion
gate. These tests do not
call GitHub, Windows MCP, Chrome, or a paid model API.

## Legacy compatibility and deprecation boundary

The old `bridge_worker.py` remains available for existing deployments. In the
candidate it now has unique temporary files for concurrent JSON writers,
paginated comment reads, and an observable daemon error callback with an
optional consecutive-failure stop. These fixes reduce legacy risk but do not
make the JSON relay the V4 authority. The old relay must not be described as
SQLite-backed until it is explicitly launched through this seam.

## What is still unverified

- A real Windows logged-in ChatGPT session through `run_live_turn`.
- CAPTCHA, expired login, or address-bar recovery in the candidate seam.
- A production GitHub issue/comment round trip.
- Scheduled-task replacement or production state migration.
- Long-duration unattended stability.

Those checks require a separately authorized canary. This handoff therefore
describes a runnable candidate integration and its tested boundaries, not a
production completion claim.

