# Scorp P0 ChatGPT Transport Bake-off V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This V2 plan supersedes `docs/superpowers/plans/2026-09-12-scorp-p0-transport-bakeoff.md` because self-review found that `chatgpt-use ask` must be treated as a long-running owner process, not as a synchronous `submitted` API.

**Goal:** Select and prove a browser-native ChatGPT Web transport for Scorp while leaving the deterministic control plane and authoritative production project untouched.

**Architecture:** Keep ProjectState/D/Master transition/Worker guard/scheduler/Worker conversation pool/`DurableActorTransportV3` unchanged. Candidate B1 launches one `chatgpt-use ask --request-id <submission_id>` owner process and returns to Scorp only after the upstream request receipt proves a remote submit/conversation identity; `poll` and `recover` read receipt/status and never submit. If B1 is not Windows-usable or cannot target/recover exact conversations, Candidate B2 implements the same Scorp backend contract directly on `chrome-use`, borrowing upstream record-first/fail-closed semantics. Live validation uses GPT-Native Executor V4.1 and an isolated root, not RDC or the installed production bridge.

**Tech Stack:** Python 3.11+, `unittest`, existing Scorp V3 Python modules, GitHub Actions, GPT-Native Executor V4.1, `chatgpt-use`/`chrome-use`, Windows authenticated Chrome.

**Spec:** `docs/superpowers/specs/2026-09-12-scorp-p0-transport-bakeoff-design.md`

## Global Constraints

- Authoritative source lineage starts at `5e5fdc7ef82aa244d5f29c7468bed56abdc78d57`; do not regress to an older implementation.
- Never write to `C:\ScorpAgent\state-v3\active` or mutate `production-e2e-g274` during P0.
- Never enable the disabled production watchdog or replace the installed production bridge during P0.
- Use `C:\ScorpAgent\p0-transport-bakeoff` for all local P0 state/evidence.
- Preserve `MASTER_IDENTITY=A`, objective/acceptance hashes, CAS state transitions, Worker result admission, conversation-slot leases, and terminal fencing.
- `DurableActorTransportV3` remains authoritative for `SUBMITTING/SUBMITTED/COMPLETED/RETRYABLE/TIMED_OUT/AMBIGUOUS` semantics.
- Unknown post-submit state is never retryable.
- `recover()` never sends a prompt.
- A live winner requires 20 consecutive lifecycle cycles with: wrong conversation = 0, duplicate submit = 0, manual continue = 0, unreconciled identity change = 0, stale Worker lease deadlock = 0, terminal transport residue = 0.
- No production migration belongs to this plan.

---

### Task 1: Create isolated execution branch, capability probe, and CI gate

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/p0_upstream_probe.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_p0_upstream_probe.py`
- Create: `.github/workflows/scorp-p0-transport-ci.yml`

**Produces:**
- `classify_candidate_b(evidence: dict) -> str`
- `probe_versions() -> dict`

- [ ] **1. Write failing classifier tests**

```python
import unittest
from p0_upstream_probe import classify_candidate_b

class ProbeTests(unittest.TestCase):
    def test_chatgpt_use_wins_only_when_receipts_and_windows_backend_are_proven(self):
        evidence = {
            "chatgpt_use": {"present": True, "request_receipts": True, "conversation_record": True, "windows": True},
            "chrome_use": {"present": True, "windows": True},
        }
        self.assertEqual("CHATGPT_USE", classify_candidate_b(evidence))

    def test_falls_back_to_chrome_use(self):
        evidence = {
            "chatgpt_use": {"present": False, "request_receipts": False, "conversation_record": False, "windows": False},
            "chrome_use": {"present": True, "windows": True},
        }
        self.assertEqual("CHROME_USE_DIRECT", classify_candidate_b(evidence))

    def test_blocks_without_proven_candidate(self):
        self.assertEqual("BLOCKED", classify_candidate_b({"chatgpt_use": {}, "chrome_use": {}}))
```

- [ ] **2. Run RED**

```text
cd scorp-agent/chatgpt-gui-bridge
python -m unittest -v tests.test_p0_upstream_probe
```

Expected: module missing.

- [ ] **3. Implement minimal classifier/version probe**

```python
from __future__ import annotations
import shutil
import subprocess


def classify_candidate_b(evidence: dict) -> str:
    cgu = evidence.get("chatgpt_use") or {}
    cu = evidence.get("chrome_use") or {}
    if all(cgu.get(k) is True for k in ("present", "request_receipts", "conversation_record", "windows")):
        return "CHATGPT_USE"
    if cu.get("present") is True and cu.get("windows") is True:
        return "CHROME_USE_DIRECT"
    return "BLOCKED"


