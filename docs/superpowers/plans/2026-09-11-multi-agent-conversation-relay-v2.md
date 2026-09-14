# Multi-Agent Conversation Relay V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing single-writer ChatGPT GUI Bridge so one persistent A controller conversation can dispatch bounded work to persistent B/C worker conversations, workers can return structured handoffs to A, and the cycle can continue without user `continue` messages.

**Architecture:** Keep one GUI Bridge process as the sole Chrome automation writer. Add a role-aware persistent conversation registry and a durable relay state machine. A may emit only controller relay decisions; B/C may emit only worker handoffs. The relay opens the target role's saved ChatGPT conversation, captures a bound structured response, persists it before forwarding, and then routes the next turn back to A. Legacy continuation requests remain supported unchanged.

**Tech Stack:** Python 3.14, unittest, MCP 2.2.0, windows-mcp 0.8.5, Chrome, Windows Scheduled Tasks, existing Scorp Orchestrator/V4 durable state.

**Spec:** `docs/superpowers/specs/2026-09-11-multi-agent-orchestrator-v2-design.md`

## Global Constraints
- GPT-5.6 Sol remains the sole reasoning controller; no Codex or local model dependency.
- Exactly one GUI Bridge process may drive Chrome.
- Existing V2 continuation parsing, task-to-conversation reuse, `RESPONSE_CAPTURED`/`POSTED` semantics and legacy single-worker tasks must remain green.
- A is the only dispatch authority. B/C cannot publish V4 actions or mutate mission authority fields.
- Every relay turn is bound to `mission_id`, `turn_id`, `generation`, `from_role`, `to_role`, and the relevant objective/root hashes.
- A saved role conversation failure must fail closed; never silently create a replacement conversation.
- Relay outputs are durable before the next role is opened.
- Routine A↔B/C continuation must not require a user message.

---

### Task 1: Generic structured role-turn transport

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/gui_transport.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/bridge_core.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_role_turn_transport.py`

**Interfaces:**
- Produces: `render_role_prompt(envelope: dict) -> str`
- Produces: `extract_role_response(snapshot: str, turn_id: str) -> dict`
- Produces: `run_role_turn(prompt: str, turn_id: str, conversation_url: str | None = None) -> tuple[dict, str, str | None]`
- Marker: `SCORP_GUI_ROLE_V1::<turn_id>::<unpadded-base64url-compact-json>`

- [ ] **Step 1:** Add failing tests proving exact turn-ID binding, malformed base64 rejection, wrong turn-ID rejection, first-turn `https://chatgpt.com/` launch, and reuse of a supplied canonical `/c/<id>` URL.
- [ ] **Step 2:** Run `python -m unittest tests.test_role_turn_transport -v`; expected RED because role-turn helpers do not exist.
- [ ] **Step 3:** Implement marker parsing by reusing the existing strict base64url/terminal-response patterns without changing `SCORP_GUI_MUTATION_V2` behavior.
- [ ] **Step 4:** Run targeted tests and the full bridge suite; all legacy mutation tests must remain green.
- [ ] **Step 5:** Commit `feat: add structured role chat turns`.

### Task 2: Persistent mission-role conversation ownership

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_role_conversation_registry.py`

**Interfaces:**
- Extend `ConversationRegistry` with role keys `mission_id:A`, `mission_id:B`, `mission_id:C` while preserving legacy task-ID keys.
- Produces: `get_role_url(mission_id: str, role: str) -> str | None`
- Produces: `record_role(mission_id: str, role: str, turn_id: str, conversation_url: str) -> str`

- [ ] **Step 1:** Add failing tests for independent A/B/C URLs, restart persistence, same-role reuse, and rejection of roles outside `{A,B,C}`.
- [ ] **Step 2:** Run targeted tests and verify RED reaches missing role registry methods.
- [ ] **Step 3:** Implement role-key persistence using the existing atomic JSON writer and strict canonical URL validation.
- [ ] **Step 4:** Run targeted tests plus existing conversation registry/reuse tests.
- [ ] **Step 5:** Commit `feat: persist mission role conversations`.

### Task 3: Durable A↔B/C relay state machine

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/role_relay.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_role_relay.py`

