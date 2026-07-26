import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import model_profile_store as store  # noqa: E402
from model_research import EvidenceClaim, SourceSnapshot  # noqa: E402


class ModelProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "profiles.db"
        db.init_db(self.database)
        self.url = "https://civitai.com/models/934764/example"
        self.snapshot = SourceSnapshot(
            "snap-1", "original_source", self.url, self.url,
            "2026-07-26T10:00:00Z", "application/json", "a" * 64,
            '{"name":"Example"}', "succeeded", None,
        )
        self.claim = EvidenceClaim(
            "claim-1", "model.versionName", "v1", "original_source",
            ("snap-1",), "official version", "source_recorded", "proposed",
        )
        self.profile = {"id": "example-model", "displayName": "Example", "rules": {}}
        self.revised_profile = {
            "id": "example-model", "displayName": "Example v1",
            "model": {"versionName": "untrusted"},
        }
        self._draft_number = 0

    def tearDown(self):
        self.tempdir.cleanup()

    def _draft(self, profile=None):
        self._draft_number += 1
        suffix = self._draft_number
        snapshot = SourceSnapshot(
            f"snap-{suffix}", "original_source", self.url, self.url,
            "2026-07-26T10:00:00Z", "application/json", "a" * 64,
            '{"name":"Example"}', "succeeded", None,
        )
        claim = EvidenceClaim(
            f"claim-{suffix}", "model.versionName", "v1", "original_source",
            (snapshot.snapshot_id,), "official version", "source_recorded",
            "proposed",
        )
        run = store.create_research_run(self.url, db_path=self.database)
        complete = store.complete_research_run(
            run["runId"], [snapshot], [claim], db_path=self.database
        )
        return store.create_profile_draft(
            run["runId"], profile or self.profile, complete["claims"],
            db_path=self.database,
        )

    def test_research_evidence_and_profile_versions_are_immutable(self):
        draft = self._draft()
        revised = store.revise_profile_draft(
            draft["versionId"], self.revised_profile,
            {"claim-1": "approved"}, "confirmed official version",
            db_path=self.database,
        )
        self.assertNotEqual(revised["versionId"], draft["versionId"])
        self.assertEqual(
            store.get_profile_version(draft["versionId"], db_path=self.database)[
                "profile"
            ],
            self.profile,
        )
        self.assertEqual(revised["parentVersionId"], draft["versionId"])
        self.assertEqual(
            revised["profile"]["model"], {"versionName": "v1"}
        )

    def test_only_reviewed_version_can_activate_with_compare_and_swap(self):
        draft = self._draft()
        with self.assertRaisesRegex(store.ProfileStoreError, "not_reviewed"):
            store.activate_profile_version(
                draft["versionId"], None, db_path=self.database
            )
        reviewed = store.review_profile_version(
            draft["versionId"], "evidence checked", db_path=self.database
        )
        active = store.activate_profile_version(
            reviewed["versionId"], None, db_path=self.database
        )
        self.assertEqual(active["lifecycleStatus"], "active")

        other = self._draft(
            {"id": "example-model", "displayName": "Example two", "rules": {}}
        )
        other_reviewed = store.review_profile_version(
            other["versionId"], "checked", db_path=self.database
        )
        with self.assertRaisesRegex(
            store.ProfileStoreError, "active_version_changed"
        ):
            store.activate_profile_version(
                other_reviewed["versionId"], None, db_path=self.database
            )

        replacement = store.activate_profile_version(
            other_reviewed["versionId"], active["versionId"],
            db_path=self.database,
        )
        self.assertEqual(replacement["lifecycleStatus"], "active")
        self.assertEqual(
            store.get_profile_version(active["versionId"], db_path=self.database)[
                "lifecycleStatus"
            ],
            "superseded",
        )
        self.assertEqual(
            [item["versionId"] for item in store.list_active_profile_versions(
                db_path=self.database
            )],
            [replacement["versionId"]],
        )

    def test_completion_is_idempotent_and_rejects_unknown_evidence(self):
        run = store.create_research_run(self.url, db_path=self.database)
        first = store.complete_research_run(
            run["runId"], [self.snapshot], [self.claim], db_path=self.database
        )
        replay = store.complete_research_run(
            run["runId"], [self.snapshot], [self.claim], db_path=self.database
        )
        self.assertEqual(replay, first)

        other = store.create_research_run(self.url, db_path=self.database)
        bad = EvidenceClaim(
            "claim-bad", "displayName", "Bad", "original_source",
            ("snap-missing",), "unknown evidence", "source_recorded", "proposed",
        )
        with self.assertRaisesRegex(store.ProfileStoreError, "unknown_evidence"):
            store.complete_research_run(
                other["runId"], [self.snapshot], [bad], db_path=self.database
            )
        with db.database(self.database) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM model_evidence_snapshots WHERE run_id = ?",
                    (other["runId"],),
                ).fetchone()[0],
                0,
            )

    def test_rejected_claim_is_not_projected_into_profile_rules(self):
        draft = self._draft()
        revised = store.revise_profile_draft(
            draft["versionId"],
            {"id": "example-model", "displayName": "Example", "rules": {}},
            {"claim-1": "rejected"}, "not applicable", db_path=self.database,
        )
        self.assertNotIn("model", revised["profile"])
        self.assertEqual(revised["claimDecisions"], {"claim-1": "rejected"})

    def test_revision_transaction_rolls_back_on_insert_failure(self):
        draft = self._draft()
        with patch.object(store, "_insert_profile_version", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                store.revise_profile_draft(
                    draft["versionId"], self.revised_profile,
                    {"claim-1": "approved"}, "note", db_path=self.database,
                )
        with db.database(self.database) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM model_profile_versions WHERE profile_id = ?",
                ("example-model",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_concurrent_activation_has_one_cas_winner_and_one_active_row(self):
        first = store.review_profile_version(
            self._draft()["versionId"], "first checked", db_path=self.database
        )
        second = store.review_profile_version(
            self._draft()["versionId"], "second checked", db_path=self.database
        )

        def activate(version_id):
            try:
                return store.activate_profile_version(
                    version_id, None, db_path=self.database
                )
            except Exception as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                activate, (first["versionId"], second["versionId"])
            ))
        winners = [item for item in results if isinstance(item, dict)]
        conflicts = [
            item for item in results
            if isinstance(item, store.ProfileStoreError)
            and item.code == "active_version_changed"
        ]
        self.assertEqual((len(winners), len(conflicts)), (1, 1))
        self.assertEqual(
            len(store.list_active_profile_versions(db_path=self.database)), 1
        )


if __name__ == "__main__":
    unittest.main()
