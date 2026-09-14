# Scorp P0 ChatGPT Transport Bake-off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select and prove a browser-native ChatGPT Web execution transport for Scorp without changing the deterministic control-plane semantics or touching authoritative production state.

**Architecture:** Keep `ProjectStateStore`, deterministic D, Master transition CAS/replay logic, Worker guard, scheduler, Worker conversation pool, and `DurableActorTransportV3` unchanged. First attempt a thin adapter over `chatgpt-use` request receipts/conversation records because it already implements fail-closed `duplicate`, `detached`, and `submission_unknown` semantics on top of `chrome-use`; only if that path fails the Windows/exact-conversation gate, implement a Scorp-native `chrome-use` driver behind the existing `ActorGuiBackendV3` contract. All live tests run against an isolated PoC root through the existing GPT-Native Executor V4.1 GitHub→Windows channel, never through RDC and never through the installed production V3 root.

**Tech Stack:** Python 3.11+ logic tests, existing Scorp V3 Python runtime, `unittest`, GitHub Actions, GPT-Native Executor V4.1, `chatgpt-use`/`chrome-use` as external browser-native candidates, Windows + authenticated Chrome for live acceptance.

**Spec:** `docs/superpowers/specs/2026-09-12-scorp-p0-transport-bakeoff-design.md`

## Global Constraints

- Baseline implementation lineage is `feature/d-lifecycle-preemption-v3` from `5e5fdc7ef82aa244d5f29c7468bed56abdc78d57`; the spec/plan commits may advance the branch, but no implementation may silently rebase onto older code.
- Do not clear, overwrite, migrate, or repurpose `C:\ScorpAgent\state-v3\active`.
- Do not mutate the active `production-e2e-g274` project during P0.
- Do not enable the disabled production watchdog during P0.
- Do not replace the installed production bridge during P0.
- Use isolated local PoC root `C:\ScorpAgent\p0-transport-bakeoff` and isolated browser/transport state.
- Preserve Scorp root-objective hash, acceptance hash, `MASTER_IDENTITY=A`, project-state CAS, Worker admission guard, and terminal fencing.
- Workers never mutate authoritative project state.
- `DurableActorTransportV3` remains the authority for exactly-once intent/recovery semantics.
- A candidate that cannot prove exact conversation identity or safe recovery fails closed and is not promoted.
- The live acceptance gate is 20 consecutive lifecycle cycles with 0 wrong-conversation submissions, 0 duplicate submissions, 0 manual continuation actions, 0 unreconciled identity changes, and 0 terminal transport residue.
- No production migration occurs in this plan.

---

### Task 1: Establish an isolated P0 branch and upstream capability gate

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/p0_upstream_probe.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_p0_upstream_probe.py`
- Create: `.github/workflows/scorp-p0-transport-ci.yml`

**Interfaces:**
- Consumes: Windows PATH and the external commands `chatgpt-use`, `chrome-use` when present.
- Produces: `probe_environment() -> dict` with exact executable/version/health evidence and a deterministic `candidate_b_mode` value: `CHATGPT_USE`, `CHROME_USE_DIRECT`, or `BLOCKED`.

- [ ] **Step 1: Write the failing probe tests**

```python
import unittest
from p0_upstream_probe import classify_candidate_b


class P0UpstreamProbeTests(unittest.TestCase):
    def test_prefers_chatgpt_use_when_receipts_and_browser_backend_are_available(self):
        evidence = {
            "chatgpt_use": {"present": True, "request_receipts": True, "conversation_record": True},
            "chrome_use": {"present": True, "windows": True},
        }
        self.assertEqual("CHATGPT_USE", classify_candidate_b(evidence))

    def test_falls_back_to_direct_chrome_use_when_chatgpt_use_is_not_windows_usable(self):
        evidence = {
            "chatgpt_use": {"present": False, "request_receipts": False, "conversation_record": False},
            "chrome_use": {"present": True, "windows": True},
        }
        self.assertEqual("CHROME_USE_DIRECT", classify_candidate_b(evidence))

    def test_blocks_when_no_browser_native_candidate_is_proven(self):
        evidence = {
            "chatgpt_use": {"present": False, "request_receipts": False, "conversation_record": False},
            "chrome_use": {"present": False, "windows": False},
        }
        self.assertEqual("BLOCKED", classify_candidate_b(evidence))
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run from `scorp-agent/chatgpt-gui-bridge`:

