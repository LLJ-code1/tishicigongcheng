import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from build_random_wordlists import DEFAULT_SOURCE_DIR  # noqa: E402
from publish_wordlist_proposals import (  # noqa: E402
    ProposalPublishError,
    publish_approved_proposals,
    stage_publication,
)
sys.path.insert(0, str(ROOT / "prototype"))
import db as prompt_db  # noqa: E402


class PublishWordlistProposalsTests(unittest.TestCase):
    def test_staging_validates_new_catalog_without_touching_real_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"
            import shutil

            shutil.copytree(DEFAULT_SOURCE_DIR, source)
            catalog = stage_publication(
                [
                    {
                        "id": "wordlist-one",
                        "categoryId": "scene_environment",
                        "targetBlock": "scene",
                        "text": "unique reviewer scene literal",
                    }
                ],
                source_dir=source,
                output=Path(folder) / "ignored.json",
            )

        self.assertTrue(catalog["version"].startswith("v1-"))
        self.assertEqual(catalog["entryCount"], 472)

    def test_staging_rejects_duplicate_without_writing_the_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"
            import shutil

            shutil.copytree(DEFAULT_SOURCE_DIR, source)
            original = (source / "blocks" / "scene" / "scene_environment.txt").read_text(encoding="utf-8")
            duplicate = original.splitlines()[0]
            with self.assertRaises(ProposalPublishError):
                stage_publication(
                    [{"id": "wordlist-one", "categoryId": "scene_environment", "text": duplicate}],
                    source_dir=source,
                    output=Path(folder) / "ignored.json",
                )
            self.assertEqual((source / "blocks" / "scene" / "scene_environment.txt").read_text(encoding="utf-8"), original)

    def test_explicit_publish_updates_only_the_approved_proposal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            import shutil

            shutil.copytree(DEFAULT_SOURCE_DIR, source)
            database = root / "studio.db"
            prompt_db.init_db(database)
            prompt_db.create_project(
                {
                    "id": "project-one",
                    "name": "Proposal project",
                    "mode": "text",
                    "status": "draft",
                    "metadata": {
                        "wordlistProposals": [
                            {
                                "id": "wordlist-one",
                                "categoryId": "scene_environment",
                                "targetBlock": "scene",
                                "text": "unique reviewer scene literal",
                                "projectId": "project-one",
                                "submittedAt": "2026-07-27T00:00:00Z",
                                "status": "submitted",
                            }
                        ]
                    },
                },
                db_path=database,
            )

            result = publish_approved_proposals(
                database,
                ["wordlist-one"],
                source_dir=source,
                output=root / "v1.json",
            )
            project = prompt_db.get_project("project-one", db_path=database)

        self.assertTrue(result["catalogVersion"].startswith("v1-"))
        proposal = project["metadata"]["wordlistProposals"][0]
        self.assertEqual(proposal["status"], "published")
        self.assertEqual(proposal["publishedCatalogVersion"], result["catalogVersion"])
