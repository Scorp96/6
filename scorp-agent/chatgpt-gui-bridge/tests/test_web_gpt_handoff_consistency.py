import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


class WebGptHandoffConsistencyTests(unittest.TestCase):
    def _read_json(self, relative):
        return json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))

    def test_handoff_is_bound_to_validated_code_candidate(self):
        validation = self._read_json("docs/handoffs/SCORP_V4_GIT6_VALIDATION.json")
        handoff = self._read_json("docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.json")
        candidate = validation["validated_commit"]

        self.assertRegex(candidate, r"^[0-9a-f]{40}$")
        self.assertEqual(candidate, handoff["candidate_commit"])
        self.assertEqual(candidate, handoff["evidence_binding"]["validated_commit"])

        markdown = (REPO_ROOT / "docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(candidate, markdown)

        # A handoff must not quietly retain an older candidate identity.
        commits = set(re.findall(r"\b[0-9a-f]{40}\b", markdown))
        self.assertEqual({candidate}, commits)


if __name__ == "__main__":
    unittest.main()
