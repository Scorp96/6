from pathlib import Path
import json

from actor_gui_backend_v3 import ActorGuiBackendV3
from chat_resource_manager_v3 import ChatResourceManagerV3
from continuation_watchdog_v3 import ContinuationWatchdog
from durable_actor_transport_v3 import DurableActorTransportV3
from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_coordinator_v3 import MasterWorkerCoordinatorV3
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_bootstrap_v3 import CONTRACT_PROTOCOL, sha256_text
from project_lifecycle_v3 import ARCHIVE_PROTOCOL, ROTATION_PROTOCOL
from project_state_v3 import ProjectStateStore, TERMINAL_STATUS
from session_registry_v3 import SessionRegistryV3
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3
from worker_event_pump_v3 import WorkerEventPumpV3
from worker_conversation_pool_v3 import WorkerConversationPoolV3
from worker_event_queue_v3 import WorkerEventQueueV3


class ProductionV3Runtime:
    def __init__(self, project_root, *, max_inflight=5, max_workers=4, driver=None, adaptive_browser_poll=False):
        self.root = Path(project_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_inflight = int(max_inflight)
        if self.max_inflight < 1 or self.max_inflight > 9:
            raise ValueError("PRODUCTION_V3_INFLIGHT_LIMIT_INVALID")
        self.max_workers = int(max_workers)
        if self.max_workers < 1 or self.max_workers > 8:
            raise ValueError("PRODUCTION_V3_WORKER_LIMIT_INVALID")
        self.driver = driver
        self.adaptive_browser_poll = bool(adaptive_browser_poll)
        self._coordinator = None
        self._project_id = None

    def _validate_project_contract(self, state):
        contract_path = self.root / "project-contract.json"
        if not contract_path.is_file():
            return None
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ValueError("PROJECT_CONTRACT_INVALID") from exc
        if not isinstance(contract, dict):
            raise ValueError("PROJECT_CONTRACT_INVALID")
        if contract.get("protocol_version") != CONTRACT_PROTOCOL:
            raise ValueError("PROJECT_CONTRACT_PROTOCOL_INVALID")
        if contract.get("project_id") != state.get("project_id"):
            raise ValueError("PROJECT_CONTRACT_PROJECT_MISMATCH")
        if contract.get("master_identity") != "A" or state.get("master_identity") != "A":
            raise ValueError("PROJECT_CONTRACT_MASTER_IDENTITY_INVALID")

        objective = contract.get("root_objective")
        acceptance = contract.get("acceptance_criteria")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("PROJECT_CONTRACT_GOAL_HASH_MISMATCH")
        if not isinstance(acceptance, str) or not acceptance.strip():
            raise ValueError("PROJECT_CONTRACT_ACCEPTANCE_HASH_MISMATCH")

        goal_hash = sha256_text(objective)
        acceptance_hash = sha256_text(acceptance)
        if goal_hash != contract.get("root_objective_sha256") or goal_hash != state.get("goal_contract_sha256"):
            raise ValueError("PROJECT_CONTRACT_GOAL_HASH_MISMATCH")
        if acceptance_hash != contract.get("acceptance_sha256") or acceptance_hash != state.get("acceptance_sha256"):
            raise ValueError("PROJECT_CONTRACT_ACCEPTANCE_HASH_MISMATCH")
        return contract

    def _reset_cached_project(self):
        self._coordinator = None
        self._project_id = None

    def _rotation_allows_project_change(self, previous_project_id):
        previous = str(previous_project_id or "").strip()
        if not previous:
            return False
        marker_path = self.root.parent / "project-rotation.json"
        if not marker_path.is_file():
            return False
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        if not isinstance(marker, dict):
            return False
        if marker.get("protocol_version") != ROTATION_PROTOCOL or marker.get("phase") != "ARCHIVED":
            return False
        if marker.get("previous_project_id") != previous:
            return False
        archive_path = str(marker.get("archive_path") or "").strip()
        if not archive_path:
            return False
        archived_root = Path(archive_path)
        state_path = archived_root / "project-state.json"
        manifest_path = archived_root / "archive-manifest.json"
        if not state_path.is_file() or not manifest_path.is_file():
            return False
        try:
            archived = ProjectStateStore(state_path).load()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        if archived.get("status") not in TERMINAL_STATUS:
            return False
        if not isinstance(manifest, dict) or manifest.get("protocol_version") != ARCHIVE_PROTOCOL:
            return False
        checks = (
            archived.get("project_id") == previous,
            archived.get("goal_contract_sha256") == marker.get("previous_goal_contract_sha256"),
            archived.get("acceptance_sha256") == marker.get("previous_acceptance_sha256"),
            archived.get("state_version") == marker.get("previous_state_version"),
            manifest.get("project_id") == archived.get("project_id"),
            manifest.get("status") == archived.get("status"),
            manifest.get("state_version") == archived.get("state_version"),
            manifest.get("goal_contract_sha256") == archived.get("goal_contract_sha256"),
            manifest.get("acceptance_sha256") == archived.get("acceptance_sha256"),
            manifest.get("archive_path") == str(archived_root),
        )
        return all(checks)

    def _build(self):
        state_path = self.root / "project-state.json"
        if not state_path.is_file():
            return None
        state = ProjectStateStore(state_path)
        current = state.load()
        contract = self._validate_project_contract(current)
        project_id = str(current.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("PROJECT_ID_EMPTY")
        leases = MasterWindowLeaseStore(self.root / "master-window-lease.json")
        queue = WorkerEventQueueV3(self.root / "worker-events-v3")
        sessions = SessionRegistryV3(self.root / "sessions-v3.json")
        outbox = self.root / "master-worker-v3-outbox"
        watchdog = ContinuationWatchdog(state, leases, outbox, project_contract=contract)
        pump = WorkerEventPumpV3(state, leases, queue, outbox)
        transition = MasterStateTransitionV3(state, leases, queue)
        driver = self.driver or WindowsMcpActorDriverV3()
        backend = ActorGuiBackendV3(driver)
        transport = DurableActorTransportV3(self.root / "actor-transport-v3.json", backend)
        chat_resources = ChatResourceManagerV3(self.root / "chat-resource-v3.json")
        worker_pool = WorkerConversationPoolV3(self.root / "worker-conversation-pool-v3.json", pool_size=self.max_workers)
        relay = ParallelMasterWorkerRelayV3(
            self.root,
            sessions,
            transport,
            master_transition=transition,
            max_inflight=self.max_inflight,
            max_workers=self.max_workers,
            chat_resources=chat_resources,
            worker_pool=worker_pool,
            adaptive_browser_poll=self.adaptive_browser_poll,
        )
        self._project_id = project_id
        self._coordinator = MasterWorkerCoordinatorV3(
            project_id, state, leases, watchdog, pump, relay, outbox, master_ttl_seconds=1500
        )
        return self._coordinator

    async def run_once(self):
        state_path = self.root / "project-state.json"
        if not state_path.is_file():
            self._reset_cached_project()
            return {"status": "IDLE", "runtime": "V3"}

        current = ProjectStateStore(state_path).load()
        self._validate_project_contract(current)
        current_project_id = str(current.get("project_id") or "").strip()
        if not current_project_id:
            raise ValueError("PROJECT_ID_EMPTY")

        if self._coordinator is None:
            self._build()
        elif current_project_id != self._project_id:
            if not self._rotation_allows_project_change(self._project_id):
                raise ValueError("PRODUCTION_V3_PROJECT_CHANGED")
            self._reset_cached_project()
            self._build()

        result = await self._coordinator.run_once()
        if str(result.get("status") or "").upper() in TERMINAL_STATUS:
            return {
                "status": "IDLE",
                "runtime": "V3",
                "project_id": self._project_id,
                "project_status": result.get("status"),
            }
        return result