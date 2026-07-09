import tempfile
import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402


class PromptStudioDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "prompt_studio.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_project_and_prompt_version_are_persisted(self):
        project = db.create_project(
            {"name": "Rain Street", "mode": "text"},
            self.db_path,
        )
        version = db.create_prompt_version(
            project["id"],
            {
                "source": "expand",
                "positiveEn": "1girl, rain",
                "positiveZh": "雨中的成年女性角色",
                "blocks": [{"id": "scene", "en": "rain"}],
            },
            self.db_path,
        )

        loaded = db.get_project(project["id"], self.db_path)

        self.assertEqual(loaded["name"], "Rain Street")
        self.assertEqual(version["version"], 1)
        self.assertEqual(loaded["versions"][0]["positiveEn"], "1girl, rain")

    def test_favorites_are_split_by_type(self):
        db.upsert_favorite(
            {
                "id": "animadex-characters-hatsune_miku",
                "type": "characters",
                "name": "Hatsune Miku",
                "source": "AnimaDex",
                "sourceId": "hatsune_miku",
                "targetBlock": "subject",
                "en": "hatsune miku",
            },
            self.db_path,
        )
        db.upsert_favorite(
            {
                "id": "animadex-artists-mika_pikazo",
                "type": "artists",
                "name": "Mika Pikazo",
                "source": "AnimaDex",
                "sourceId": "mika_pikazo",
                "targetBlock": "artist",
                "en": "@mika pikazo",
            },
            self.db_path,
        )

        characters = db.list_favorites("characters", db_path=self.db_path)
        artists = db.list_favorites("artists", db_path=self.db_path)

        self.assertEqual([item["name"] for item in characters], ["Hatsune Miku"])
        self.assertEqual([item["name"] for item in artists], ["Mika Pikazo"])

    def test_settings_round_trip(self):
        saved = db.put_settings(
            {"textProvider": "local", "autoCombine": True},
            self.db_path,
        )
        loaded = db.get_settings(self.db_path)

        self.assertEqual(saved["textProvider"], "local")
        self.assertTrue(loaded["autoCombine"])


if __name__ == "__main__":
    unittest.main()

