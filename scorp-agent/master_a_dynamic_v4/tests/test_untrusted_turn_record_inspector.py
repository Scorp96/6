from __future__ import annotations

import copy
import hashlib
import unittest

from master_a_dynamic_v4.untrusted_turn_record_inspector import (
    inspect_untrusted_conversation_record,
)

CID = "server-conversation-fixture"
UID = "latest-user-node"
AID = "latest-assistant-node"


def record(*, answer="Candidate output", finish="stop", ended=True):
    return {
        "conversation_id": CID,
        "current_node": AID,
        "mapping": {
            "root": {"parent": None, "message": None},
            UID: {
                "parent": "root",
                "message": {
                    "author": {"role": "user"}, "content": {"parts": ["New task"]},
                },
            },
            AID: {
                "parent": UID,
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": [answer]},
                    "metadata": {"finish_details": {"type": finish}},
                    "end_turn": ended,
                },
            },
        },
    }


def inspect(value, **kwargs):
    return inspect_untrusted_conversation_record(
        value,
        expected_conversation_id=kwargs.pop("conversation_id", CID),
        expected_user_node_id=kwargs.pop("user_node_id", UID),
        **kwargs,
    )


class UntrustedTurnRecordInspectorTests(unittest.TestCase):
    def test_structurally_finished_is_still_not_host_attested(self):
        decision = inspect(record())
        self.assertEqual("FINISHED_UNATTESTED", decision.status)
        self.assertEqual(hashlib.sha256(b"Candidate output").hexdigest(), decision.response_sha256)
        self.assertFalse(decision.host_terminal_event_verified)
        self.assertFalse(decision.physical_identity_verified)
        self.assertFalse(decision.browser_send_authorized)

    def test_wrong_conversation_cannot_promote_old_reply(self):
        self.assertEqual("CONVERSATION_ID_MISMATCH",
                         inspect(record(), conversation_id="another-conversation").reason)

    def test_expected_user_node_is_mandatory(self):
        self.assertEqual("EXPECTED_USER_NODE_ID_REQUIRED",
                         inspect(record(), user_node_id="").reason)

    def test_current_branch_ignores_finished_abandoned_fork(self):
        x = record()
        x["mapping"]["another-user"] = {
            "parent": "root", "message": {"author": {"role": "user"}},
        }
        x["mapping"]["abandoned-answer"] = {
            "parent": "another-user",
            "message": {
                "author": {"role": "assistant"}, "end_turn": True,
                "metadata": {"finish_details": {"type": "stop"}},
                "content": {"parts": ["Old branch answer"]},
            },
        }
        x["mapping"][AID]["message"]["end_turn"] = False
        self.assertEqual("INCOMPLETE", inspect(x).status)

    def test_latest_user_branch_mismatch_is_blocked(self):
        x = record()
        x["mapping"][AID]["parent"] = "different-user"
        x["mapping"]["different-user"] = {
            "parent": "root", "message": {"author": {"role": "user"}},
        }
        self.assertEqual("LATEST_USER_ID_MISMATCH", inspect(x).reason)

    def test_end_turn_false_stays_incomplete(self):
        self.assertEqual("SERVER_END_TURN_NOT_CONFIRMED",
                         inspect(record(ended=False)).reason)

    def test_interrupted_max_tokens_and_cancelled_never_finish(self):
        for mode in ("interrupted", "max_tokens", "cancelled"):
            with self.subTest(mode=mode):
                x = inspect(record(finish=mode))
                self.assertEqual("INCOMPLETE", x.status)
                self.assertIsNone(x.response_sha256)

    def test_unknown_or_missing_finish_details_not_promoted(self):
        for mode in ("unknown_new_finish", None):
            with self.subTest(mode=mode):
                self.assertEqual("ASSISTANT_FINISH_REASON_UNVERIFIED",
                                 inspect(record(finish=mode)).reason)

    def test_empty_reply_never_becomes_finished(self):
        self.assertEqual("ASSISTANT_CONTENT_UNAVAILABLE",
                         inspect(record(answer=" ")).reason)

    def test_reasoning_recap_cannot_replace_an_actual_answer(self):
        x = record()
        x["mapping"]["reasoning-node"] = {
            "parent": AID,
            "message": {
                "author": {"role": "assistant"},
                "content": {"content_type": "reasoning_recap", "parts": ["private trace"]},
                "end_turn": True,
                "metadata": {"finish_details": {"type": "stop"}},
            },
        }
        x["current_node"] = "reasoning-node"
        self.assertEqual("FINISHED_UNATTESTED", inspect(x).status)
        self.assertEqual(hashlib.sha256(b"Candidate output").hexdigest(),
                         inspect(x).response_sha256)

    def test_cycle_and_orphan_branch_are_rejected(self):
        x = record()
        x["mapping"][UID]["parent"] = AID
        self.assertEqual("LIVE_BRANCH_CYCLE", inspect(x).reason)
        x = record()
        x["mapping"][AID]["parent"] = "missing-node"
        self.assertEqual("LIVE_BRANCH_NODE_INVALID", inspect(x).reason)

    def test_newer_user_without_response_does_not_reuse_older_answer(self):
        x = record()
        x["mapping"]["next-user"] = {
            "parent": AID, "message": {"author": {"role": "user"}},
        }
        x["current_node"] = "next-user"
        self.assertEqual("ASSISTANT_REPLY_NOT_FOUND",
                         inspect(x, user_node_id="next-user").reason)

    def test_structurally_bad_messages_are_rejected(self):
        for value in ({}, [], {"mapping": {}}):
            with self.subTest(value=value):
                self.assertEqual("BLOCKED", inspect(value).status)

    def test_pure_parser_does_not_modify_input_record(self):
        row = record()
        original = copy.deepcopy(row)
        inspect(row)
        self.assertEqual(original, row)


if __name__ == "__main__":
    unittest.main()