```text
python -m unittest -v tests.test_p0_upstream_probe
```

Expected: import failure because `p0_upstream_probe.py` does not exist.

- [ ] **Step 3: Implement the minimal deterministic classifier and version probe**

```python
from __future__ import annotations

import json
import shutil
import subprocess


def classify_candidate_b(evidence: dict) -> str:
    chatgpt = evidence.get("chatgpt_use") or {}
    chrome = evidence.get("chrome_use") or {}
    if (
        chatgpt.get("present") is True
        and chatgpt.get("request_receipts") is True
        and chatgpt.get("conversation_record") is True
        and chrome.get("present") is True
        and chrome.get("windows") is True
    ):
        return "CHATGPT_USE"
    if chrome.get("present") is True and chrome.get("windows") is True:
        return "CHROME_USE_DIRECT"
    return "BLOCKED"


def _version(executable: str) -> dict:
    path = shutil.which(executable)
    if not path:
        return {"present": False, "path": None, "version": None}
    cp = subprocess.run([path, "--version"], text=True, capture_output=True, timeout=20)
    return {
        "present": cp.returncode == 0,
        "path": path,
        "version": (cp.stdout or cp.stderr or "").strip(),
        "returncode": cp.returncode,
    }


def probe_environment() -> dict:
    chatgpt = _version("chatgpt-use")
    chrome = _version("chrome-use")
    # Upstream capability flags are conservative: live validation may only
    # promote them from False to True after the corresponding CLI behavior
    # is observed on this machine.
    chatgpt.update({"request_receipts": False, "conversation_record": False})
    chrome.update({"windows": bool(chrome.get("present"))})
    evidence = {"chatgpt_use": chatgpt, "chrome_use": chrome}
    evidence["candidate_b_mode"] = classify_candidate_b(evidence)
    return evidence


if __name__ == "__main__":
    print(json.dumps(probe_environment(), ensure_ascii=False, sort_keys=True))
```

The live executor action later updates only evidence, not source logic, after probing `chatgpt-use status`/request-receipt behavior.

- [ ] **Step 4: Add isolated CI for portable logic**

Create `.github/workflows/scorp-p0-transport-ci.yml`:

```yaml
name: Scorp P0 Transport CI

on:
  push:
    branches:
      - p0/chatgpt-transport-bakeoff
  workflow_dispatch:

jobs:
  logic:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install existing runtime dependency
        run: python -m pip install --disable-pip-version-check 'mcp==2.2.0'
      - name: Compile
        run: python -m compileall -q scorp-agent/chatgpt-gui-bridge
      - name: P0 targeted tests
        working-directory: scorp-agent/chatgpt-gui-bridge
        run: python -m unittest -v tests.test_p0_upstream_probe
      - name: Existing full regression
        working-directory: scorp-agent/chatgpt-gui-bridge
        run: python -m unittest discover -s tests -p 'test_*.py' -v
```

- [ ] **Step 5: Run targeted + full portable regression and commit**

```text
python -m unittest -v tests.test_p0_upstream_probe
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q .
git diff --check
```

Expected: all PASS.

Commit:

```text
test: add P0 browser transport capability gate
```

---

### Task 2: Implement Candidate B1 as a thin `chatgpt-use` receipt adapter

**Execution gate:** Execute this task only if the Windows probe proves `chatgpt-use` can run on this machine and its request-receipt/status behavior is available. If that gate fails, record `B1_GATE_FAILED` evidence and proceed to Task 3 without changing Scorp semantics.

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/chatgpt_use_actor_backend_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chatgpt_use_actor_backend_v3.py`
- Test existing: `scorp-agent/chatgpt-gui-bridge/tests/test_durable_actor_transport_v3.py`

**Interfaces:**
- Consumes: `chatgpt-use ask ... --request-id <submission_id> --busy fail` and `chatgpt-use status <submission_id>` JSON results.
- Produces the exact backend contract already consumed by `DurableActorTransportV3`:
  - `submit(submission_id, *, prompt, turn_id, actor_kind, conversation_url, timeout_seconds) -> dict`
  - `poll(handle, *, turn_id, actor_kind, timeout_seconds) -> dict`
  - `recover(submission_id, *, turn_id, actor_kind) -> dict | None`

- [ ] **Step 1: Write failing receipt-mapping tests**

```python
import asyncio
import unittest
from chatgpt_use_actor_backend_v3 import ChatGptUseActorBackendV3


