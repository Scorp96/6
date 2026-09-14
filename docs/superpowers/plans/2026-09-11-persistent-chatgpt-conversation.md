# Persistent ChatGPT Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse one durable ChatGPT conversation for every continuation of the same task without weakening request binding or exactly-once publication.

**Architecture:** Add an atomic task-to-conversation registry in the bridge state root. The worker passes the saved canonical conversation URL into the GUI transport; the transport launches either the saved URL or a new chat and returns the canonical URL after a completed turn.

**Tech Stack:** Python 3.14, unittest, MCP 2.2.0, windows-mcp 0.8.5, Chrome, Windows Scheduled Tasks.

**Spec:** `docs/superpowers/specs/2026-09-11-persistent-chatgpt-conversation-design.md`

## Global Constraints
- GPT-5.6 Sol remains the sole reasoning controller; no Codex or local model dependency.
- Preserve V2 base64url mutation protocol and deterministic binding.
- Preserve `RESPONSE_CAPTURED`/`POSTED` exactly-once behavior.
- Never silently create a replacement chat when a saved conversation fails.
- Production acceptance creates no diagnostic GPT turns.

---
### Task 1: Conversation-aware GUI transport

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/gui_transport.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_conversation_reuse.py`

**Interfaces:**
- Consumes: optional canonical `conversation_url: str | None`
- Produces: `run_live_turn(prompt, request_id, conversation_url=None)` returning the canonical conversation URL

- [ ] **Step 1:** Add failing tests proving first turn launches `https://chatgpt.com/`, later turn launches the supplied `/c/<id>` URL, and malformed/non-ChatGPT URLs fail before `App` is called.
- [ ] **Step 2:** Run `python -m unittest tests.test_conversation_reuse -v`; expect RED on the missing optional URL behavior.
- [ ] **Step 3:** Add strict canonical URL validation and parameterize acquisition/submission without changing V2 response parsing.
- [ ] **Step 4:** Run the targeted test; expect PASS, then run all bridge tests.
- [ ] **Step 5:** Commit `feat: reuse task ChatGPT conversations`.
### Task 2: Durable task conversation registry

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_conversation_registry.py`

**Interfaces:**
- Produces: atomic `conversation-registry.json` keyed by task ID
- Passes: `conversation_url` into the GUI turn and persists the returned canonical URL

- [ ] **Step 1:** Add failing tests for first-turn persistence, second-request reuse for the same task, restart persistence, and isolation between two task IDs.
- [ ] **Step 2:** Run the targeted tests and verify RED reaches the new assertions.
- [ ] **Step 3:** Implement registry load/atomic save and wire the URL through `obtain_and_publish_decision`; do not invoke GUI for `RESPONSE_CAPTURED` or adopted decisions.
- [ ] **Step 4:** Run targeted and full bridge suites; all historical tests must remain green.
- [ ] **Step 5:** Commit `feat: persist task conversation ownership`.
### Task 3: Deployment and production acceptance

**Files:**
- Modify only if required by test contract: `scorp-agent/chatgpt-gui-bridge/install-bridge.ps1`
- Test: existing bridge suite plus new conversation tests

**Interfaces:**
- Consumes: current production task `gui-bridge-prod-ready-overnight-20260910`
- Produces: installed bridge that reuses the same conversation across two distinct live continuations

- [ ] **Step 1:** Run the complete suite from a clean worktree and verify zero failures.
- [ ] **Step 2:** Inspect diff/status, commit any deployment-contract change, and deploy through `install-bridge.ps1` with the Scheduled Task quiesced.
- [ ] **Step 3:** After the current stale GUI lease expires, start the bridge once and allow only the real pending continuation to run.
- [ ] **Step 4:** Verify its next continuation uses the exact same canonical conversation URL, while request IDs and trusted decisions remain distinct and exactly-once.
- [ ] **Step 5:** Continue the same production task to terminal or a new evidence-backed blocker; run final restart/single-writer/provenance audit before declaring GREEN.