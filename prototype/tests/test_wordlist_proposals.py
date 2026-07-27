import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wordlist_proposals import WordlistProposalError, normalize_wordlist_proposals  # noqa: E402


class WordlistProposalTests(unittest.TestCase):
    def test_normalizes_a_review_only_proposal_with_project_provenance(self):
        result = normalize_wordlist_proposals(
            [
                {
                    "id": "wordlist-one",
                    "categoryId": "scene_environment",
                    "targetBlock": "scene",
                    "text": "rainy library",
                    "status": "submitted",
                }
            ],
            project_id="project-one",
            submitted_at="2026-07-27T12:00:00Z",
        )

        self.assertEqual(result[0]["projectId"], "project-one")
        self.assertEqual(result[0]["submittedAt"], "2026-07-27T12:00:00Z")
        self.assertEqual(result[0]["status"], "submitted")

    def test_rejects_unknown_category_block_mismatch_and_prompt_separators(self):
        baseline = {
            "id": "wordlist-one",
            "categoryId": "scene_environment",
            "targetBlock": "scene",
            "text": "rainy library",
        }
        for changes in (
            {"categoryId": "unknown"},
            {"targetBlock": "appearance"},
            {"text": "rain, library"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(WordlistProposalError):
                    normalize_wordlist_proposals(
                        [{**baseline, **changes}],
                        project_id="project-one",
                        submitted_at="2026-07-27T12:00:00Z",
                    )
