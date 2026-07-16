import concurrent.futures
import sqlite3
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

    def test_project_rejects_unsafe_ids_and_wrong_field_types(self):
        invalid_payloads = [
            {"id": "slash/id"},
            {"id": ""},
            {"name": {"nested": "name"}},
            {"mode": ["text"]},
            {"mode": "video"},
            {"status": {"draft": True}},
            {"metadata": []},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                db.create_project(payload, self.db_path)

        self.assertEqual(db.list_projects(self.db_path), [])

    def test_duplicate_project_id_raises_integrity_error_without_corrupting_db(self):
        db.create_project({"id": "project-fixed"}, self.db_path)

        with self.assertRaises(sqlite3.IntegrityError):
            db.create_project({"id": "project-fixed"}, self.db_path)

        self.assertEqual(len(db.list_projects(self.db_path)), 1)

    def test_project_metadata_drops_nested_provider_secrets(self):
        project = db.create_project(
            {
                "metadata": {
                    "settings": {
                        "apiTextKey": "external-secret",
                        "local_text_key": "local-secret",
                        "textProvider": "api",
                    },
                    "draftInput": "rainy street",
                }
            },
            self.db_path,
        )

        self.assertNotIn("apiTextKey", project["metadata"]["settings"])
        self.assertNotIn("local_text_key", project["metadata"]["settings"])
        self.assertEqual(project["metadata"]["settings"]["textProvider"], "api")

    def test_prompt_version_rejects_invalid_number_and_shapes(self):
        project = db.create_project({}, self.db_path)
        invalid_payloads = [
            {"version": "not-an-int"},
            {"version": True},
            {"version": 0},
            {"version": -1},
            {"blocks": "not-an-array"},
            {"blocks": [{}], "metadata": []},
            {"blocks": [{"id": "scene", "weight": -1}]},
            {"blocks": [{"id": "scene", "weight": 121}]},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                db.create_prompt_version(project["id"], payload, self.db_path)

    def test_duplicate_version_id_raises_integrity_error(self):
        project = db.create_project({}, self.db_path)
        db.create_prompt_version(
            project["id"],
            {"id": "version-fixed"},
            self.db_path,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.create_prompt_version(
                project["id"],
                {"id": "version-fixed"},
                self.db_path,
            )

    def test_automatic_version_numbers_are_unique_under_concurrency(self):
        project = db.create_project({}, self.db_path)

        def save_version(index):
            return db.create_prompt_version(
                project["id"],
                {"source": f"worker-{index}"},
                self.db_path,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            versions = list(executor.map(save_version, range(2)))

        self.assertEqual(sorted(item["version"] for item in versions), [1, 2])

    def test_favorite_requires_object_blocks_and_typed_fields(self):
        invalid_resources = [
            {"id": "snippet-1", "type": "snippets", "blocks": "bad"},
            {"id": "snippet-2", "type": "snippets", "blocks": ["bad"]},
            {"id": "snippet-3", "type": "snippets", "name": {}},
            {"id": "snippet-4", "type": "snippets", "hasImage": 1},
        ]

        for resource in invalid_resources:
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                db.upsert_favorite(resource, self.db_path)

    def test_favorite_accepts_realistic_animadex_slug_characters(self):
        resource_id = "animadex-characters-k/da_(series)#100%:variant[alt]'s"
        favorite = db.upsert_favorite(
            {
                "id": resource_id,
                "type": "characters",
                "name": "Complex slug",
            },
            self.db_path,
        )

        self.assertEqual(favorite["id"], resource_id)
        self.assertRegex(favorite["favoriteId"], r"^favorite-[a-f0-9]{24}$")
        self.assertTrue(db.delete_favorite(favorite["favoriteId"], self.db_path))

    def test_favorite_delete_removes_historical_duplicate_rows(self):
        resource_id = "animadex-characters-duplicate/resource"
        favorite = db.upsert_favorite(
            {
                "id": resource_id,
                "type": "characters",
                "name": "Duplicate resource",
            },
            self.db_path,
        )
        with db.database(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO favorites (
                    id, resource_id, type, name, meta, source, source_id,
                    target_block, en, zh, thumb_url, has_image,
                    blocks_json, metadata_json, created_at, updated_at
                )
                SELECT ?, resource_id, type, name, meta, source, source_id,
                    target_block, en, zh, thumb_url, has_image,
                    blocks_json, metadata_json, created_at, updated_at
                FROM favorites WHERE id = ?
                """,
                ("legacy-duplicate-id", favorite["favoriteId"]),
            )

        self.assertTrue(db.delete_favorite(favorite["favoriteId"], self.db_path))
        with db.database(self.db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM favorites WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_legacy_project_ids_remain_readable_and_versionable(self):
        project_id = "旧 项目/k-da"
        timestamp = db.now_iso()
        with db.database(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, mode, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, 'text', 'draft', '{}', ?, ?)
                """,
                (project_id, "Legacy", timestamp, timestamp),
            )

        self.assertEqual(db.get_project(project_id, self.db_path)["id"], project_id)
        version = db.create_prompt_version(project_id, {}, self.db_path)
        self.assertEqual((version["projectId"], version["version"]), (project_id, 1))

    def test_historical_bad_json_shapes_are_returned_with_safe_defaults(self):
        project = db.create_project({}, self.db_path)
        version = db.create_prompt_version(project["id"], {}, self.db_path)
        with db.database(self.db_path) as connection:
            connection.execute(
                "UPDATE projects SET metadata_json = '[]' WHERE id = ?",
                (project["id"],),
            )
            connection.execute(
                """
                UPDATE prompt_versions
                SET blocks_json = '\"bad\"', metadata_json = '[]'
                WHERE id = ?
                """,
                (version["id"],),
            )

        loaded = db.get_project(project["id"], self.db_path)

        self.assertEqual(loaded["metadata"], {})
        self.assertEqual(loaded["versions"][0]["blocks"], [])
        self.assertEqual(loaded["versions"][0]["metadata"], {})

    def test_historical_non_finite_or_invalid_block_items_are_not_returned(self):
        project = db.create_project({}, self.db_path)
        version = db.create_prompt_version(project["id"], {}, self.db_path)
        with db.database(self.db_path) as connection:
            connection.execute(
                "UPDATE prompt_versions SET blocks_json = ? WHERE id = ?",
                ('[null, {"id": 3}, {"id": "scene", "weight": 999}]', version["id"]),
            )

        loaded = db.get_project(project["id"], self.db_path)

        self.assertEqual(loaded["versions"][0]["blocks"], [])

        with db.database(self.db_path) as connection:
            connection.execute(
                "UPDATE prompt_versions SET blocks_json = ? WHERE id = ?",
                ('[{"id": "scene", "weight": NaN}]', version["id"]),
            )
        self.assertEqual(
            db.get_project(project["id"], self.db_path)["versions"][0]["blocks"],
            [],
        )

        with db.database(self.db_path) as connection:
            connection.execute(
                "UPDATE prompt_versions SET blocks_json = ? WHERE id = ?",
                ('[{"id": "scene", "weight": 1e10000}]', version["id"]),
            )
        self.assertEqual(
            db.get_project(project["id"], self.db_path)["versions"][0]["blocks"],
            [],
        )

    def test_metadata_secret_aliases_are_removed(self):
        metadata = db.sanitize_metadata(
            {
                "api-text-key": "one",
                "api.text.key": "two",
                "access_token": "three",
                "refreshToken": "four",
                "client_secret": "five",
                "authToken": "six",
                "github_token": "seven",
                "mySecret": "eight",
                "privateKey": "nine",
                "authorizationHeader": "ten",
                "bearerTokenValue": "eleven",
                "clientSecretValue": "twelve",
                "privateKeyPem": "thirteen",
                "credentialValue": "fourteen",
                "displayName": "safe",
            }
        )

        self.assertEqual(metadata, {"displayName": "safe"})

        project = db.create_project({}, self.db_path)
        with db.database(self.db_path) as connection:
            connection.execute(
                "UPDATE projects SET metadata_json = ? WHERE id = ?",
                (
                    '{"settings":{"apiTextKey":"legacy-secret",'
                    '"textProvider":"api"}}',
                    project["id"],
                ),
            )
        loaded = db.get_project(project["id"], self.db_path)
        self.assertEqual(loaded["metadata"], {"settings": {"textProvider": "api"}})

    def test_settings_preserve_explicit_null(self):
        saved = db.put_settings({"optionalValue": None}, self.db_path)

        self.assertIsNone(saved["optionalValue"])

    def test_json_serialization_rejects_non_finite_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                db.json_dumps({"value": value})

    def test_invalid_unicode_is_rejected_before_sqlite_or_json_storage(self):
        with self.assertRaises(ValueError):
            db.create_project({"name": "bad\ud800name"}, self.db_path)
        with self.assertRaises(ValueError):
            db.json_dumps({"value": "bad\ud800value"})
        with self.assertRaises(ValueError):
            db.upsert_favorite(
                {
                    "id": "favorite-safe",
                    "type": "characters",
                    "blocks": [{"id": "bad\ud800block"}],
                },
                self.db_path,
            )

    def test_extremely_large_block_numbers_are_rejected_as_validation_errors(self):
        huge = 10**400
        for key in ("weight", "confidence"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                db.normalize_blocks([{"id": "scene", key: huge}])


if __name__ == "__main__":
    unittest.main()
