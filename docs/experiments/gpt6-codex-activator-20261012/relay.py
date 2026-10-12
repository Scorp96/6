"""Isolated, deterministic activation-and-receipt control plane.

GPT performs the work. Codex may claim, attempt activation through a separate
approved adapter, and record observations. This module does not contact GPT,
open Chrome, bypass authentication, or claim a successful remote wake-up.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

ID_RE = re.compile(r"[A-Za-z0-9_.:-]{3,128}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
URL_RE = re.compile(r"https://chatgpt\.com/c/[A-Za-z0-9-]+\Z")
STATES = ("PREPARED", "CLAIMED", "MAY_HAVE_SUBMITTED", "OBSERVED_UNVERIFIED")
ZERO = "0" * 64


class GateError(RuntimeError):
    pass


def required_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise GateError(f"{label}_INVALID")
    return value


def required_hash(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise GateError(f"{label}_INVALID")
    return value


def required_url(value: str) -> str:
    if not isinstance(value, str) or not URL_RE.fullmatch(value):
        raise GateError("PINNED_CONVERSATION_URL_INVALID")
    return value


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Relay:
    def __init__(self, db_path: str | Path):
        path = Path(db_path).expanduser().resolve()
        if not path.parent.is_dir():
            raise GateError("DATABASE_PARENT_MISSING")
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS activations (
                request_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                conversation_url TEXT NOT NULL,
                master_epoch INTEGER NOT NULL CHECK(master_epoch > 0),
                turn_no INTEGER NOT NULL CHECK(turn_no > 0),
                prompt_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN
                    ('PREPARED','CLAIMED','MAY_HAVE_SUBMITTED','OBSERVED_UNVERIFIED')),
                claimed_by TEXT,
                lease_until INTEGER,
                result_sha256 TEXT,
                receipt_sha256 TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(project_id, master_epoch, turn_no)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS journal (
                event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL REFERENCES activations(request_id),
                event_name TEXT NOT NULL,
                event_json TEXT NOT NULL,
                previous_sha256 TEXT NOT NULL,
                event_sha256 TEXT NOT NULL,
                written_at INTEGER NOT NULL
            )
        """)

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def row(self, request_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM activations WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise GateError("REQUEST_NOT_FOUND")
        return dict(row)

    def event(self, request_id: str, event_name: str, detail: dict) -> str:
        recent = self.db.execute(
            "SELECT event_sha256 FROM journal WHERE request_id=? "
            "ORDER BY event_seq DESC LIMIT 1", (request_id,)
        ).fetchone()
        previous = recent[0] if recent else ZERO
        written_at = int(time.time())
        record = {
            "request_id": request_id,
            "event": event_name,
            "details": detail,
            "previous_sha256": previous,
            "written_at": written_at,
        }
        payload = canonical(record)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.db.execute(
            "INSERT INTO journal(request_id,event_name,event_json,previous_sha256,event_sha256,written_at)"
            " VALUES(?,?,?,?,?,?)",
            (request_id, event_name, payload, previous, digest, written_at),
        )
        return digest

    def prepare(self, *, request_id: str, project_id: str, conversation_url: str,
                master_epoch: int, turn_no: int, prompt_sha256: str) -> dict:
        rid = required_id(request_id, "REQUEST_ID")
        project = required_id(project_id, "PROJECT_ID")
        url = required_url(conversation_url)
        prompt = required_hash(prompt_sha256, "PROMPT_SHA256")
        if type(master_epoch) is not int or master_epoch <= 0:
            raise GateError("MASTER_EPOCH_INVALID")
        if type(turn_no) is not int or turn_no <= 0:
            raise GateError("TURN_NUMBER_INVALID")
        with self.tx():
            existing = self.db.execute(
                "SELECT * FROM activations WHERE request_id=?", (rid,)
            ).fetchone()
            if existing:
                obj = dict(existing)
                identity = (project, url, master_epoch, turn_no, prompt)
                if tuple(obj[k] for k in (
                    "project_id", "conversation_url", "master_epoch",
                    "turn_no", "prompt_sha256"
                )) != identity:
                    raise GateError("REQUEST_IDENTITY_COLLISION")
                return obj
            self.db.execute(
                "INSERT INTO activations (request_id,project_id,conversation_url,"
                "master_epoch,turn_no,prompt_sha256,state,created_at) "
                "VALUES (?,?,?,?,?,?,'PREPARED',?)",
                (rid, project, url, master_epoch, turn_no, prompt, int(time.time()))
            )
            self.event(rid, "PREPARED", {
                "project_id": project, "conversation_url": url,
                "master_epoch": master_epoch, "turn_no": turn_no,
                "prompt_sha256": prompt
            })
            return self.row(rid)

    def claim(self, request_id: str, activator_id: str, lease_seconds: int = 300) -> dict:
        rid = required_id(request_id, "REQUEST_ID")
        who = required_id(activator_id, "ACTIVATOR_ID")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise GateError("LEASE_DURATION_INVALID")
        now = int(time.time())
        with self.tx():
            record = self.row(rid)
            if record["state"] == "CLAIMED" and record["claimed_by"] == who \
                    and record["lease_until"] > now:
                return record
            if record["state"] == "CLAIMED" and record["lease_until"] > now:
                raise GateError("ACTIVATION_ALREADY_CLAIMED")
            if record["state"] not in {"CLAIMED", "PREPARED"}:
                raise GateError("ACTIVATION_UNCERTAIN_NO_RETRY")
            self.db.execute(
                "UPDATE activations SET state='CLAIMED', claimed_by=?, lease_until=? "
                "WHERE request_id=?", (who, now + lease_seconds, rid)
            )
            self.event(rid, "CLAIMED", {
                "activator_id": who, "lease_until": now + lease_seconds,
                "prior_state": record["state"]
            })
            return self.row(rid)

    def arm(self, request_id: str, activator_id: str) -> dict:
        """Durably record possible browser side effect BEFORE any send."""
        rid = required_id(request_id, "REQUEST_ID")
        who = required_id(activator_id, "ACTIVATOR_ID")
        with self.tx():
            record = self.row(rid)
            if record["state"] != "CLAIMED" or record["claimed_by"] != who:
                raise GateError("CLAIM_BINDING_INVALID")
            if record["lease_until"] <= int(time.time()):
                raise GateError("CLAIM_LEASE_EXPIRED")
            self.db.execute(
                "UPDATE activations SET state='MAY_HAVE_SUBMITTED' WHERE request_id=?",
                (rid,)
            )
            self.event(rid, "MAY_HAVE_SUBMITTED", {
                "activator_id": who,
                "conversation_url": record["conversation_url"],
                "epoch": record["master_epoch"],
                "turn": record["turn_no"]
            })
            return self.row(rid)

    def record_observation(self, request_id: str, activator_id: str,
                           observed_url: str, response_sha256: str,
                           observation_ref: str) -> dict:
        """Record claimed external proof; NOT independent proof of real GPT output."""
        rid = required_id(request_id, "REQUEST_ID")
        who = required_id(activator_id, "ACTIVATOR_ID")
        url = required_url(observed_url)
        sha = required_hash(response_sha256, "RESPONSE_SHA256")
        ref = required_id(observation_ref, "OBSERVATION_REF")
        with self.tx():
            record = self.row(rid)
            if record["state"] != "MAY_HAVE_SUBMITTED":
                raise GateError("NOT_AWAITING_BROWSER_RESULT")
            if record["claimed_by"] != who:
                raise GateError("ACTIVATOR_IDENTITY_MISMATCH")
            if record["conversation_url"] != url:
                raise GateError("CONVERSATION_BINDING_MISMATCH")
            receipt = self.event(rid, "OBSERVED_UNVERIFIED", {
                "activator_id": who, "response_sha256": sha,
                "observation_ref": ref, "conversation_url": url
            })
            self.db.execute(
                "UPDATE activations SET state='OBSERVED_UNVERIFIED',"
                "result_sha256=?, receipt_sha256=? WHERE request_id=?",
                (sha, receipt, rid)
            )
            return self.row(rid)

    def inspect(self, request_id: str) -> dict:
        record = self.row(required_id(request_id, "REQUEST_ID"))
        record["safe_to_retry_send"] = False
        record["next_action"] = {
            "PREPARED": "CLAIM_ONLY",
            "CLAIMED": "ARM_ONLY_AFTER_AUTH_AND_BROWSER_BINDING",
            "MAY_HAVE_SUBMITTED": "RECONCILE_READ_ONLY_NO_SEND",
            "OBSERVED_UNVERIFIED": "GPT_REVIEW_EXTERNAL_EVIDENCE"
        }[record["state"]]
        return record

    def verify_journal(self, request_id: str) -> dict:
        rid = required_id(request_id, "REQUEST_ID")
        self.row(rid)
        records = self.db.execute(
            "SELECT * FROM journal WHERE request_id=? ORDER BY event_seq", (rid,)
        ).fetchall()
        prev = ZERO
        for n, line in enumerate(records, 1):
            try:
                event = json.loads(line["event_json"])
            except (TypeError, ValueError) as exc:
                raise GateError(f"JOURNAL_HASH_CHAIN_BROKEN_AT_{n}") from exc
            if not isinstance(event, dict) or event.get("request_id") != rid or event.get("previous_sha256") != prev or event.get("written_at") != line["written_at"] \
                    or line["previous_sha256"] != prev \
                    or hashlib.sha256(line["event_json"].encode("utf-8")).hexdigest() != line["event_sha256"]:
                raise GateError(f"JOURNAL_HASH_CHAIN_BROKEN_AT_{n}")
            prev = line["event_sha256"]
        return {"request_id": rid, "event_count": len(records),
                "last_event_sha256": prev, "chain_valid": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="op", required=True)
    prepare = sub.add_parser("prepare")
    for arg in ("request-id", "project-id", "conversation-url",
                "prompt-sha256"):
        prepare.add_argument("--" + arg, required=True)
    prepare.add_argument("--epoch", type=int, required=True)
    prepare.add_argument("--turn", type=int, required=True)
    for op in ("claim", "arm", "observe", "inspect", "verify"):
        cmd = sub.add_parser(op)
        cmd.add_argument("--request-id", required=True)
        if op in ("claim", "arm", "observe"):
            cmd.add_argument("--activator-id", required=True)
        if op == "claim":
            cmd.add_argument("--lease-seconds", type=int, default=300)
        if op == "observe":
            cmd.add_argument("--observed-url", required=True)
            cmd.add_argument("--response-sha256", required=True)
            cmd.add_argument("--observation-ref", required=True)
    args = parser.parse_args()
    try:
        relay = Relay(args.db)
        try:
            if args.op == "prepare":
                result = relay.prepare(
                    request_id=args.request_id, project_id=args.project_id,
                    conversation_url=args.conversation_url,
                    master_epoch=args.epoch, turn_no=args.turn,
                    prompt_sha256=args.prompt_sha256)
            elif args.op == "claim":
                result = relay.claim(args.request_id, args.activator_id, args.lease_seconds)
            elif args.op == "arm":
                result = relay.arm(args.request_id, args.activator_id)
            elif args.op == "observe":
                result = relay.record_observation(
                    args.request_id, args.activator_id,
                    args.observed_url, args.response_sha256, args.observation_ref)
            elif args.op == "inspect":
                result = relay.inspect(args.request_id)
            else:
                result = relay.verify_journal(args.request_id)
        finally:
            relay.close()
        print(canonical({"status": "OK", "result": result}))
        return 0
    except (GateError, sqlite3.Error) as exc:
        print(canonical({"status": "BLOCKED", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