def _version(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"present": False, "path": None, "version": None}
    cp = subprocess.run([path, "--version"], text=True, capture_output=True, timeout=20)
    return {"present": cp.returncode == 0, "path": path, "version": (cp.stdout or cp.stderr or "").strip()}


def probe_versions() -> dict:
    return {"chatgpt_use": _version("chatgpt-use"), "chrome_use": _version("chrome-use")}
```

- [ ] **4. Add CI**

```yaml
name: Scorp P0 Transport CI
on:
  push:
    branches: [p0/chatgpt-transport-bakeoff]
  workflow_dispatch:
jobs:
  logic:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - run: python -m pip install --disable-pip-version-check 'mcp==2.2.0'
      - run: python -m compileall -q scorp-agent/chatgpt-gui-bridge
      - working-directory: scorp-agent/chatgpt-gui-bridge
        run: python -m unittest discover -s tests -p 'test_*.py' -v
```

- [ ] **5. Verify + commit**

```text
python -m unittest -v tests.test_p0_upstream_probe
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q .
git diff --check
```

Commit: `test: add P0 browser transport capability gate`

---

### Task 2: Candidate B1 — asynchronous `chatgpt-use` receipt backend

**Gate:** Execute only if the Windows live probe proves `chatgpt-use` runs and its request receipt/status files expose request id, submitted state, conversation id, and terminal outcome. Otherwise record `B1_GATE_FAILED` and execute Task 3.

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/chatgpt_use_cli_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/chatgpt_use_actor_backend_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chatgpt_use_cli_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chatgpt_use_actor_backend_v3.py`

**Produces:**
- `ChatGptUseCliV3.start_request(...) -> OwnerProcess`
- `ChatGptUseCliV3.status(request_id: str) -> dict`
- `ChatGptUseActorBackendV3.submit(...) -> dict`
- `ChatGptUseActorBackendV3.poll(...) -> dict`
- `ChatGptUseActorBackendV3.recover(...) -> dict | None`

`OwnerProcess` contains only local process identity and is not authoritative for remote submission.

- [ ] **1. Write failing CLI owner-process tests**

```python
import asyncio
import unittest
from chatgpt_use_cli_v3 import ChatGptUseCliV3

class FakeProcess:
    pid = 4321

class CliTests(unittest.TestCase):
    def test_start_request_names_request_exactly_once(self):
        calls = []
        async def starter(argv):
            calls.append(argv)
            return FakeProcess()
        cli = ChatGptUseCliV3(starter=starter)
        owner = asyncio.run(cli.start_request(
            request_id="actor-submit-1",
            prompt="TURN_ID=turn-1",
            session="scorp-master",
            timeout_seconds=120,
        ))
        self.assertEqual(4321, owner.pid)
        self.assertEqual(1, len(calls))
        self.assertIn("actor-submit-1", calls[0])
```

- [ ] **2. Write failing backend crash/recovery tests**

```python
import asyncio
import unittest
from chatgpt_use_actor_backend_v3 import ChatGptUseActorBackendV3

class FakeCli:
    def __init__(self):
        self.starts = 0
        self.states = []
    async def start_request(self, **kwargs):
        self.starts += 1
        return type("P", (), {"pid": 1})()
    async def status(self, request_id):
        return self.states.pop(0)

class BackendTests(unittest.TestCase):
    def test_submit_returns_only_after_receipt_proves_remote_identity(self):
        cli = FakeCli()
        cli.states = [{"status": "running", "submitted": "yes", "conversation_id": "abc"}]
        backend = ChatGptUseActorBackendV3(cli, receipt_poll_seconds=0)
        handle = asyncio.run(backend.submit(
            "actor-submit-1", prompt="P", turn_id="turn-1", actor_kind="MASTER",
            conversation_url="https://chatgpt.com/c/abc", timeout_seconds=120,
        ))
        self.assertEqual("https://chatgpt.com/c/abc", handle["conversation_url"])
        self.assertEqual(1, cli.starts)

    def test_recover_never_starts_new_owner(self):
        cli = FakeCli()
        cli.states = [{"status": "detached", "submitted": "yes", "conversation_id": "abc"}]
        backend = ChatGptUseActorBackendV3(cli, receipt_poll_seconds=0)
        handle = asyncio.run(backend.recover("actor-submit-1", turn_id="turn-1", actor_kind="MASTER"))
        self.assertEqual("https://chatgpt.com/c/abc", handle["conversation_url"])
        self.assertEqual(0, cli.starts)

    def test_submission_unknown_returns_no_handle_and_never_resubmits(self):
        cli = FakeCli()
        cli.states = [{"status": "submission_unknown"}]
        backend = ChatGptUseActorBackendV3(cli, receipt_poll_seconds=0)
        self.assertIsNone(asyncio.run(backend.recover("actor-submit-1", turn_id="turn-1", actor_kind="WORKER")))
        self.assertEqual(0, cli.starts)
```

