# Chrome-Use V3 Transport Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make chrome-use the default Master/Worker V3 browser transport in the isolated P0 branch without changing Scorp's durable A/D/Worker control-plane semantics.

**Architecture:** Keep `ParallelMasterWorkerRelayV3 -> DurableActorTransportV3 -> ActorGuiBackendV3` unchanged and replace only driver construction. `bridge_worker.py` explicitly selects `chrome-use` by default, constructs `ChromeUseCliV3 + ChromeUseActorDriverV3`, and allows Windows-MCP only via an explicit legacy option. Existing SessionRegistryV3 and WorkerConversationPoolV3 continue to own canonical conversation reuse.

**Tech Stack:** Python 3, unittest, chrome-use CLI v1.5.123, Scorp Master/Worker V3.

**Spec:** `docs/superpowers/specs/2026-09-13-chrome-use-v3-transport-cutover-design.md`

## Global Constraints

- Work only on `p0/chatgpt-transport-bakeoff`.
- Never touch `production-e2e-g274` or `C:\ScorpAgent\state-v3\active`.
- Never resubmit #735/g735 or any older timed-out probe identity.
- Never invoke Codex or a local model.
- No automatic fallback from chrome-use to Windows-MCP.
- Preserve DurableActorTransportV3 exactly-once behavior and all root/acceptance/state-version invariants.
- Use chrome-use's native session/tab model; do not add coordinate/foreground/HWND browser control.

---

### Task 1: Lock transport-selection behavior with RED tests

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_v3_transport_selection.py`
- Modify later: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Modify later: `scorp-agent/chatgpt-gui-bridge/production_v3_runtime.py`

**Interfaces:**
- Produces: explicit `build_v3_driver(transport, project_root, chrome_use_executable, timeout_seconds=30)` helper contract.
- Produces: bridge CLI arguments `--v3-transport` and `--chrome-use-executable`.

- [ ] **Step 1: Write failing tests**

Tests must assert:

```python
from pathlib import Path
import tempfile
import unittest

from bridge_worker import build_v3_driver
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3

class V3TransportSelectionTests(unittest.TestCase):
    def test_chrome_use_is_default_and_builds_chrome_use_driver(self):
        with tempfile.TemporaryDirectory() as td:
            driver = build_v3_driver(
                transport="chrome-use",
                project_root=td,
                chrome_use_executable=r"C:\\fake\\chrome-use.exe",
            )
            self.assertIsInstance(driver, ChromeUseActorDriverV3)
            self.assertEqual(driver.state_path, Path(td) / "chrome-use-driver-v3.json")

    def test_missing_chrome_use_executable_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "CHROME_USE_EXECUTABLE_REQUIRED"):
                build_v3_driver(
                    transport="chrome-use",
                    project_root=td,
                    chrome_use_executable=None,
                )

    def test_windows_mcp_requires_explicit_legacy_selection(self):
        with tempfile.TemporaryDirectory() as td:
            driver = build_v3_driver(
                transport="windows-mcp",
                project_root=td,
                chrome_use_executable=None,
            )
            self.assertIsInstance(driver, WindowsMcpActorDriverV3)

    def test_unknown_transport_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "V3_TRANSPORT_INVALID"):
                build_v3_driver("auto", td, None)
```

- [ ] **Step 2: Run RED**

Run:

```powershell
python.exe -m unittest -v tests.test_v3_transport_selection
```

Expected: FAIL because `build_v3_driver` does not exist.

- [ ] **Step 3: Commit test-only RED**

Commit message:

```text
test: require explicit chrome-use V3 transport
```

---

### Task 2: Implement minimal chrome-use driver construction

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Reuse: `scorp-agent/chatgpt-gui-bridge/chrome_use_cli_v3.py`
- Reuse: `scorp-agent/chatgpt-gui-bridge/chrome_use_actor_driver_v3.py`
- Reuse: `scorp-agent/chatgpt-gui-bridge/windows_mcp_actor_driver_v3.py`

**Interfaces:**
- `build_v3_driver(transport: str, project_root: str, chrome_use_executable: str | None, timeout_seconds: int = 30)` returns an actor driver.
- chrome-use state path is `<project_root>/chrome-use-driver-v3.json`.

- [ ] **Step 1: Add minimal helper**

Implementation shape:

```python
def build_v3_driver(transport, project_root, chrome_use_executable, timeout_seconds=30):
    value = str(transport or "").strip().lower()
    if value == "chrome-use":
        executable = str(chrome_use_executable or "").strip()
        if not executable:
            raise ValueError("CHROME_USE_EXECUTABLE_REQUIRED")
        from chrome_use_cli_v3 import ChromeUseCliV3
        from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
        cli = ChromeUseCliV3(executable=executable)
        return ChromeUseActorDriverV3(
            cli,
            Path(project_root) / "chrome-use-driver-v3.json",
            timeout_seconds=timeout_seconds,
        )
    if value == "windows-mcp":
        from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3
        return WindowsMcpActorDriverV3()
    raise ValueError("V3_TRANSPORT_INVALID")
```

- [ ] **Step 2: Run targeted test**

```powershell
python.exe -m unittest -v tests.test_v3_transport_selection
```

Expected: PASS.

- [ ] **Step 3: Commit**

```text
feat: add explicit V3 transport selection
```

---

### Task 3: Wire bridge CLI to chrome-use by default

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/bridge_worker.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_v3_transport_selection.py`

