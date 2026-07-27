import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generation_match import prompt_match_diagnostics  # noqa: E402


class GenerationMatchTests(unittest.TestCase):
    def test_reports_evidence_status_without_rewriting_the_prompt(self):
        result = prompt_match_diagnostics(
            "pink hair, library, day, red dress",
            {
                "items": [
                    {"tag": "pink hair", "status": "confirmed"},
                    {"tag": "library", "status": "uncertain"},
                    {"tag": "day", "status": "conflict"},
                ]
            },
        )

        self.assertTrue(result["advisory"])
        self.assertEqual(result["confirmed"], ["pink hair"])
        self.assertEqual(result["uncertain"], ["library"])
        self.assertEqual(result["conflict"], ["day"])
        self.assertEqual(result["unobserved"], ["red dress"])
        self.assertEqual(result["confirmedMatchRate"], 0.25)
        self.assertEqual(result["possibleMatchRate"], 0.5)

    def test_rejects_invalid_consensus_instead_of_guessing(self):
        with self.assertRaisesRegex(ValueError, "consensus"):
            prompt_match_diagnostics("1girl", {"items": "not-a-list"})