class FakeCli:
    def __init__(self):
        self.ask_result = None
        self.status_result = None
        self.ask_calls = []

    async def ask(self, **kwargs):
        self.ask_calls.append(kwargs)
        return dict(self.ask_result)

    async def status(self, request_id):
        return dict(self.status_result)


class ChatGptUseActorBackendV3Tests(unittest.TestCase):
    def test_submitted_receipt_returns_conversation_bound_handle(self):
        cli = FakeCli()
        cli.ask_result = {
            "status": "submitted",
            "request_id": "actor-submit-1",
            "conversation_id": "abc123",
        }
        backend = ChatGptUseActorBackendV3(cli)
        result = asyncio.run(backend.submit(
            "actor-submit-1", prompt="P", turn_id="turn-1", actor_kind="MASTER",
            conversation_url="https://chatgpt.com/c/abc123", timeout_seconds=120,
        ))
        self.assertEqual("https://chatgpt.com/c/abc123", result["conversation_url"])
        self.assertEqual(1, len(cli.ask_calls))

    def test_submission_unknown_is_never_classified_retryable(self):
        cli = FakeCli()
        cli.ask_result = {"status": "submission_unknown", "request_id": "actor-submit-1"}
        backend = ChatGptUseActorBackendV3(cli)
        with self.assertRaisesRegex(RuntimeError, "ACTOR_BACKEND_SUBMISSION_UNKNOWN"):
            asyncio.run(backend.submit(
                "actor-submit-1", prompt="P", turn_id="turn-1", actor_kind="WORKER",
                conversation_url=None, timeout_seconds=120,
            ))

    def test_recover_detached_receipt_never_resubmits(self):
        cli = FakeCli()
        cli.status_result = {
            "status": "detached",
            "request_id": "actor-submit-1",
            "conversation_id": "conv-7",
        }
        backend = ChatGptUseActorBackendV3(cli)
        handle = asyncio.run(backend.recover("actor-submit-1", turn_id="turn-1", actor_kind="WORKER"))
        self.assertEqual("https://chatgpt.com/c/conv-7", handle["conversation_url"])
        self.assertEqual([], cli.ask_calls)
```

- [ ] **Step 2: Run tests and verify RED**

```text
python -m unittest -v tests.test_chatgpt_use_actor_backend_v3
```

Expected: module missing.

- [ ] **Step 3: Implement strict receipt/status mapping**

Implementation rules:

```python
SUBMITTED = {"submitted", "completed", "detached"}
AMBIGUOUS = {"submission_unknown"}
SAFE_NO_SEND = {"failed"}  # only when receipt explicitly says submitted == "no"
```

Canonical conversion:

```python
def conversation_url(conversation_id: str) -> str:
    value = str(conversation_id or "").strip()
    if not value:
        raise ValueError("ACTOR_BACKEND_CONVERSATION_ID_MISSING")
    return f"https://chatgpt.com/c/{value}"
```

Hard rules:

```text
- supplied conversation_url must match receipt conversation_id when both exist;
- `submission_unknown` raises `ACTOR_BACKEND_SUBMISSION_UNKNOWN` and must flow to Scorp ambiguity handling, never RETRYABLE;
- `duplicate` is accepted only by reading status for the same request id; it never sends again;
- `failed` is RETRYABLE only when upstream evidence explicitly says no submission occurred;
- `recover()` calls status only; it never calls ask;
- completed text is not authoritative unless upstream says the conversation record reached terminal/end_turn state.
```

- [ ] **Step 4: Run backend + DurableActorTransport regression**

```text
python -m unittest -v \
  tests.test_chatgpt_use_actor_backend_v3 \
  tests.test_durable_actor_transport_v3
```

Expected: PASS, including existing zero-duplicate crash recovery tests.

- [ ] **Step 5: Commit B1 adapter**

```text
feat: adapt chatgpt-use receipts to Scorp actor transport
```

---

### Task 3: Implement Candidate B2 direct `chrome-use` driver only if B1 fails the Windows/exact-target gate

**Execution gate:** Skip this task if Task 2 passes live exact-conversation and recovery tests. If skipped, record `B2_NOT_NEEDED` in the P0 evidence bundle.

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/chrome_use_cli_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/chrome_use_actor_driver_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chrome_use_cli_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chrome_use_actor_driver_v3.py`
- Test existing: `scorp-agent/chatgpt-gui-bridge/tests/test_actor_gui_backend_v3.py`