**Interfaces:**
- CLI: `--v3-transport {chrome-use,windows-mcp}`, default `chrome-use`.
- CLI: `--chrome-use-executable`, default from environment variable `SCORP_CHROME_USE_EXE` or empty.
- `build_runtime_from_args()` passes the constructed driver into `ProductionV3Runtime(..., driver=driver)`.

- [ ] **Step 1: Extend failing tests**

Add tests proving parser defaults to `chrome-use`, explicit `windows-mcp` works, and `build_runtime_from_args()` injects the selected driver.

- [ ] **Step 2: Run RED**

```powershell
python.exe -m unittest -v tests.test_v3_transport_selection
```

Expected: FAIL before parser/runtime wiring exists.

- [ ] **Step 3: Implement parser/runtime wiring**

Requirements:

```python
parser.add_argument('--v3-transport', choices=('chrome-use', 'windows-mcp'), default='chrome-use')
parser.add_argument('--chrome-use-executable', default=os.environ.get('SCORP_CHROME_USE_EXE', ''))
```

and in `build_runtime_from_args`:

```python
driver = build_v3_driver(
    args.v3_transport,
    args.v3_project_root,
    args.chrome_use_executable,
)
v3_runtime = ProductionV3Runtime(
    args.v3_project_root,
    max_inflight=args.v3_max_inflight,
    driver=driver,
)
```

- [ ] **Step 4: Run test**

Expected: PASS.

- [ ] **Step 5: Commit**

```text
feat: default V3 bridge transport to chrome-use
```

---

### Task 4: Prove persistent conversation/session reuse composition

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/tests/test_chrome_use_v3_composition.py`
- Reuse: `parallel_master_worker_relay_v3.py`
- Reuse: `session_registry_v3.py`
- Reuse: `worker_conversation_pool_v3.py`
- Reuse: `chrome_use_actor_driver_v3.py`

**Interfaces:**
- Existing canonical URL is authoritative and must reuse the same chrome-use session.
- Only a first-chat bootstrap may use a temporary `scorp-p0-turn-*` session.

- [ ] **Step 1: Add composition test**

Use a fake `ChromeUseCliV3` runner and two logical turns sharing an already-bound canonical conversation URL. Assert:

```text
same conversation URL
same driver session
no second `open https://chatgpt.com/` bootstrap
no HWND/window_handle field
```

Also assert worker-pool URL reuse survives release/reacquire for the same slot.

- [ ] **Step 2: Run test**

Expected: PASS if existing reuse semantics are correct; if it fails, treat failure as evidence and make only the minimal correction in `ChromeUseActorDriverV3` or pool binding.

- [ ] **Step 3: Commit**

```text
test: prove persistent chrome-use V3 session reuse
```

---

### Task 5: Full P0 regression gate

**Files:** no code changes unless a regression is proven.

- [ ] **Step 1: Run targeted suite**

```powershell
python.exe -m unittest -v \
  tests.test_v3_transport_selection \
  tests.test_chrome_use_v3_composition \
  tests.test_p0_live_chatgpt_probe \
  tests.test_chrome_use_cli_v3 \
  tests.test_chrome_use_click_timeout_recovery_v3 \
  tests.test_chrome_use_conversation_read_v3 \
  tests.test_chrome_use_send_button_v3 \
  tests.test_chrome_use_actor_driver_v3 \
  tests.test_actor_gui_backend_v3 \
  tests.test_durable_actor_transport_v3 \
  tests.test_parallel_master_worker_relay_v3
```

Expected: all PASS.

- [ ] **Step 2: Compile changed Python files**

```powershell
python.exe -m py_compile bridge_worker.py chrome_use_actor_driver_v3.py tests\\test_v3_transport_selection.py tests\\test_chrome_use_v3_composition.py
```

Expected: PASS.

- [ ] **Step 3: Verify no P0 default dependency on Windows foreground control**

Static assertion: the default bridge transport selects chrome-use; WindowsMCP remains reachable only by explicit `windows-mcp` selection.

- [ ] **Step 4: Commit any final test-only adjustments**

Commit message if needed:

```text
test: verify chrome-use V3 transport cutover
```

---

### Task 6: Isolated runtime acceptance before any new Live Probe

**Files:** no production mutation.

- [ ] **Step 1: Sync Windows P0 worktree to the exact new HEAD**

Use `git pull --ff-only origin p0/chatgpt-transport-bakeoff` and verify exact HEAD.

- [ ] **Step 2: Run a local non-browser construction smoke**

Instantiate the bridge/runtime with:

```text
--v3-transport chrome-use
--chrome-use-executable C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe
--v3-project-root <isolated P0 project root>
```

Do not touch `state-v3\active`.

- [ ] **Step 3: Inventory chrome-use sessions/tabs read-only**

Use native `session list` / `tab list` to establish a bounded baseline. Do not navigate or submit.

- [ ] **Step 4: Only after Tasks 1-6 are GREEN may a single new live acceptance identity be considered.**

Live acceptance must additionally prove:

```text
0 OS-level foreground/window actions
0 duplicate submit
canonical conversation/session reuse
bounded tab/session count
explicit assistant response completion
```

If live acceptance fails, inspect that exact persisted turn/session read-only before any further write.