import json
import tempfile
import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tag_semantics import TagSemanticError, diagnose_tags, load_catalog, normalize_catalog  # noqa: E402


def catalog():
    return {
        "schemaVersion": 1,
        "version": "fixture-v1",
        "sourceReview": {"licenseReviewed": True, "redistributionApproved": True},
        "policy": {"lowFrequencyPostCount": 100},
        "tags": [
            {
                "canonical": "long hair",
                "aliases": ["long_hair"],
                "implications": ["hair"],
                "postCount": 500,
                "category": "appearance",
            },
            {
                "canonical": "hair",
                "aliases": [],
                "implications": [],
                "postCount": 3,
                "category": "appearance",
            },
            {
                "canonical": "red hair",
                "aliases": [],
                "implications": ["hair"],
                "postCount": 500,
                "category": "appearance",
            },
        ],
        "conflictRules": [
            {"left": "long hair", "right": "red hair", "decision": "manual_review"}
        ],
    }


class TagSemanticsTests(unittest.TestCase):
    def test_diagnoses_alias_frequency_implication_and_conflict_without_rewriting(self):
        result = diagnose_tags(
            ["long_hair", "hair", "red hair", "unknown tag"], catalog()
        )
        self.assertEqual(result["catalogStatus"], "ready")
        self.assertEqual(result["tags"][0]["canonical"], "long hair")
        self.assertEqual(result["tags"][1]["status"], "low_frequency")
        self.assertEqual(result["tags"][3]["status"], "unknown")
        self.assertIn(
            {"kind": "alias_merge", "from": "long_hair", "to": "long hair", "action": "merge"},
            result["suggestions"],
        )
        self.assertTrue(any(item["kind"] == "implication_duplicate" for item in result["suggestions"]))
        self.assertTrue(any(item["kind"] == "semantic_conflict" for item in result["suggestions"]))

    def test_missing_or_unreviewed_catalog_fails_closed(self):
        self.assertEqual(diagnose_tags(["1girl"])["catalogStatus"], "unavailable")
        invalid = catalog()
        invalid["sourceReview"]["licenseReviewed"] = False
        with self.assertRaises(TagSemanticError):
            normalize_catalog(invalid)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertIsNone(load_catalog(path))
