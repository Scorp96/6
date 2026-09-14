from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import sqlite3
import sys
import time
from collections.abc import Mapping
from typing import Any

from .browser_adapter import BrowserAdapter
from .models import canonical_json, sha256_json
from .path_policy import PathPolicy
from .scheduler import Scheduler
from .state_store import StateStore, UTC


class SoakEvidenceError(RuntimeError):
    pass


def _stamp(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def initial_not_run_receipt(*, requested_duration_seconds: float) -> dict[str, Any]:
    return {
        "format": "scorp-v4-short-soak-receipt/1",
        "status": "NOT_RUN",
        "requested_duration_seconds": float(requested_duration_seconds),
        "actual_duration_seconds": 0.0,
        "long_duration_stability_proven": False,
        "reason": "The real short performance and recovery run has not started.",
    }


def validate_short_soak_receipt(receipt: Mapping[str, Any]) -> str:
    if receipt.get("format") != "scorp-v4-short-soak-receipt/1":
        raise SoakEvidenceError("SHORT_SOAK_FORMAT_INVALID")
    status = str(receipt.get("status") or "")
    if status == "NOT_RUN":
        if receipt.get("long_duration_stability_proven") is not False:
            raise SoakEvidenceError("LONG_STABILITY_CLAIM_INVALID")
        return status
    if status != "PASS":
        raise SoakEvidenceError("SHORT_SOAK_STATUS_INVALID")
    required = (
        "requested_duration_seconds", "actual_duration_seconds", "started_at", "ended_at",
        "task_cycles", "tasks_verified", "throughput_tasks_per_second", "concurrency_peak",
        "slot_reuse_count", "scheduler_recoveries", "max_recovery_seconds",
        "recovery_limit_seconds", "browser_intents", "submit_attempts",
        "duplicate_submits", "errors", "environment", "long_duration_stability_proven",
    )
    if any(key not in receipt or receipt[key] is None for key in required):
        raise SoakEvidenceError("SHORT_SOAK_RECEIPT_INCOMPLETE")
    requested = float(receipt["requested_duration_seconds"])
    actual = float(receipt["actual_duration_seconds"])
    if requested < 1.0 or actual < requested:
        raise SoakEvidenceError("SHORT_SOAK_DURATION_TOO_SMALL")
    if int(receipt["task_cycles"]) < 1 or int(receipt["tasks_verified"]) != int(receipt["task_cycles"]) * 3:
        raise SoakEvidenceError("SHORT_SOAK_TASK_ACCOUNTING_INVALID")
    if float(receipt["throughput_tasks_per_second"]) <= 0:
        raise SoakEvidenceError("SHORT_SOAK_THROUGHPUT_INVALID")
    if int(receipt["concurrency_peak"]) != 2:
        raise SoakEvidenceError("SHORT_SOAK_CONCURRENCY_NOT_TWO")
    if int(receipt["slot_reuse_count"]) < 1:
        raise SoakEvidenceError("SHORT_SOAK_SLOT_REUSE_MISSING")
    if int(receipt["scheduler_recoveries"]) < 1:
        raise SoakEvidenceError("SHORT_SOAK_RECOVERY_MISSING")
    if float(receipt["max_recovery_seconds"]) > float(receipt["recovery_limit_seconds"]):
        raise SoakEvidenceError("SHORT_SOAK_RECOVERY_BOUND_EXCEEDED")
    if int(receipt["browser_intents"]) != int(receipt["task_cycles"]):
        raise SoakEvidenceError("SHORT_SOAK_BROWSER_ACCOUNTING_INVALID")
    if int(receipt["submit_attempts"]) != int(receipt["browser_intents"]):
        raise SoakEvidenceError("SHORT_SOAK_SUBMIT_ACCOUNTING_INVALID")
    if int(receipt["duplicate_submits"]) != 0:
        raise SoakEvidenceError("SHORT_SOAK_DUPLICATE_SUBMIT")
    if int(receipt["errors"]) != 0:
        raise SoakEvidenceError("SHORT_SOAK_ERRORS_NONZERO")
    environment = receipt["environment"]
    if not isinstance(environment, Mapping) or not all(environment.get(key) for key in ("os", "python", "cpu_count", "sqlite", "sqlite_pragmas")):
        raise SoakEvidenceError("SHORT_SOAK_ENVIRONMENT_INCOMPLETE")
    if receipt["long_duration_stability_proven"] is not False:
        raise SoakEvidenceError("LONG_STABILITY_CLAIM_INVALID")
    return status


class _SoakEngine:
    def __init__(self, store: StateStore, intent_id: str):
        self.store = store
        self.intent_id = intent_id
        self.submit_count = 0

    def auth_state(self, channel: str) -> dict[str, str]:
        return {"status": "AUTHENTICATED", "channel": channel, "engine": "SHORT_SOAK_FAKE_BROWSER"}

    def submit(self, intent: Mapping[str, Any]) -> dict[str, str]:
        if self.store.get_intent(self.intent_id)["state"] != "MAY_HAVE_SUBMITTED":
            raise SoakEvidenceError("SUBMIT_WITHOUT_DURABLE_INTENT")
        self.submit_count += 1
        return {
            "status": "SUBMITTED",
            "conversation_url": f"https://chatgpt.com/c/short-soak-{self.intent_id}",
            "remote_identity": f"remote-{self.intent_id}",
        }

    def reconcile(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": "RESPONSE_CAPTURED",
            "conversation_url": f"https://chatgpt.com/c/short-soak-{self.intent_id}",
            "remote_identity": f"remote-{self.intent_id}",
            "response": {"kind": "HANDOFF", "intent_id": self.intent_id},
        }


def _run_cycle(
    store: StateStore,
    root: pathlib.Path,
    scheduler: Scheduler,
    cycle: int,
    *,
    recover: bool,
) -> dict[str, Any]:
    source = root / "benchmark-input.csv"
    report = root / f"benchmark-report-{cycle}.json"
    prefix = f"C{cycle:06d}"
    tasks = [
        {
            "task_id": f"{prefix}-T1",
            "objective_sha256": sha256_json({"cycle": cycle, "task": "T1"}),
            "resource_scope": [source],
            "access_mode": "read",
            "dependencies": [],
        },
        {
            "task_id": f"{prefix}-T2",
            "objective_sha256": sha256_json({"cycle": cycle, "task": "T2"}),
            "resource_scope": [source],
            "access_mode": "read",
            "dependencies": [],
        },
        {
            "task_id": f"{prefix}-T3",
            "objective_sha256": sha256_json({"cycle": cycle, "task": "T3"}),
            "resource_scope": [report],
            "access_mode": "write",
            "dependencies": [f"{prefix}-T1", f"{prefix}-T2"],
        },
    ]
    scheduler.enqueue_graph(tasks)
    now = dt.datetime.now(UTC)
    claims = scheduler.claim_runnable(master_epoch=0, now=now, lease_seconds=1)
    if len(claims) != 2:
        raise SoakEvidenceError("SHORT_SOAK_CONCURRENT_ADMISSION_FAILED")
    recoveries = 0
    recovery_seconds = 0.0
    if recover:
        started = time.perf_counter()
        recoveries = scheduler.recover_expired_leases(now=now + dt.timedelta(seconds=2))
        claims = scheduler.claim_runnable(master_epoch=0, now=now + dt.timedelta(seconds=2))
        recovery_seconds = time.perf_counter() - started
        if recoveries < 1 or len(claims) != 2:
            raise SoakEvidenceError("SHORT_SOAK_SCHEDULER_RECOVERY_FAILED")
    slots = [claim.slot_id for claim in claims]
    for claim in claims:
        result_id = scheduler.record_candidate(
            claim.assignment_id,
            lease_token=claim.lease_token,
            master_epoch=claim.master_epoch,
            kind="HANDOFF",
            payload={"cycle": cycle, "task_id": claim.task_id, "result_sha256": claim.objective_sha256},
            now=(now + dt.timedelta(seconds=2)) if recover else now,
        )
        scheduler.verify_candidate(result_id, result_sha256=claim.objective_sha256)
    final = scheduler.claim_runnable(master_epoch=0)
    if len(final) != 1 or final[0].task_id != f"{prefix}-T3":
        raise SoakEvidenceError("SHORT_SOAK_DEPENDENCY_RELEASE_FAILED")
    last = final[0]
    result_id = scheduler.record_candidate(
        last.assignment_id,
        lease_token=last.lease_token,
        master_epoch=last.master_epoch,
        kind="HANDOFF",
        payload={"cycle": cycle, "task_id": last.task_id, "result_sha256": last.objective_sha256},
    )
    scheduler.verify_candidate(result_id, result_sha256=last.objective_sha256)
    slots.append(last.slot_id)

    intent_id = f"short-soak-intent-{cycle:06d}"
    store.prepare_intent(
        "short-soak",
        intent_id,
        actor_id="A",
        channel="master",
        action_kind="CHATGPT_SUBMIT",
        payload={"marker": sha256_json({"cycle": cycle, "intent": intent_id})},
    )
    engine = _SoakEngine(store, intent_id)
    adapter = BrowserAdapter(store, engine)
    adapter.submit_once(intent_id)
    adapter.reconcile(intent_id)
    adapter.submit_once(intent_id)
    if engine.submit_count != 1:
        raise SoakEvidenceError("SHORT_SOAK_DUPLICATE_SUBMIT")
    return {
        "slots": slots,
        "recoveries": recoveries,
        "recovery_seconds": recovery_seconds,
        "submit_attempts": engine.submit_count,
    }


def run_short_soak(
    lab_root: str | pathlib.Path,
    *,
    duration_seconds: float,
    recovery_limit_seconds: float = 2.0,
) -> dict[str, Any]:
    requested = float(duration_seconds)
    if requested < 1.0:
        raise SoakEvidenceError("SHORT_SOAK_DURATION_TOO_SMALL")
    root = pathlib.Path(lab_root).resolve(strict=True)
    if not root.is_dir():
        raise SoakEvidenceError("SHORT_SOAK_LAB_ROOT_INVALID")
    source = root / "benchmark-input.csv"
    if not source.exists():
        source.write_text("order_id,category,amount\nS-1,benchmark,1.00\n", encoding="utf-8", newline="\n")
    database = root / "short-soak.sqlite3"
    if database.exists():
        raise SoakEvidenceError("SHORT_SOAK_DATABASE_ALREADY_EXISTS")
    store = StateStore(database, allowed_roots=[root])
    store.create_contract(
        "short-soak",
        root_contract={"objective": "short performance and recovery pressure test"},
        acceptance_contract={"required": ["AC12_SHORT_SOAK_PERFORMANCE"]},
    )
    scheduler = Scheduler(store, "short-soak", PathPolicy([root]), max_workers=2)
    started_wall = dt.datetime.now(UTC)
    started = time.perf_counter()
    cycle = 0
    slot_uses: dict[str, int] = {}
    scheduler_recoveries = 0
    max_recovery_seconds = 0.0
    submit_attempts = 0
    try:
        while time.perf_counter() - started < requested:
            cycle += 1
            result = _run_cycle(store, root, scheduler, cycle, recover=(cycle == 1))
            for slot in result["slots"]:
                slot_uses[slot] = slot_uses.get(slot, 0) + 1
            scheduler_recoveries += int(result["recoveries"])
            max_recovery_seconds = max(max_recovery_seconds, float(result["recovery_seconds"]))
            submit_attempts += int(result["submit_attempts"])
    finally:
        store.close()
    ended = time.perf_counter()
    ended_wall = dt.datetime.now(UTC)
    actual = ended - started
    tasks_verified = cycle * 3
    slot_reuse_count = sum(max(0, count - 1) for count in slot_uses.values())
    receipt = {
        "format": "scorp-v4-short-soak-receipt/1",
        "status": "PASS",
        "requested_duration_seconds": requested,
        "actual_duration_seconds": actual,
        "started_at": _stamp(started_wall),
        "ended_at": _stamp(ended_wall),
        "task_cycles": cycle,
        "tasks_verified": tasks_verified,
        "throughput_tasks_per_second": tasks_verified / actual,
        "concurrency_peak": 2,
        "slot_reuse_count": slot_reuse_count,
        "scheduler_recoveries": scheduler_recoveries,
        "max_recovery_seconds": max_recovery_seconds,
        "recovery_limit_seconds": float(recovery_limit_seconds),
        "browser_intents": cycle,
        "submit_attempts": submit_attempts,
        "duplicate_submits": 0,
        "errors": 0,
        "database": str(database),
        "database_sha256": _file_sha256(database),
        "environment": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count() or 1,
            "processor": platform.processor() or "unknown",
            "sqlite": sqlite3.sqlite_version,
            "sqlite_pragmas": StateStore(database, allowed_roots=[root]).connection_settings(),
        },
        "browser_scope": "INJECTED_FAKE_ENGINE_REAL_ADAPTER_STATE_MACHINE",
        "long_duration_stability_proven": False,
    }
    StateStore(database, allowed_roots=[root]).close()
    validate_short_soak_receipt(receipt)
    return receipt


def _file_sha256(path: pathlib.Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SCORP V4 short performance/recovery soak")
    parser.add_argument("--lab-root", type=pathlib.Path, required=True)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    receipt = run_short_soak(args.lab_root, duration_seconds=args.duration_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(receipt) + "\n", encoding="utf-8", newline="\n")
    print(canonical_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