**Interfaces:**
- `ChromeUseCliV3.run_json(session: str, *args: str, timeout_seconds: int) -> object`
- `ChromeUseActorDriverV3.submit_prompt(...) -> str`
- `ChromeUseActorDriverV3.snapshot_conversation(conversation_url, *, window_handle=None) -> str`
- `ChromeUseActorDriverV3.discover_turn_conversations(turn_id) -> list[dict]`

- [ ] **Step 1: Write the failing CLI wrapper tests**

```python
import asyncio
import unittest
from chrome_use_cli_v3 import ChromeUseCliV3


class ChromeUseCliV3Tests(unittest.TestCase):
    def test_run_json_binds_explicit_session_and_rejects_invalid_json(self):
        calls = []
        async def runner(argv, timeout_seconds):
            calls.append(argv)
            return 0, '{"ok":true}', ''
        cli = ChromeUseCliV3(runner=runner)
        result = asyncio.run(cli.run_json("scorp-a-123", "status", timeout_seconds=10))
        self.assertEqual({"ok": True}, result)
        self.assertIn("scorp-a-123", calls[0])
```

- [ ] **Step 2: Write failing driver identity tests**

```python
class FakeChromeUseCli:
    async def open(self, session, url):
        return {"url": url}
    async def eval(self, session, script):
        return {
            "href": "https://chatgpt.com/c/a1",
            "text": "TURN_ID=turn-1",
            "streaming": False,
        }


def test_existing_conversation_never_silently_changes_target():
    driver = ChromeUseActorDriverV3(FakeChromeUseCli())
    snapshot = asyncio.run(driver.submit_prompt(
        prompt="TURN_ID=turn-1", turn_id="turn-1", actor_kind="MASTER",
        conversation_url="https://chatgpt.com/c/a1",
    ))
    assert "https://chatgpt.com/c/a1" in snapshot
```

Also add explicit tests for:

```text
- canonical target mismatch -> ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH;
- discovery with zero matches -> [];
- discovery with one turn marker -> one URL;
- multiple matches are returned so ActorGuiBackendV3 fails closed as ambiguous;
- no method closes or mutates a non-owned tab on mismatch.
```

- [ ] **Step 3: Implement a strict CLI wrapper**

Use `asyncio.create_subprocess_exec` only inside the default runner; tests inject a fake runner. Every command must include an explicit Scorp session and `--json`. Nonzero exit -> `CHROME_USE_COMMAND_FAILED`; invalid JSON -> `CHROME_USE_INVALID_JSON`; timeout -> `CHROME_USE_TIMEOUT`.

Do not auto-install or auto-upgrade binaries from inside the driver.

- [ ] **Step 4: Implement browser-native driver with record-first semantics**

The driver may use the page only to send/reacquire. It must not treat DOM quiescence or rendered markdown as authoritative completion evidence. If direct server conversation-record access cannot be implemented safely on Windows, Candidate B2 fails rather than downgrading Scorp to lossy DOM completion.

Deterministic session names:

```python
def session_name(actor_kind: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"scorp-{actor_kind.lower()}-{digest}"
```

Exact targeting rule:

```text
known conversation_url -> attach/open that exact canonical URL -> verify page/server conversation id -> submit;
new conversation -> create once -> obtain canonical conversation id -> persist it through existing Scorp handle/ledger;
restart -> enumerate only Scorp-owned sessions/tabs -> search the exact turn marker -> never submit during discovery.
```

- [ ] **Step 5: Run driver/backend regression and commit**

```text
python -m unittest -v \
  tests.test_chrome_use_cli_v3 \
  tests.test_chrome_use_actor_driver_v3 \
  tests.test_actor_gui_backend_v3 \
  tests.test_durable_actor_transport_v3
```

Expected: PASS.

Commit:

```text
feat: add direct chrome-use Scorp actor driver fallback
```

---

