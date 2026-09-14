import pathlib
import tempfile
import unittest

from actor_response_journal_v3 import ActorResponseJournalV3


class ActorResponseJournalV3Tests(unittest.TestCase):
    def test_record_and_restart_preserve_exact_response(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "responses.json"
            journal = ActorResponseJournalV3(path)
            response = {"kind": "CONTINUE", "state_patch": {"next_exact_action": "x"}}
            first = journal.record("turn-1", "a" * 64, response)
            self.assertEqual("RECORDED", first["status"])
            restarted = ActorResponseJournalV3(path)
            loaded = restarted.load("turn-1", "a" * 64)
            self.assertEqual(response, loaded["response"])
            self.assertEqual(first["response_sha256"], loaded["response_sha256"])

    def test_exact_record_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ActorResponseJournalV3(pathlib.Path(td) / "responses.json")
            response = {"kind": "HANDOFF", "evidence": ["ok"]}
            first = journal.record("turn-1", "b" * 64, response)
            second = journal.record("turn-1", "b" * 64, response)
            self.assertEqual("RECORDED", first["status"])
            self.assertEqual("ALREADY_RECORDED", second["status"])
            self.assertEqual(first["response_sha256"], second["response_sha256"])

    def test_same_turn_changed_input_or_changed_response_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ActorResponseJournalV3(pathlib.Path(td) / "responses.json")
            journal.record("turn-1", "c" * 64, {"kind": "WAIT"})
            with self.assertRaisesRegex(ValueError, "ACTOR_RESPONSE_TURN_CONFLICT"):
                journal.load("turn-1", "d" * 64)
            with self.assertRaisesRegex(ValueError, "ACTOR_RESPONSE_CONFLICT"):
                journal.record("turn-1", "c" * 64, {"kind": "CONTINUE"})

    def test_missing_turn_returns_none_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "responses.json"
            journal = ActorResponseJournalV3(path)
            self.assertIsNone(journal.load("missing", "e" * 64))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
