from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.acceptance import AcceptanceValidator
from master_a_dynamic_v4.master_reasoning import MasterReasoningCoordinator
from master_a_dynamic_v4.runtime_commands import RuntimeCommandService
from master_a_dynamic_v4.state_store import StateStore


STRICT_ACCEPTANCE = "AC_PERSISTENT_RUNTIME_OPERATIONAL"


class _BootstrapAdapter:
    def __init__(self, store):
        self.store = store

    def rebind(
        self,
        project_id,
        channel,
        *,
        actor_id,
        conversation_url,
        predecessor_url,
        reason,
        evidence,
    ):
        return self.store.rebind_browser(
            project_id,
            channel,
            actor_id=actor_id,
            conversation_url=conversation_url,
            predecessor_url=predecessor_url,
            reason=reason,
            evidence=evidence,
        )

    def reconcile(self, _intent_id):
        raise AssertionError("reconcile is not expected in bootstrap success")


class _BootstrapGateway:
    def __init__(self, store):
        self.store = store
        self.project_id = "p"
        self.adapter = _BootstrapAdapter(store)
        self.submit_calls = 0

    def submit_intent(self, intent_id):
        self.submit_calls += 1
        intent = self.store.get_intent(intent_id)
        payload = json.loads(intent["payload_json"])
        binding = dict(payload["reasoning_binding"])
        self.store.begin_possible_submit(intent_id)
        captured = self.store.capture_response(
            intent_id,
            response={
                "master_decision_version": 1,
                **binding,
                "action": "WAIT",
                "reason": "bootstrap complete",
            },
            conversation_url="https://chatgpt.com/c/master-r2-bootstrap",
            remote_identity="remote-r2-bootstrap",
            observation={
                "source": "unit-test",
                "snapshot": (
                    "Focused Window: Chrome\n"
                    "https://chatgpt.com/c/master-r2-bootstrap\n"
                    "{\"master_decision_version\":1}"
                ),
            },
        )
        self.store.finalize_intent(intent_id)
        return captured


class _Controller:
    def apply_plan(self, _plan):
        return {"status": "ADMITTED"}