- [ ] **3. Run RED**

```text
python -m unittest -v tests.test_chatgpt_use_cli_v3 tests.test_chatgpt_use_actor_backend_v3
```

Expected: modules missing.

- [ ] **4. Implement owner-process wrapper**

Default launch shape:

```python
argv = [
    executable,
    "ask",
    prompt,
    "--request-id", request_id,
    "--busy", "fail",
    "--session", session,
]
```

`start_request()` uses `asyncio.create_subprocess_exec` and returns immediately after process creation. It never launches the same `request_id` twice within one adapter instance.

`status()` executes:

```text
chatgpt-use status <request_id>
```

and parses the single JSON object. Invalid/non-JSON status is `CHATGPT_USE_STATUS_INVALID`, not proof of no-send.

- [ ] **5. Implement strict backend state mapping**

```text
submitted=yes + conversation_id -> handle is safe to persist
completed + conversation_id + terminal record -> COMPLETED
running + submitted=yes -> PENDING
busy/duplicate -> read status for same request id; never start another process
detached + conversation_id -> recoverable handle
submission_unknown -> recover returns None; DurableActorTransport converts unresolved SUBMITTING to AMBIGUOUS
failed + submitted=no -> raise the existing known pre-submit error type only when the upstream reason maps to throttle/composer unavailable; otherwise fail closed
failed with submitted=yes/unknown -> not RETRYABLE
```

Supplied `conversation_url` must equal `https://chatgpt.com/c/<conversation_id>` once the receipt knows the id. Mismatch raises `ACTOR_BACKEND_CONVERSATION_MISMATCH`.

- [ ] **6. Prove compatibility with existing Scorp durable semantics**

```text
python -m unittest -v \
  tests.test_chatgpt_use_cli_v3 \
  tests.test_chatgpt_use_actor_backend_v3 \
  tests.test_durable_actor_transport_v3
```

Expected: PASS; crash-after-submit path has one owner-process start and zero duplicate submit.

Commit: `feat: adapt chatgpt-use receipts to Scorp actor transport`

---

### Task 3: Candidate B2 — direct `chrome-use` fallback

**Gate:** Skip when B1 passes exact targeting, crash recovery, and concurrent remote-generation requirements. Record `B2_NOT_NEEDED`. Execute only after a concrete B1 failure is recorded.

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/chrome_use_cli_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/chrome_use_actor_driver_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chrome_use_cli_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chrome_use_actor_driver_v3.py`

**Produces:** existing driver shape already consumed by `ActorGuiBackendV3`:
- `submit_prompt(...) -> str`
- `snapshot_conversation(...) -> str`
- `discover_turn_conversations(...) -> list[dict]`

- [ ] **1. Write RED tests for explicit session/JSON CLI calls and exact URL binding**

```python
async def runner(argv, timeout_seconds):
    return 0, '{"url":"https://chatgpt.com/c/a1"}', ''

cli = ChromeUseCliV3(runner=runner)
result = asyncio.run(cli.run_json("scorp-master-a1", "status", timeout_seconds=10))
self.assertEqual("https://chatgpt.com/c/a1", result["url"])
```

Driver tests must cover exact target match, target mismatch, zero/one/multiple recovery matches, and never closing an unrelated tab.

- [ ] **2. Implement strict CLI wrapper**

All commands use an explicit Scorp-owned `--session` and JSON output. Nonzero exit, timeout, or malformed JSON are transport failures; none prove a prompt was not submitted.

- [ ] **3. Implement record-first driver**

The page is used to send/reacquire only. Completion/reply evidence must come from the authoritative ChatGPT conversation record when a conversation id exists; rendered `innerText`/DOM quiescence cannot be the terminal oracle.

Known conversation flow:

```text
canonical URL -> attach/open exact Scorp session -> verify conversation id -> send once -> record identity
```

New conversation flow:

```text
Scorp turn marker -> new Scorp-owned session -> send once -> obtain conversation id -> return canonical URL
```

Recovery flow:

```text
enumerate Scorp-owned sessions only -> locate exact turn marker/conversation id -> 0 matches => None; >1 => ambiguous; never submit
```

- [ ] **4. Run integration regression**

```text
python -m unittest -v \
  tests.test_chrome_use_cli_v3 \
  tests.test_chrome_use_actor_driver_v3 \
  tests.test_actor_gui_backend_v3 \
  tests.test_durable_actor_transport_v3
