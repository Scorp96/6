# ChatGPT GUI Continuation Bridge V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a production-ready local bridge that turns `GPT_CONTINUATION_REQUIRED` into a fresh GPT-5.6 Sol browser turn and a validated continuation decision without user intervention.

**Architecture:** A Python bridge runs in the interactive Windows session, watches the existing continuation outbox/registry, uses Windows-MCP to control the authenticated Chrome ChatGPT UI, extracts only a terminal structured mutation, binds identity deterministically, and posts exactly one trusted GitHub decision comment. Orchestrator/V4 protocols stay unchanged.

**Tech Stack:** Python 3 via `uv`, `mcp` Python SDK, `windows-mcp==0.8.5`, Windows PowerShell 5.1, GitHub CLI, existing Orchestrator/V4.

**Spec:** `docs/superpowers/specs/2026-09-10-chatgpt-gui-continuation-bridge.md`

## Global Constraints

- GPT-5.6 Sol is the sole reasoning/planning/coding/debugging controller.
- Codex and local reasoning models are prohibited.
- Runtime control repo is `Scorp96/scorp-control-plane`; source/fallback repo remains `Scorp96/666`.
- Only `safety_class=standard` continuation requests are auto-processed.
- Exactly-once behavior is required at request/decision publication boundaries.
- Any ambiguous GUI or identity state fails closed.
- A response is parseable only after ChatGPT streaming is terminal.
- No changes to Orchestrator/V4 behavior unless an integration test proves they are required.

---

### Task 1: Pure continuation binding and response parser

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/bridge_core.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_bridge_core.py`

**Interfaces:**
- Consumes: continuation request JSON, current task JSON, previous result JSON, raw Windows-MCP Snapshot text.
- Produces: `find_chat_editor(snapshot)`, `response_is_terminal(snapshot)`, `extract_mutation(snapshot, request_id)`, `build_bound_decision(request, mutation)`.
- [ ] **Step 1: Write failing parser/binding tests**

Cover: editor coordinate extraction in Chinese/English UI variants; stop-control present means non-terminal; final response marker extraction; malformed/duplicate marker rejection; exact request/task/sequence/generation/result binding; non-standard safety rejection.

- [ ] **Step 2: Run tests and observe RED**

Run: `uv run python -m unittest discover -s scorp-agent/chatgpt-gui-bridge/tests -v`
Expected: failures because `bridge_core.py` does not yet implement the interfaces.

- [ ] **Step 3: Implement minimal pure core**

Use only Python standard library. Model output format is one terminal line:
`SCORP_GUI_MUTATION_V1::<request_id>::<compact-json-mutation>`.
The bridge, not the model, fills all continuation decision binding fields.

- [ ] **Step 4: Run unit tests GREEN**

Run the same unittest command; all tests must pass.

- [ ] **Step 5: Commit Task 1**

Commit message: `feat: add deterministic ChatGPT continuation binding core`.

### Task 2: Windows-MCP Chrome ChatGPT transport

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/gui_transport.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_gui_transport.py`
- Create: `scorp-agent/chatgpt-gui-bridge/canary.py`

**Interfaces:**
- Consumes: rendered controller prompt and unique request id.
- Produces: terminal Snapshot text containing one matching mutation marker, plus conversation URL evidence.

- [ ] **Step 1: Write failing transport-state tests**

Test the state machine with a fake MCP session: new tab, navigation, editor discovery, clipboard restore, terminal polling, timeout, conflicting-response rejection while tolerating identical UI-tree presentation duplicates.

- [ ] **Step 2: Observe RED**

Run the full unittest suite and confirm failures are transport implementation gaps only.
- [ ] **Step 3: Implement the minimal Windows-MCP transport**

Use `uvx --from windows-mcp==0.8.5 windows-mcp serve` and only the required GUI tools. Navigation uses `Ctrl+T`, `Ctrl+L`, clipboard paste, and Enter; prompt entry uses a Snapshot-derived ChatGPT editor coordinate. Save/restore the user's clipboard around prompt submission.

- [ ] **Step 4: Run a harmless live canary**

Open a fresh ChatGPT conversation and submit a fixed mutation canary. Success requires: a new `/c/<id>` conversation URL, terminal response state, one exact response marker, and model response evidence naming GPT-5.6 Sol / High. No project mutation is published during this canary.

- [ ] **Step 5: Commit Task 2**

Commit message: `feat: add Windows-MCP ChatGPT GUI transport`.

### Task 3: Durable worker, publication, and production acceptance

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Create: `scorp-agent/chatgpt-gui-bridge/run-bridge.ps1`
- Create: `scorp-agent/chatgpt-gui-bridge/install-bridge.ps1`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_bridge_worker.py`

**Interfaces:**
- Consumes: `C:\ScorpAgent\orchestrator-v1\continuation-outbox`, matching registry task, previous V4 result evidence, and private GitHub control issue.
- Produces: one `SCORP_CONT_DECISION` comment per request, durable local ledger state, health JSON, and Scheduled Task runtime.

- [ ] **Step 1: Write failing worker tests**

Test pending-request selection, live registry identity checks, expiry, existing-decision adoption, deterministic decision id, no duplicate publication after restart, standard-only safety gate, and fail-closed GUI errors.

- [ ] **Step 2: Observe RED**

Run the complete unittest suite and verify only missing worker behavior fails.

- [ ] **Step 3: Implement minimal worker**

Poll every 10 seconds. Before GUI work, re-read registry and GitHub comments. After GUI output, re-read registry again before publication. Use `gh api` through redirected subprocess capture with explicit timeout; never treat stderr alone as failure. Persist ledger/health as UTF-8 no-BOM via atomic replace.

- [ ] **Step 4: Full local regression GREEN**

Run all unit tests plus Python compile checks and PowerShell parser checks for wrappers/installers.
- [ ] **Step 5: Install rollback-capable Scheduled Task**

Install `ScorpChatGptGuiBridge` as an interactive-user Scheduled Task. Preserve previous task definition if present; verify one bridge process, health file freshness, and no Codex process launched by the bridge.

- [ ] **Step 6: Production A→GPT→B acceptance**

Seed one harmless Orchestrator task whose A action succeeds and requires a new continuation. Do not pre-author B. Require the bridge to consume the generated request, open a fresh ChatGPT conversation, obtain a bound `SET_NEXT_ACTION` mutation, publish exactly one decision, and allow Orchestrator→V4 to execute B to DONE without user input.

Acceptance evidence must prove:
- A result predates B creation.
- one continuation request issue;
- one bound decision comment;
- one B execution issue / one claim / one result;
- bridge restart during an in-flight or already-published request creates no duplicate decision;
- final registry has READY=0 and RUNNING=0;
- Orchestrator health is IDLE with no tick error;
- bridge health is IDLE/READY and fresh;
- no Codex invocation attributable to the bridge.

- [ ] **Step 7: Commit, push, and durable checkpoint**

Commit message: `feat: add unattended ChatGPT GUI continuation bridge`.
Push only the isolated feature branch. Record accepted release SHA and production evidence in `Scorp96/scorp-control-plane#1`. Do not alter unrelated backlog items.

## Completion Gate

The project is GREEN only when the production A→GPT→B test passes without the user opening a chat, typing `继续`, copying output, or repairing state. If Chrome/ChatGPT UI is unavailable or locked, the bridge must remain safely WAITING and retry later; it must never synthesize a decision locally.