### Task 4: Add transport conformance and fault-injection tests against existing Scorp semantics

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_browser_transport_conformance_v3.py`
- Modify only if a proven incompatibility exists: `scorp-agent/chatgpt-gui-bridge/actor_gui_backend_v3.py`
- Test existing: `tests/test_durable_actor_transport_v3.py`
- Test existing: `tests/test_worker_conversation_pool_v3.py`
- Test existing: `tests/test_parallel_master_worker_relay_v3.py`

**Interfaces:**
- Consumes either B1 or B2 backend behind existing `DurableActorTransportV3`.
- Produces a shared conformance verdict for Scorp scenarios 1–9 before any live soak.

- [ ] **Step 1: Write a reusable backend contract fixture**

Required assertions:

```python
CONTRACT_CASES = (
    "existing_conversation_target_preserved",
    "unique_recovery_without_resubmit",
    "zero_match_recovery_fails_closed",
    "multiple_match_recovery_fails_closed",
    "post_submit_crash_zero_duplicate",
    "known_pre_submit_failure_only_retryable_with_no_send_evidence",
    "stale_target_reacquires_by_canonical_identity",
)
```

- [ ] **Step 2: Prove existing Scorp tests remain unchanged**

Run:

```text
python -m unittest -v \
  tests.test_durable_actor_transport_v3 \
  tests.test_worker_conversation_pool_v3 \
  tests.test_parallel_master_worker_relay_v3
```

Expected: PASS without modifying ProjectState/D/Worker guard/scheduler/pool semantics.

- [ ] **Step 3: Add explicit crash-point injection**

The test must reproduce this sequence:

```text
Scorp writes SUBMITTING
-> backend proves remote request submitted
-> simulated local exception before Scorp stores returned handle
-> new backend/transport instance
-> recover same request/turn
-> one existing remote identity
-> backend submit call count remains 1
```

Expected on no unique remote identity: `AMBIGUOUS`, never a second submit.

- [ ] **Step 4: Add conversation-slot reuse integration**

Use `WorkerConversationPoolV3(pool_size=1)`:

```text
alpha acquires slot -> binds conversation C -> completes -> releases
beta acquires same slot -> gets conversation C
beta has distinct worker/session/turn ids
transport targets C exactly
```

- [ ] **Step 5: Full regression and commit**

```text
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q .
git diff --check
```

Commit:

```text
test: enforce browser transport conformance with Scorp semantics
```

---

### Task 5: Add isolated live bake-off runner and evidence format

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/transport_bakeoff_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/p0_transport_probe.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_transport_bakeoff_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_p0_transport_probe.py`

**Interfaces:**
- `run_scenario(candidate, scenario, root, *, cycle_id) -> dict`
- `summarize_evidence(rows: list[dict]) -> dict`
- CLI:

```text
python p0_transport_probe.py --candidate windows-mcp|chatgpt-use|chrome-use --scenario <name> --root C:\ScorpAgent\p0-transport-bakeoff
python p0_transport_probe.py --candidate <name> --soak-cycles 20 --root C:\ScorpAgent\p0-transport-bakeoff
```

- [ ] **Step 1: Write failing evidence-summary tests**

```python
from transport_bakeoff_v3 import summarize_evidence


def test_acceptance_rejects_any_identity_or_duplicate_failure():
    rows = [{
        "wrong_conversation": 0,
        "duplicate_submit": 1,
        "manual_continue": 0,
        "identity_change": 0,
        "terminal_residue": 0,
    }]
    assert summarize_evidence(rows)["accepted"] is False
```

- [ ] **Step 2: Implement an append-only evidence record**

Each JSONL row must contain:

```text
protocol_version = scorp.p0-transport-evidence/v1
candidate
scenario
cycle_id
started_at
finished_at
project_id
turn_id(s)
conversation_url(s)
submission_id(s)
wrong_conversation
duplicate_submit
manual_continue
identity_change
stale_worker_lease
terminal_residue
result
error
artifact_paths
```

Evidence root:

```text
C:\ScorpAgent\p0-transport-bakeoff\evidence\<candidate>\evidence.jsonl
```

- [ ] **Step 3: Encode scenarios 1–10 from the approved spec**

Exact scenario names:

```text
persistent_a
worker_slot_reuse
crash_after_submit
ambiguous_recovery
adapter_restart
chrome_restart
concurrent_generations
throttle_pre_submit
stale_target
unattended_lifecycle
```

No scenario is allowed to read/write `C:\ScorpAgent\state-v3\active`.

- [ ] **Step 4: Add a path-isolation test**

```python
with self.assertRaisesRegex(ValueError, "P0_ROOT_PRODUCTION_FORBIDDEN"):
    validate_p0_root(r"C:\ScorpAgent\state-v3\active")
```

Also reject parent/alias paths resolving to the production root.

- [ ] **Step 5: Run portable harness tests and commit**

```text
python -m unittest -v tests.test_transport_bakeoff_v3 tests.test_p0_transport_probe
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q .
git diff --check
```

Commit:

```text
feat: add isolated Scorp transport bake-off harness
```