```

Commit: `feat: add direct chrome-use Scorp transport fallback`

---

### Task 4: Shared conformance, isolation, and bake-off harness

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/transport_bakeoff_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/p0_transport_probe.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_browser_transport_conformance_v3.py`
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_transport_bakeoff_v3.py`

**Produces:**
- `validate_p0_root(path) -> Path`
- `run_scenario(candidate, scenario, root, cycle_id) -> dict`
- `summarize_evidence(rows) -> dict`

- [ ] **1. Write isolation RED test**

```python
with self.assertRaisesRegex(ValueError, "P0_ROOT_PRODUCTION_FORBIDDEN"):
    validate_p0_root(r"C:\ScorpAgent\state-v3\active")
```

Resolve paths before comparison so aliases/parents cannot bypass the guard.

- [ ] **2. Write shared conformance cases**

Required cases:

```text
existing_conversation_target_preserved
unique_recovery_without_resubmit
zero_match_recovery_fails_closed
multiple_match_recovery_fails_closed
post_submit_crash_zero_duplicate
known_pre_submit_failure_retryable_only_with_no-send evidence
stale_target_reacquires_by canonical identity
worker slot URL reused by distinct logical worker
```

- [ ] **3. Encode approved live scenarios exactly**

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

- [ ] **4. Write append-only evidence JSONL**

Each row contains:

```text
protocol_version=scorp.p0-transport-evidence/v1
candidate
scenario
cycle_id
started_at
finished_at
turn_ids
conversation_urls
submission_ids
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

Evidence path:

```text
C:\ScorpAgent\p0-transport-bakeoff\evidence\<candidate>\evidence.jsonl
```

- [ ] **5. Run portable regression**

```text
python -m unittest -v tests.test_browser_transport_conformance_v3 tests.test_transport_bakeoff_v3
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q .
git diff --check
```

Commit: `feat: add isolated browser transport bake-off harness`

---

### Task 5: Live Windows execution through GPT-Native Executor V4.1

**External control:** `Scorp96/scorp-control-plane` issues using `scorp.exec/v4`; no RDC.

- [ ] **1. Executor health action**

Require a trusted-author terminal result bound to the exact `(task_id, issue_number, action_id, claim_token)` tuple before any browser action.

- [ ] **2. Create isolated local worktree**

Target:

```text
C:\ScorpAgent\worktrees\p0-chatgpt-transport-bakeoff
```

Branch:

```text
p0/chatgpt-transport-bakeoff
```

Verify exact HEAD before running tests.

- [ ] **3. Probe installed candidate versions without auto-upgrade**

```text
chatgpt-use --version
chrome-use --version
chrome-use status
```

For B1, create one disposable P0 request id and verify request receipt/status semantics. No production conversation is used for this capability probe.

Decision:

```text
B1 exact receipt/Windows gate PASS -> execute B1 first
B1 FAIL + chrome-use healthy -> execute B2
both FAIL -> BLOCKED_EXTERNAL and stop
```

- [ ] **4. Run targeted Windows tests**

```text
python -m unittest -v tests.test_p0_upstream_probe
python -m unittest -v tests.test_durable_actor_transport_v3
python -m unittest -v tests.test_browser_transport_conformance_v3
```

- [ ] **5. Run hard scenarios 1–10 against Candidate A and eligible Candidate B**

Stop a candidate immediately on wrong-conversation or duplicate-submit evidence.

The unattended lifecycle must execute:

```text
A -> DISPATCH -> Workers -> HANDOFF/BLOCKER -> A validate/integrate -> DRAIN -> D -> same canonical A conversation -> CONTINUE/TERMINAL
```

Manual continuation count must be `0`.

---

### Task 6: 20-cycle soak and evidence-backed selection

**Files:**
- Create after live evidence exists: `docs/superpowers/evidence/2026-09-12-scorp-p0-transport-bakeoff-result.md`

- [ ] **1. Run 20 consecutive lifecycle cycles for each candidate that passed all hard scenarios**

All hard counters must remain `0`:

```text
wrong_conversation
duplicate_submit
manual_continue
identity_change
stale_worker_lease
terminal_residue
```

- [ ] **2. Final regression**

```text
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q scorp-agent/chatgpt-gui-bridge
git diff --check
```

- [ ] **3. Write result report with exact versions/commit/scenario matrix/counters/TaskGroup observations/conversation identity evidence/rollback status**

Decision is exactly one of:

```text
SELECT_B
RETAIN_A
BLOCKED_EXTERNAL
```

`SELECT_B` requires every hard scenario PASS and all 20-cycle hard counters equal zero.

- [ ] **4. Stop P0 after selection**

Do not migrate production, add Temporal, replace D, or delete the rollback Windows-MCP path in this plan. Production migration + reboot/logon recovery require the next separately reviewed plan.

- [ ] **5. Commit only sanitized evidence/report artifacts**

Commit: `docs: record P0 transport bake-off decision evidence`