class R2PhysicalMasterIntegrationTests(unittest.TestCase):
    def _store(self, root: Path, *, strict: bool = True) -> StateStore:
        store = StateStore(root / "state.sqlite3", [root])
        store.create_contract(
            "p",
            root_contract={"objective": "r2"},
            acceptance_contract={
                "required": [STRICT_ACCEPTANCE] if strict else []
            },
        )
        return store

    def _verify_binding(self, store: StateStore, *, daemon_epoch: int, url: str):
        current = store.get_browser_binding("p", "master")
        return store.rebind_browser(
            "p",
            "master",
            actor_id="A",
            conversation_url=url,
            predecessor_url=url,
            reason="PHYSICAL_SESSION_VERIFIED",
            evidence={
                "source": "READ_ONLY_PHYSICAL_VERIFY",
                "auth_status": "AUTHENTICATED",
                "driver_url": url,
                "physical_url": url,
                "session": "scorp-p0-conv-r2",
                "snapshot_sha256": "b" * 64,
                "binding_generation": int(current["generation"]),
                "daemon_epoch": int(daemon_epoch),
                "master_epoch": int(
                    store.get_project_state("p")["master_epoch"]
                ),
                "verified_at": "2026-10-01T11:00:00Z",
            },
        )

    def test_activation_snapshot_derives_strict_physical_gate_from_acceptance_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            try:
                store.start_master_session("p", "master-a", ttl_seconds=300)
                before = store.activation_snapshot("p", daemon_epoch=7)
                self.assertTrue(before.master_physical_required)
                self.assertFalse(before.master_physical_bound)
                self.assertFalse(before.master_physical_verified)

                url = "https://chatgpt.com/c/master-r2"
                store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=url,
                    predecessor_url=None,
                    reason="MASTER_REASONING_BOOTSTRAP",
                    evidence={"source": "bootstrap"},
                )
                bound = store.activation_snapshot("p", daemon_epoch=7)
                self.assertTrue(bound.master_physical_bound)
                self.assertFalse(bound.master_physical_verified)

                self._verify_binding(store, daemon_epoch=7, url=url)
                verified = store.activation_snapshot("p", daemon_epoch=7)
                self.assertTrue(verified.master_physical_verified)

                restarted = store.activation_snapshot("p", daemon_epoch=8)
                self.assertTrue(restarted.master_physical_bound)
                self.assertFalse(restarted.master_physical_verified)
            finally:
                store.close()

    def test_non_strict_contract_does_not_require_physical_master(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root, strict=False)
            try:
                snapshot = store.activation_snapshot("p", daemon_epoch=1)
                self.assertFalse(snapshot.master_physical_required)
            finally:
                store.close()

    def test_successful_bootstrap_reasoning_promotes_canonical_master_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            try:
                gateway = _BootstrapGateway(store)
                coordinator = MasterReasoningCoordinator(gateway, _Controller())
                result = coordinator.run_once()
                self.assertEqual("IDLE", result["status"])
                self.assertEqual(1, gateway.submit_calls)

                binding = store.get_browser_binding("p", "master")
                self.assertIsNotNone(binding)
                self.assertEqual(
                    "https://chatgpt.com/c/master-r2-bootstrap",
                    binding["conversation_url"],
                )
                self.assertEqual("A", binding["actor_id"])
                self.assertEqual(0, int(binding["generation"]))
                self.assertEqual(
                    "MASTER_REASONING_BOOTSTRAP",
                    binding["rebind_reason"],
                )
                evidence = json.loads(binding["evidence_json"])
                self.assertEqual(
                    "MASTER_REASONING_BOOTSTRAP",
                    evidence["source"],
                )
                self.assertEqual(result["intent_id"], evidence["intent_id"])
                self.assertEqual(
                    "remote-r2-bootstrap",
                    evidence["remote_identity"],
                )
            finally:
                store.close()

    def test_bootstrap_wait_replays_same_terminal_without_second_submit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            try:
                gateway = _BootstrapGateway(store)
                coordinator = MasterReasoningCoordinator(gateway, _Controller())
                first = coordinator.run_once()
                second = coordinator.run_once()
                self.assertEqual("IDLE", first["status"])
                self.assertEqual("IDLE", second["status"])
                self.assertEqual(first["intent_id"], second["intent_id"])
                self.assertEqual(1, gateway.submit_calls)
            finally:
                store.close()

    def test_runtime_status_surfaces_master_browser_and_physical_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            try:
                store.start_master_session("p", "master-a", ttl_seconds=300)
                lease = store.acquire_daemon_lease(
                    "p", "daemon-r2", ttl_seconds=300
                )
                url = "https://chatgpt.com/c/master-r2"
                store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=url,
                    predecessor_url=None,
                    reason="MASTER_REASONING_BOOTSTRAP",
                    evidence={"source": "bootstrap"},
                )
                self._verify_binding(
                    store,
                    daemon_epoch=int(lease["daemon_epoch"]),
                    url=url,
                )

                service = RuntimeCommandService(
                    store,
                    project_id="p",
                    daemon_epoch=int(lease["daemon_epoch"]),
                    actor="test",
                )
                master = service._master_status("p")
                self.assertEqual(
                    url,
                    master["browser_binding"]["conversation_url"],
                )
                self.assertTrue(master["physical"]["required"])
                self.assertTrue(master["physical"]["bound"])
                self.assertTrue(master["physical"]["verified"])

                runtime = service._runtime_status("p")
                self.assertEqual(
                    url,
                    runtime["master_browser_binding"]["conversation_url"],
                )
                self.assertTrue(runtime["master_physical"]["verified"])
            finally:
                store.close()

    def test_strict_acceptance_requires_bound_and_verified_physical_master(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            try:
                validator = AcceptanceValidator(store)
                missing = validator.evaluate("p", "a" * 40, {})
                self.assertIn(
                    "MASTER_PHYSICAL_BINDING_REQUIRED",
                    missing.blockers,
                )

                lease = store.acquire_daemon_lease(
                    "p", "daemon-r2", ttl_seconds=300
                )
                url = "https://chatgpt.com/c/master-r2"
                store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=url,
                    predecessor_url=None,
                    reason="MASTER_REASONING_BOOTSTRAP",
                    evidence={"source": "bootstrap"},
                )
                unverified = validator.evaluate("p", "a" * 40, {})
                self.assertNotIn(
                    "MASTER_PHYSICAL_BINDING_REQUIRED",
                    unverified.blockers,
                )
                self.assertIn(
                    "MASTER_PHYSICAL_VERIFICATION_REQUIRED",
                    unverified.blockers,
                )

                self._verify_binding(
                    store,
                    daemon_epoch=int(lease["daemon_epoch"]),
                    url=url,
                )
                verified = validator.evaluate("p", "a" * 40, {})
                self.assertFalse(
                    any(
                        blocker.startswith("MASTER_PHYSICAL_")
                        for blocker in verified.blockers
                    )
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