---

### Task 6: Execute the Windows live probe through GPT-Native Executor V4.1

**Files:**
- No production file mutation.
- Evidence written only under `C:\ScorpAgent\p0-transport-bakeoff`.
- GitHub execution issues are created in `Scorp96/scorp-control-plane` using protocol `scorp.exec/v4`.

**Interfaces:**
- Consumes the existing executor queue protocol `[SCORP_EXEC] <task_id> <action_id>` and exact JSON body matching `scorp-agent/executor-v4.schema.json`.
- Produces trusted-author `SCORP_EXEC_RESULT` evidence bound to `(task_id, issue_number, action_id, claim_token)`.

- [ ] **Step 1: Health-check the Windows executor before P0 actions**

Create one standard-safety `health` action. Acceptance:

```text
executor returns DONE
trusted result identity matches issue/task/action/claim
no active production project file is touched
```

- [ ] **Step 2: Create an isolated git checkout/worktree action for P0**

Use a deterministic `git` action to create/update an isolated P0 branch/worktree rooted outside the production install, for example:

```text
C:\ScorpAgent\worktrees\p0-chatgpt-transport-bakeoff
```

The executor must verify the checked-out commit/branch before tests run.

- [ ] **Step 3: Probe external candidate availability**

Run, without auto-upgrade:

```text
chatgpt-use --version
chrome-use --version
chrome-use status
```

If `chatgpt-use` is present, additionally run a no-resubmit request-receipt/status probe using a disposable P0 request id. Record the exact version and stdout/stderr hash.

Decision:

```text
B1 usable + exact receipt semantics -> run B1 first
B1 unavailable/incompatible, chrome-use healthy -> run B2
neither healthy -> P0 BLOCKED_EXTERNAL; do not touch production
```

- [ ] **Step 4: Run targeted Windows tests from the isolated worktree**

```text
python -m unittest -v tests.test_p0_upstream_probe
python -m unittest -v tests.test_durable_actor_transport_v3
python -m unittest -v tests.test_browser_transport_conformance_v3
```

- [ ] **Step 5: Run live scenarios 1–9 for Candidate A and Candidate B**

Each scenario writes its own evidence row and artifact directory. A candidate stops immediately on wrong-conversation or duplicate-submit evidence; no “best effort” continuation is allowed after either invariant breaks.

- [ ] **Step 6: Run the full unattended lifecycle scenario**

Required chain:

```text
A -> DISPATCH -> Workers -> HANDOFF/BLOCKER -> A validate/integrate -> DRAIN -> D -> same A conversation -> CONTINUE/TERMINAL
```

Required manual continuation count: `0`.

---

### Task 7: Run 20-cycle soak, select the winner, and stop

**Files:**
- Create after live run: `docs/superpowers/evidence/2026-09-12-scorp-p0-transport-bakeoff-result.md`
- No production runtime modifications.

**Interfaces:**
- Consumes the append-only evidence JSONL from Task 5/6.
- Produces one decision: `SELECT_B`, `RETAIN_A`, or `BLOCKED_EXTERNAL`.

- [ ] **Step 1: Run 20 consecutive lifecycle cycles for every candidate that passed hard scenarios**

Acceptance counters must all be zero:

```text
wrong_conversation
duplicate_submit
manual_continue
identity_change
stale_worker_lease
terminal_residue
```

- [ ] **Step 2: Run final existing regression**

```text
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q scorp-agent/chatgpt-gui-bridge
git diff --check
```

- [ ] **Step 3: Produce the evidence-backed decision report**

The report must include:

```text
exact candidate versions
exact tested commit
hard-scenario matrix
20-cycle counters
timeouts/ambiguous incidents
TaskGroup presence/absence by candidate
conversation identity evidence
rollback status
winner and rejected alternatives
```

Decision rules:

```text
SELECT_B only if every hard scenario passes and all 20-cycle hard counters are zero.
RETAIN_A if B weakens exact targeting or recovery while A passes the gate.
BLOCKED_EXTERNAL if browser-native prerequisites cannot be made testable without changing production or weakening safety.
```

- [ ] **Step 4: Stop P0 after selection**

Do not add more abstraction after one candidate passes. Do not migrate production in this plan. Production migration and Windows reboot/logon recovery require a separate approved plan.

- [ ] **Step 5: Commit only evidence/report artifacts that contain no secrets or browser tokens**

```text
docs: record P0 transport bake-off decision evidence
```
