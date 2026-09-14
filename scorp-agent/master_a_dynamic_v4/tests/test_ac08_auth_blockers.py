from __future__ import annotations

import pathlib
import tempfile
import unittest


class FakeEngine:
    def __init__(self, store, intent_id, *, auth):
        self.store = store
        self.intent_id = intent_id
        self.auth = auth
        self.submit_count = 0

    def auth_state(self, channel):
        return {"status": self.auth, "channel": channel}

    def submit(self, intent):
        self.submit_count += 1
        return {"status": "SUBMITTED", "conversation_url": "https://chatgpt.com/c/ac08", "remote_identity": "remote-ac08"}


class AuthBlockerTests(unittest.TestCase):
    def test_windows_login_chatgpt_login_captcha_and_unknown_auth_all_block_without_submit(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter
        from master_a_dynamic_v4.state_store import StateStore

        blocked_states = [
            "WINDOWS_NOT_LOGGED_IN",
            "WINDOWS_SESSION_UNAVAILABLE",
            "CHATGPT_LOGGED_OUT",
            "CAPTCHA",
            "VERIFICATION_CHALLENGE",
            "UNKNOWN_AUTH_STATE",
        ]
        for index, auth_state in enumerate(blocked_states):
            with self.subTest(auth_state=auth_state), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                store = StateStore(root / "state.sqlite3", allowed_roots=[root])
                store.create_contract("project-ac08", root_contract={"objective": "auth block"}, acceptance_contract={"required": ["AC08"]})
                intent_id = f"intent-{index}"
                store.prepare_intent("project-ac08", intent_id, actor_id="A", channel="master", action_kind="CHATGPT_SUBMIT", payload={"prompt_sha256": f"{index:x}" * 64})
                engine = FakeEngine(store, intent_id, auth=auth_state)
                try:
                    row = BrowserAdapter(store, engine).submit_once(intent_id)
                    self.assertEqual("BLOCKED_AMBIGUOUS", row["state"])
                    self.assertEqual(f"AUTH_BLOCKED:{auth_state}", row["ambiguity_reason"])
                    self.assertEqual(0, engine.submit_count)
                finally:
                    store.close()


if __name__ == "__main__":
    unittest.main()
