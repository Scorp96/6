from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .models import canonical_json, sha256_json
from .state_store import utc_now


class MasterReasoningRejected(RuntimeError):
    """Fail-closed rejection for durable Master reasoning."""


class MasterReasoningCoordinator:
    """Durable exactly-once bridge from daemon wakeups to Logical Master A.

    Reuses action_intents/outbox and BrowserAdapter rather than creating a
    second queue. A semantic SQLite snapshot is hashed before reasoning; the
    captured MASTER_DECISION/1 must bind to that hash, state_version,
    master_epoch and operator/objective generations before application.
    """

    _ACTIVE_PROJECT_STATES = frozenset({"ACTIVE", "RUNNING"})
    _ACTIVE_OPERATOR_STATES = frozenset({"ACTIVE", "RUNNING"})
    _UNRESOLVED_STATES = frozenset({
        "PREPARED", "VERIFIED_NOT_SUBMITTED", "MAY_HAVE_SUBMITTED",
        "BLOCKED_AMBIGUOUS", "CONFIRMED_SUBMITTED", "RESPONSE_CAPTURED",
    })
    _ALLOWED_ACTIONS = frozenset({"APPLY_PLAN", "WAIT", "HUMAN_REQUIRED"})

    def __init__(self, gateway: Any, controller: Any) -> None:
        if gateway is None or not hasattr(gateway, "store") or not callable(
            getattr(gateway, "submit_intent", None)
        ):
            raise ValueError("MASTER_REASONING_GATEWAY_REQUIRED")
        if controller is None or not callable(getattr(controller, "apply_plan", None)):
            raise ValueError("MASTER_REASONING_CONTROLLER_REQUIRED")
        self.gateway = gateway
        self.store = gateway.store
        self.controller = controller
        self.project_id = str(getattr(gateway, "project_id", "") or "").strip()
        if not self.project_id:
            raise ValueError("MASTER_REASONING_PROJECT_REQUIRED")

    def semantic_snapshot(self) -> dict[str, Any]:
        """Stable semantic state; heartbeat-only timestamps are excluded."""
        def decode_object(raw: object, *, field: str) -> dict[str, Any]:
            try:
                value = json.loads(str(raw or "{}"))
            except (TypeError, ValueError) as exc:
                raise MasterReasoningRejected(
                    "MASTER_REASONING_" + field + "_INVALID"
                ) from exc
            if not isinstance(value, Mapping):
                raise MasterReasoningRejected(
                    "MASTER_REASONING_" + field + "_INVALID"
                )
            return dict(value)

        with self.store._connection() as conn:
            state = conn.execute(
                """SELECT project_id,state_version,master_epoch,status,phase,
                          completion_candidate_commit
                   FROM project_state WHERE project_id=?""",
                (self.project_id,),
            ).fetchone()
            control = conn.execute(
                """SELECT operator_state,operator_generation,objective_generation,
                          objective_sha256
                   FROM operator_controls WHERE project_id=?""",
                (self.project_id,),
            ).fetchone()
            contract = conn.execute(
                """SELECT root_contract_json,acceptance_contract_json,contract_sha256
                   FROM contracts WHERE project_id=?""",
                (self.project_id,),
            ).fetchone()
            if state is None or control is None or contract is None:
                raise MasterReasoningRejected("MASTER_REASONING_STATE_MISSING")
            task_rows = [dict(row) for row in conn.execute(
                """SELECT task_id,objective_sha256,resource_scope_json,task_context_json,
                          access_mode,required,state,result_sha256
                   FROM task_nodes WHERE project_id=? ORDER BY task_id""",
                (self.project_id,),
            )]
            tasks = []
            for row in task_rows:
                row["resource_scope"] = json.loads(str(row.pop("resource_scope_json")))
                row["task_context"] = decode_object(
                    row.pop("task_context_json"), field="TASK_CONTEXT"
                )
                tasks.append(row)
            assignments = [dict(row) for row in conn.execute(
                """SELECT assignment_id,task_id,worker_id,slot_id,master_epoch,
                          base_state_version,operator_generation,objective_generation,state
                   FROM assignments WHERE project_id=?
                   ORDER BY task_id,assignment_id""",
                (self.project_id,),
            )]
            leases = [dict(row) for row in conn.execute(
                """SELECT assignment_id,slot_id,master_epoch,state
                   FROM leases WHERE project_id=? ORDER BY assignment_id""",
                (self.project_id,),
            )]
            result_rows = [dict(row) for row in conn.execute(
                """SELECT result_id,assignment_id,master_epoch,result_kind,payload_json,
                          payload_sha256,verification_state,verified_result_sha256,created_at
                   FROM candidate_results WHERE project_id=?
                   ORDER BY created_at DESC,result_id DESC LIMIT 32""",
                (self.project_id,),
            )]
            results = []
            for row in reversed(result_rows):
                row["payload"] = decode_object(
                    row.pop("payload_json"), field="RESULT_PAYLOAD"
                )
                results.append(row)
            intent_rows = [dict(row) for row in conn.execute(
                """SELECT i.intent_id,i.action_kind,i.state,i.attempt,
                          i.response_json,i.response_sha256,i.ambiguity_reason,
                          o.state AS outbox_state,i.created_at
                   FROM action_intents i JOIN outbox o ON o.intent_id=i.intent_id
                   WHERE i.project_id=? AND i.action_kind<>'MASTER_REASONING'
                   ORDER BY i.created_at DESC,i.intent_id DESC LIMIT 32""",
                (self.project_id,),
            )]
            intents = []
            for row in reversed(intent_rows):
                raw_response = row.pop("response_json")
                row["response"] = (
                    decode_object(raw_response, field="INTENT_RESPONSE")
                    if raw_response
                    else None
                )
                intents.append(row)
            evidence = [dict(row) for row in conn.execute(
                """SELECT evidence_ref,acceptance_id,result,candidate_commit,
                          contract_sha256,artifact_sha256,observed_state_json,
                          raw_output_reference,finished_at
                   FROM evidence_receipts WHERE project_id=?
                   ORDER BY finished_at DESC,evidence_ref DESC LIMIT 32""",
                (self.project_id,),
            )]
            for row in evidence:
                row["observed_state"] = decode_object(
                    row.pop("observed_state_json"), field="EVIDENCE_STATE"
                )
            release = conn.execute(
                """SELECT candidate_commit,manifest_sha256,installed_commit,
                          installed_manifest_sha256,verified_at
                   FROM release_candidates WHERE project_id=?""",
                (self.project_id,),
            ).fetchone()
            daemon = conn.execute(
                """SELECT daemon_epoch,lease_status
                   FROM daemon_leases WHERE project_id=?
                   ORDER BY daemon_epoch DESC LIMIT 1""",
                (self.project_id,),
            ).fetchone()
            supervision = conn.execute(
                """SELECT restart_count,recovery_count,consecutive_failures,
                          restart_budget,circuit_state,block_reason
                   FROM daemon_supervision WHERE project_id=?""",
                (self.project_id,),
            ).fetchone()
            event_rows = [dict(row) for row in conn.execute(
                """SELECT event_id,kind,payload_json
                   FROM events
                   WHERE project_id=?
                     AND kind NOT IN (
                       'ACTIVATION_DECISION',
                       'MASTER_DECISION_APPLIED',
                       'MASTER_DECISION_STALE'
                     )
                   ORDER BY created_at DESC,event_id DESC LIMIT 32""",
                (self.project_id,),
            )]
            durable_events = []
            for row in reversed(event_rows):
                row["payload"] = decode_object(
                    row.pop("payload_json"), field="EVENT_PAYLOAD"
                )
                durable_events.append(row)
        return {
            "contract": {
                "root": decode_object(
                    contract["root_contract_json"], field="ROOT_CONTRACT"
                ),
                "acceptance": decode_object(
                    contract["acceptance_contract_json"], field="ACCEPTANCE_CONTRACT"
                ),
                "contract_sha256": str(contract["contract_sha256"]),
            },
            "project": dict(state),
            "operator": dict(control),
            "tasks": tasks,
            "assignments": assignments,
            "leases": leases,
            "results": results,
            "external_intents": intents,
            "evidence": evidence,
            "release": dict(release) if release is not None else None,
            "daemon": dict(daemon) if daemon is not None else None,
            "supervision": dict(supervision) if supervision is not None else None,
            "durable_events": durable_events,
        }

    def semantic_snapshot_sha256(self) -> str:
        return sha256_json(self.semantic_snapshot())

    def _latest_reasoning_intent(self) -> dict[str, Any] | None:
        with self.store._connection() as conn:
            row = conn.execute(
                """SELECT * FROM action_intents
                   WHERE project_id=? AND action_kind='MASTER_REASONING'
                   ORDER BY created_at DESC,intent_id DESC LIMIT 1""",
                (self.project_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    @staticmethod
    def _terminal_event_id(intent_id: str) -> str:
        return "master-reasoning-terminal-" + hashlib.sha256(
            str(intent_id).encode("utf-8")
        ).hexdigest()[:32]

    def _terminal_event(self, intent_id: str) -> dict[str, Any] | None:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id=? AND project_id=?",
                (self._terminal_event_id(intent_id), self.project_id),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        try:
            value["payload"] = json.loads(str(value["payload_json"]))
        except (TypeError, ValueError) as exc:
            raise MasterReasoningRejected("MASTER_REASONING_EVENT_INVALID") from exc
        return value

    def _latest_applied_event(self) -> dict[str, Any] | None:
        with self.store._connection() as conn:
            row = conn.execute(
                """SELECT * FROM events
                   WHERE project_id=? AND kind='MASTER_DECISION_APPLIED'
                   ORDER BY created_at DESC,event_id DESC LIMIT 1""",
                (self.project_id,),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(str(value["payload_json"]))
        return value

    def reasoning_required(self) -> bool:
        state = self.store.get_project_state(self.project_id)
        control = self.store.get_operator_control(self.project_id)
        if str(state.get("status") or "") not in self._ACTIVE_PROJECT_STATES:
            return False
        if str(control.get("operator_state") or "") not in self._ACTIVE_OPERATOR_STATES:
            return False
        latest = self._latest_reasoning_intent()
        if latest is not None:
            terminal = self._terminal_event(str(latest["intent_id"]))
            if terminal is None and str(latest.get("state") or "") in self._UNRESOLVED_STATES:
                return True
            if terminal is not None and str(terminal["payload"].get("status") or "") == "STALE":
                return True
        applied = self._latest_applied_event()
        if applied is None:
            return True
        expected = str(applied["payload"].get("output_snapshot_sha256") or "")
        return not expected or expected != self.semantic_snapshot_sha256()

    def _binding_and_prompt(self) -> tuple[dict[str, Any], str, str]:
        snapshot = self.semantic_snapshot()
        snapshot_sha = sha256_json(snapshot)
        state = snapshot["project"]
        control = snapshot["operator"]
        material = {
            "project_id": self.project_id,
            "master_epoch": int(state["master_epoch"]),
            "base_state_version": int(state["state_version"]),
            "operator_generation": int(control["operator_generation"]),
            "objective_generation": int(control["objective_generation"]),
            "input_snapshot_sha256": snapshot_sha,
        }
        intent_id = "master-reasoning-" + sha256_json(material)[:32]
        binding = {**material, "intent_id": intent_id}
        prompt = json.dumps(
            {
                "protocol": "SCORP V4 MASTER_REASONING/1",
                "role": "Logical Master A",
                "reasoning_binding": binding,
                "durable_snapshot": snapshot,
                "instructions": [
                    "Return exactly one JSON object and no prose.",
                    "Set master_decision_version=1 and copy every reasoning_binding field exactly.",
                    "Choose action from APPLY_PLAN, WAIT, HUMAN_REQUIRED.",
                    "Never declare PROJECT_COMPLETE from model judgment; deterministic acceptance owns completion.",
                    "Use APPLY_PLAN only when durable state requires a new or revised task graph.",
                    "For APPLY_PLAN include plan with project_id, master_identity='A', tasks, and optional transition_id.",
                    "Use WAIT only when existing durable work should continue without a new plan.",
                    "Use HUMAN_REQUIRED only for genuine approval, irreducible ambiguity, safety, or budget decisions.",
                ],
                "required_response": "MASTER_DECISION/1",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return binding, prompt, intent_id

    def _prepare(self) -> dict[str, Any]:
        binding, prompt, intent_id = self._binding_and_prompt()
        control = self.store.get_operator_control(self.project_id)
        payload = {
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "reasoning_binding": binding,
            "required_response": "MASTER_DECISION/1",
            "operator_generation": int(control["operator_generation"]),
            "objective_generation": int(control["objective_generation"]),
        }
        return self.store.prepare_intent(
            self.project_id,
            intent_id,
            actor_id="A",
            channel="master",
            action_kind="MASTER_REASONING",
            payload=payload,
        )

    def _load_binding(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(str(intent.get("payload_json") or "{}"))
        except (TypeError, ValueError) as exc:
            raise MasterReasoningRejected("MASTER_REASONING_PAYLOAD_INVALID") from exc
        binding = payload.get("reasoning_binding") if isinstance(payload, Mapping) else None
        if not isinstance(binding, Mapping):
            raise MasterReasoningRejected("MASTER_REASONING_BINDING_MISSING")
        return dict(binding)

    def _decision(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        if str(intent.get("state") or "") != "RESPONSE_CAPTURED":
            raise MasterReasoningRejected("MASTER_REASONING_RESPONSE_NOT_CAPTURED")
        try:
            value = json.loads(str(intent.get("response_json") or "{}"))
        except (TypeError, ValueError) as exc:
            raise MasterReasoningRejected("MASTER_DECISION_INVALID_JSON") from exc
        if not isinstance(value, Mapping):
            raise MasterReasoningRejected("MASTER_DECISION_NOT_OBJECT")
        decision = dict(value)
        binding = self._load_binding(intent)
        if str(decision.get("master_decision_version") or "") != "1":
            raise MasterReasoningRejected("MASTER_DECISION_VERSION_INVALID")
        for key in (
            "project_id", "intent_id", "master_epoch", "base_state_version",
            "operator_generation", "objective_generation", "input_snapshot_sha256",
        ):
            if key not in decision:
                raise MasterReasoningRejected("MASTER_DECISION_BINDING_MISSING:" + key)
            actual = decision[key]
            expected = binding[key]
            try:
                matched = (
                    int(actual) == int(expected)
                    if key in {"master_epoch", "base_state_version", "operator_generation", "objective_generation"}
                    else str(actual) == str(expected)
                )
            except (TypeError, ValueError):
                matched = False
            if not matched:
                raise MasterReasoningRejected("MASTER_DECISION_BINDING_MISMATCH:" + key)
        action = str(decision.get("action") or "").strip().upper()
        if action not in self._ALLOWED_ACTIONS:
            raise MasterReasoningRejected("MASTER_DECISION_ACTION_INVALID")
        decision["action"] = action
        if not str(decision.get("reason") or "").strip():
            raise MasterReasoningRejected("MASTER_DECISION_REASON_REQUIRED")
        if action == "APPLY_PLAN" and not isinstance(decision.get("plan"), Mapping):
            raise MasterReasoningRejected("MASTER_DECISION_PLAN_REQUIRED")
        return decision

    def _record_terminal(
        self,
        *,
        intent_id: str,
        kind: str,
        status: str,
        input_snapshot_sha256: str,
        output_snapshot_sha256: str,
        decision: Mapping[str, Any] | None,
        result: Mapping[str, Any] | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        event_id = self._terminal_event_id(intent_id)
        payload = {
            "intent_id": intent_id,
            "status": str(status),
            "input_snapshot_sha256": str(input_snapshot_sha256),
            "output_snapshot_sha256": str(output_snapshot_sha256),
            "decision_sha256": sha256_json(dict(decision)) if decision is not None else None,
            "action": str(decision.get("action") or "") if decision is not None else None,
            "result": dict(result or {}),
            "reason": str(reason or "") or None,
        }
        payload_text = canonical_json(payload)
        stamp = utc_now()
        with self.store._transaction() as conn:
            existing = conn.execute(
                "SELECT kind,payload_json FROM events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["kind"]) != kind or str(existing["payload_json"]) != payload_text:
                    raise MasterReasoningRejected("MASTER_REASONING_TERMINAL_CONFLICT")
                return payload
            conn.execute(
                """INSERT INTO events(
                       event_id,project_id,kind,payload_json,processed,created_at,processed_at
                   ) VALUES(?,?,?,?,1,?,?)""",
                (event_id, self.project_id, kind, payload_text, stamp, stamp),
            )
        return payload

    def _current_binding_matches(self, binding: Mapping[str, Any]) -> bool:
        state = self.store.get_project_state(self.project_id)
        control = self.store.get_operator_control(self.project_id)
        return (
            int(state["master_epoch"]) == int(binding["master_epoch"])
            and int(state["state_version"]) == int(binding["base_state_version"])
            and int(control["operator_generation"]) == int(binding["operator_generation"])
            and int(control["objective_generation"]) == int(binding["objective_generation"])
            and self.semantic_snapshot_sha256() == str(binding["input_snapshot_sha256"])
        )

    @staticmethod
    def _definitely_not_submitted(intent: Mapping[str, Any]) -> bool:
        if str(intent.get("state") or "") != "BLOCKED_AMBIGUOUS":
            return False
        reason = str(intent.get("ambiguity_reason") or "")
        if not reason.startswith("OPERATOR_GENERATION_FENCED:"):
            return False
        try:
            observation = json.loads(str(intent.get("observation_json") or "{}"))
        except (TypeError, ValueError):
            return False
        return (
            isinstance(observation, Mapping)
            and str(observation.get("side_effect") or "") == "NOT_ATTEMPTED"
        )

    def _retire_pre_io_stale_intent(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        intent_id = str(intent["intent_id"])
        binding = self._load_binding(intent)
        self.store.fence_ambiguous_intent(
            intent_id,
            reason="MASTER_REASONING_PRE_IO_AUTHORITY_FENCED",
            observation={
                "side_effect": "NOT_ATTEMPTED",
                "source_reason": str(intent.get("ambiguity_reason") or ""),
            },
        )
        current_sha = self.semantic_snapshot_sha256()
        self._record_terminal(
            intent_id=intent_id,
            kind="MASTER_DECISION_STALE",
            status="STALE",
            input_snapshot_sha256=str(binding["input_snapshot_sha256"]),
            output_snapshot_sha256=current_sha,
            decision=None,
            result={},
            reason="MASTER_REASONING_PRE_IO_AUTHORITY_FENCED",
        )
        return {
            "status": "STALE",
            "reason": "MASTER_REASONING_PRE_IO_AUTHORITY_FENCED",
            "intent_id": intent_id,
        }

    def run_once(self) -> dict[str, Any]:
        latest = self._latest_reasoning_intent()
        if latest is not None and self._terminal_event(str(latest["intent_id"])) is not None:
            latest = None
        intent = latest if latest is not None else self._prepare()
        intent_id = str(intent["intent_id"])
        state = str(intent.get("state") or "")

        if self._definitely_not_submitted(intent):
            return self._retire_pre_io_stale_intent(intent)

        if state in {"MAY_HAVE_SUBMITTED", "BLOCKED_AMBIGUOUS", "CONFIRMED_SUBMITTED"}:
            intent = self.gateway.adapter.reconcile(intent_id)
            state = str(intent.get("state") or "")
        elif state in {"PREPARED", "VERIFIED_NOT_SUBMITTED"}:
            intent = self.gateway.submit_intent(intent_id)
            state = str(intent.get("state") or "")

        if state == "FENCED_AMBIGUOUS":
            return {"status": "BLOCKED", "reason": "MASTER_REASONING_INTENT_FENCED_AMBIGUOUS", "intent_id": intent_id}
        if state != "RESPONSE_CAPTURED":
            return {
                "status": "WAITING",
                "reason": "MASTER_REASONING_RESPONSE_PENDING",
                "intent_id": intent_id,
                "intent_state": state,
            }

        # A crash can occur after response capture but before outbox cleanup.
        # Finalization is idempotent and must precede decision application so
        # recovery cannot leave a durable PENDING_CLEANUP blocker behind.
        self.store.finalize_intent(intent_id)
        intent = self.store.get_intent(intent_id)
        decision = self._decision(intent)
        binding = self._load_binding(intent)
        if not self._current_binding_matches(binding):
            current_sha = self.semantic_snapshot_sha256()
            self._record_terminal(
                intent_id=intent_id,
                kind="MASTER_DECISION_STALE",
                status="STALE",
                input_snapshot_sha256=str(binding["input_snapshot_sha256"]),
                output_snapshot_sha256=current_sha,
                decision=decision,
                result={},
                reason="MASTER_REASONING_STATE_CHANGED",
            )
            return {"status": "STALE", "reason": "MASTER_REASONING_STATE_CHANGED", "intent_id": intent_id}

        action = str(decision["action"])
        if action == "APPLY_PLAN":
            applied = self.controller.apply_plan(dict(decision["plan"]))
            result = dict(applied) if isinstance(applied, Mapping) else {"value": str(applied)}
            status = "APPLIED"
        elif action == "WAIT":
            result = {"status": "WAIT"}
            status = "IDLE"
        else:
            result = {"status": "HUMAN_REQUIRED"}
            status = "BLOCKED"

        output_sha = self.semantic_snapshot_sha256()
        self._record_terminal(
            intent_id=intent_id,
            kind="MASTER_DECISION_APPLIED",
            status="APPLIED" if action != "HUMAN_REQUIRED" else "HUMAN_REQUIRED",
            input_snapshot_sha256=str(binding["input_snapshot_sha256"]),
            output_snapshot_sha256=output_sha,
            decision=decision,
            result=result,
            reason=str(decision["reason"]),
        )
        response = {
            "status": status,
            "action": action,
            "intent_id": intent_id,
            "input_snapshot_sha256": str(binding["input_snapshot_sha256"]),
            "output_snapshot_sha256": output_sha,
            "result": result,
        }
        if action == "HUMAN_REQUIRED":
            response["reason"] = "HUMAN_APPROVAL_REQUIRED:" + str(decision["reason"])
        return response