**Interfaces:**
- Consumes files from `<bridge_root>/role-relay-outbox/*.json` with protocol `scorp.gui-role-relay/turn-v1`.
- Persists `<bridge_root>/role-relay-ledger.json` and `<bridge_root>/role-relay-inbox/<turn_id>.json` atomically.
- A response kinds: `DISPATCH`, `WAIT`, `TERMINAL`.
- Worker response kinds: `HANDOFF`, `BLOCKER`.
- `DISPATCH.assignments[*].worker_slot` is only `B` or `C`.
- B/C responses must echo the bound mission/generation/objective hashes through deterministic envelope binding; free-form fields cannot replace binding fields.

- [ ] **Step 1:** Add failing tests proving A→B→A alternation, A→C→A alternation, B/C cannot emit `DISPATCH`, duplicate identical turn replay is idempotent, conflicting duplicate fails closed, stale generation rejects, and response is durably persisted before a return turn is queued.
- [ ] **Step 2:** Run `python -m unittest tests.test_role_relay -v`; expected RED because `role_relay` does not exist.
- [ ] **Step 3:** Implement `RoleRelayRuntime.run_once()` with states `PENDING -> GUI_INFLIGHT -> RESPONSE_CAPTURED -> ROUTED`, retry/backoff matching the legacy bridge pattern, and deterministic child turn IDs derived from mission/role/generation/parent-turn hash.
- [ ] **Step 4:** Run targeted tests and restart/replay tests.
- [ ] **Step 5:** Commit `feat: add durable controller worker relay`.

### Task 4: Integrate relay into the single GUI Bridge writer loop

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Modify only if required: `scorp-agent/chatgpt-gui-bridge/run-bridge.ps1`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_bridge_role_relay_runtime.py`

**Interfaces:**
- `BridgeRuntime.run_once()` processes at most one GUI mutation per poll.
- Pending role relay work is selected deterministically alongside legacy continuation work without running two Chrome drivers concurrently.
- Legacy continuation behavior and health file remain backward-compatible.

- [ ] **Step 1:** Add failing tests that one poll never invokes both GUI paths, relay work uses the mission-role conversation URL, legacy work still uses task conversation URL, and restart preserves captured relay responses.
- [ ] **Step 2:** Verify RED.
- [ ] **Step 3:** Wire `RoleRelayRuntime` into the existing bridge process; do not create a second Scheduled Task or daemon.
- [ ] **Step 4:** Run the complete GUI Bridge test suite.
- [ ] **Step 5:** Commit `feat: run role relay under single gui writer`.

### Task 5: Production canary — real A↔B↔A lifecycle

**Files:**
- Test/deploy existing `scorp-agent/chatgpt-gui-bridge/install-bridge.ps1`
- No production mission mutation until the offline suite is green.

**Interfaces:**
- Seed one standard-safety synthetic mission with fixed A and B roles.
- A gets a bounded synthetic assignment request and must return `DISPATCH` to B.
- B receives it in its own persistent ChatGPT conversation and returns `HANDOFF`.
- The same bridge then opens A's original conversation and presents the handoff.
- A returns the next generation or `TERMINAL`.

- [ ] **Step 1:** Run the complete clean-worktree suite and verify zero failures.
- [ ] **Step 2:** Deploy the exact tested commit through the rollback-safe bridge installer with the current bridge task quiesced.
- [ ] **Step 3:** Run a synthetic A→B→A canary and verify two distinct canonical conversation URLs are created, persisted, and reused; no user message occurs between turns.
- [ ] **Step 4:** Run A→B/C parallel-planning canary with disjoint synthetic scopes but serialized GUI turns; verify both handoffs return to A and A remains sole dispatch authority.
- [ ] **Step 5:** Verify single GUI writer, no Codex/local-model process, legacy continuation regression, exact role URL reuse, stale-generation rejection, and durable restart recovery before enabling it for real Social/CBI missions.
