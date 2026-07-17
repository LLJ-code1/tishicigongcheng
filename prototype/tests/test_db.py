import concurrent.futures
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    @staticmethod
    def file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def create_legacy_database(path: Path) -> None:
        timestamp = "2026-07-16T00:00:00+00:00"
        connection = sqlite3.connect(path)
        try:
            connection.executescript(db.SCHEMA)
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, mode, status, metadata_json, created_at, updated_at
                ) VALUES ('legacy-project', 'Legacy project', 'text', 'draft',
                    '{"source":"v0"}', ?, ?)
                """,
                (timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO prompt_versions (
                    id, project_id, version, source,
                    positive_en, positive_zh, negative_en, negative_zh,
                    blocks_json, metadata_json, created_at
                ) VALUES (
                    'legacy-version', 'legacy-project', 1, 'manual',
                    '1girl', '一名女性', '', '', '[]', '{}', ?
                )
                """,
                (timestamp,),
            )
            connection.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES ('apiTextKey', '"legacy-secret"', ?)
                """,
                (timestamp,),
            )
            connection.commit()
        finally:
            connection.close()

    def test_new_database_is_initialized_as_schema_v1_without_snapshot(self):
        path = Path(self.tempdir.name) / "new-v1.db"

        missing_status = db.get_database_status(path)
        self.assertFalse(missing_status["exists"])
        self.assertTrue(missing_status["needsMigration"])
        self.assertFalse(path.exists())

        db.init_db(path)

        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                db.SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                db.APPLICATION_ID,
            )
        finally:
            connection.close()
        status = db.get_database_status(path)
        self.assertTrue(status["initialized"])
        self.assertTrue(status["compatible"])
        self.assertEqual(status["integrity"], "ok")
        self.assertFalse(db.recovery_directory(path).exists())

    def test_now_iso_keeps_microsecond_precision(self):
        self.assertRegex(
            db.now_iso(),
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$",
        )

    def test_existing_empty_sqlite_file_is_initialized_without_snapshot(self):
        path = Path(self.tempdir.name) / "empty-header.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()
        self.assertGreater(path.stat().st_size, 0)

        db.init_db(path)

        self.assertTrue(db.get_database_status(path)["initialized"])
        self.assertFalse(db.recovery_directory(path).exists())

    def test_legacy_v0_database_is_snapshotted_and_migrated_without_data_loss(self):
        path = Path(self.tempdir.name) / "legacy.db"
        self.create_legacy_database(path)

        db.init_db(path)

        project = db.get_project("legacy-project", path)
        self.assertEqual(project["name"], "Legacy project")
        self.assertEqual(project["versions"][0]["positiveEn"], "1girl")
        self.assertEqual(db.get_settings(path)["apiTextKey"], "legacy-secret")
        snapshots = list(db.recovery_directory(path).glob("*.db"))
        self.assertEqual(len(snapshots), 1)
        snapshot = sqlite3.connect(snapshots[0])
        try:
            self.assertEqual(snapshot.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(snapshot.execute("PRAGMA application_id").fetchone()[0], 0)
            self.assertEqual(
                snapshot.execute(
                    "SELECT name FROM projects WHERE id = 'legacy-project'"
                ).fetchone()[0],
                "Legacy project",
            )
            self.assertEqual(
                snapshot.execute(
                    "SELECT value_json FROM settings WHERE key = 'apiTextKey'"
                ).fetchone()[0],
                '"legacy-secret"',
            )
        finally:
            snapshot.close()

    def test_init_is_idempotent_and_does_not_create_another_snapshot(self):
        path = Path(self.tempdir.name) / "idempotent.db"
        self.create_legacy_database(path)
        db.init_db(path)
        before_hash = self.file_hash(path)
        snapshots_before = list(db.recovery_directory(path).glob("*.db"))

        db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        self.assertEqual(
            list(db.recovery_directory(path).glob("*.db")),
            snapshots_before,
        )

    def test_future_schema_is_rejected_without_modifying_database(self):
        path = Path(self.tempdir.name) / "future.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        finally:
            connection.close()
        before_hash = self.file_hash(path)

        with self.assertRaises(db.UnsupportedDatabaseVersionError):
            db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        self.assertFalse(db.get_database_status(path)["compatible"])
        self.assertFalse(db.recovery_directory(path).exists())

    def test_wrong_application_id_is_rejected_without_modification(self):
        path = Path(self.tempdir.name) / "wrong-app.db"
        self.create_legacy_database(path)
        connection = sqlite3.connect(path)
        try:
            connection.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION}")
            connection.execute("PRAGMA application_id = 123456")
        finally:
            connection.close()
        before_hash = self.file_hash(path)

        with self.assertRaises(db.DatabaseSchemaError):
            db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        self.assertFalse(db.recovery_directory(path).exists())

    def test_incomplete_legacy_schema_is_rejected_without_modification(self):
        path = Path(self.tempdir.name) / "incomplete.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()
        before_hash = self.file_hash(path)

        with self.assertRaises(db.DatabaseSchemaError):
            db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        self.assertFalse(db.recovery_directory(path).exists())

    def test_legacy_database_with_trigger_is_rejected_without_writes_or_snapshot(self):
        path = Path(self.tempdir.name) / "trigger-injected.db"
        self.create_legacy_database(path)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TRIGGER malicious_project_insert
                AFTER INSERT ON projects
                BEGIN
                    UPDATE settings
                    SET value_json = '"triggered"'
                    WHERE key = 'apiTextKey';
                END
                """
            )
            connection.commit()
        finally:
            connection.close()
        before_hash = self.file_hash(path)

        with self.assertRaisesRegex(db.DatabaseSchemaError, "触发器或视图"):
            db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        self.assertFalse(db.recovery_directory(path).exists())

    def test_corrupt_database_is_reported_read_only_and_not_modified(self):
        path = Path(self.tempdir.name) / "corrupt.db"
        path.write_bytes(b"this is not a sqlite database")
        before_hash = self.file_hash(path)

        status = db.get_database_status(path)
        self.assertFalse(status["compatible"])
        self.assertEqual(status["state"], "error")
        with self.assertRaises(db.DatabaseSchemaError):
            db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        self.assertFalse(db.recovery_directory(path).exists())

    def test_snapshot_failure_rolls_back_without_modifying_legacy_database(self):
        path = Path(self.tempdir.name) / "snapshot-failure.db"
        self.create_legacy_database(path)
        before_hash = self.file_hash(path)

        with patch.object(
            db,
            "_create_recovery_snapshot",
            side_effect=OSError("simulated disk full"),
        ), self.assertRaises(OSError):
            db.init_db(path)

        self.assertEqual(self.file_hash(path), before_hash)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)
        finally:
            connection.close()

    def test_concurrent_legacy_initialization_runs_one_migration_and_snapshot(self):
        path = Path(self.tempdir.name) / "concurrent-init.db"
        self.create_legacy_database(path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: db.init_db(path), range(4)))

        self.assertEqual(results, [path] * 4)
        self.assertEqual(len(list(db.recovery_directory(path).glob("*.db"))), 1)
        self.assertTrue(db.get_database_status(path)["initialized"])

    def test_database_context_explicitly_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with db.database(self.db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, name, mode, status, metadata_json, created_at, updated_at
                    ) VALUES ('rolled-back', 'Rollback', 'text', 'draft', '{}', '', '')
                    """
                )
                raise RuntimeError("abort")

        self.assertIsNone(db.get_project("rolled-back", self.db_path))

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

    def test_project_update_uses_a_strict_whitelist_and_preserves_versions(self):
        project = db.create_project(
            {
                "name": "Before",
                "mode": "text",
                "status": "draft",
                "metadata": {"keep": True},
            },
            self.db_path,
        )
        db.create_prompt_version(project["id"], {"positiveEn": "1girl"}, self.db_path)

        updated = db.update_project(
            project["id"],
            {
                "name": "After",
                "mode": "image",
                "status": "ready",
                "metadata": {
                    "note": "kept",
                    "apiTextKey": "must-not-be-stored",
                },
            },
            self.db_path,
        )

        self.assertEqual(updated["name"], "After")
        self.assertEqual(updated["mode"], "image")
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(updated["metadata"], {"note": "kept"})
        self.assertEqual(len(updated["versions"]), 1)
        with self.assertRaisesRegex(ValueError, "不允许的字段"):
            db.update_project(project["id"], {"id": "replacement"}, self.db_path)
        with self.assertRaises(ValueError):
            db.update_project(project["id"], {"mode": "video"}, self.db_path)
        with self.assertRaises(ValueError):
            db.update_project(project["id"], {}, self.db_path)
        self.assertIsNone(
            db.update_project("missing-project", {"name": "Missing"}, self.db_path)
        )

    def test_project_update_cas_is_atomic_and_timestamp_is_monotonic(self):
        project = db.create_project({"name": "Original"}, self.db_path)
        base_updated_at = project["updatedAt"]

        def update(index):
            try:
                return db.update_project(
                    project["id"],
                    {
                        "baseUpdatedAt": base_updated_at,
                        "name": f"Worker {index}",
                    },
                    self.db_path,
                )
            except db.ProjectConflictError as error:
                return error

        # Force both writers to request the same wall-clock timestamp. The
        # project-specific clock must still move forward after the winning write.
        with patch.object(db, "now_iso", return_value=base_updated_at):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(update, range(2)))

        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [
            item for item in results if isinstance(item, db.ProjectConflictError)
        ]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertGreater(successes[0]["updatedAt"], base_updated_at)
        self.assertEqual(conflicts[0].expected, base_updated_at)
        self.assertEqual(conflicts[0].actual, successes[0]["updatedAt"])
        stored = db.get_project(project["id"], self.db_path)
        self.assertEqual(stored["name"], successes[0]["name"])
        self.assertEqual(stored["updatedAt"], successes[0]["updatedAt"])

    def test_project_update_cas_is_optional_but_validated_when_present(self):
        project = db.create_project({"name": "Before"}, self.db_path)

        unguarded = db.update_project(
            project["id"],
            {"name": "Unguarded"},
            self.db_path,
        )
        self.assertEqual(unguarded["name"], "Unguarded")
        for invalid in (None, True, 1, ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                db.update_project(
                    project["id"],
                    {"baseUpdatedAt": invalid, "name": "Rejected"},
                    self.db_path,
                )

    def test_version_returns_project_timestamp_for_followup_cas_update(self):
        project = db.create_project({}, self.db_path)

        version = db.create_prompt_version(
            project["id"],
            {"baseVersion": 0, "positiveEn": "first"},
            self.db_path,
        )
        updated = db.update_project(
            project["id"],
            {
                "baseUpdatedAt": version["projectUpdatedAt"],
                "name": "After version",
            },
            self.db_path,
        )

        self.assertEqual(updated["name"], "After version")
        self.assertGreater(updated["updatedAt"], version["projectUpdatedAt"])

    def test_header_change_invalidates_stale_version_and_prevents_header_overwrite(self):
        project = db.create_project(
            {"name": "Original", "metadata": {"draftInput": "original"}},
            self.db_path,
        )
        stale_updated_at = project["updatedAt"]
        newer = db.update_project(
            project["id"],
            {
                "baseUpdatedAt": stale_updated_at,
                "metadata": {"draftInput": "writer-b"},
            },
            self.db_path,
        )

        with self.assertRaises(db.ProjectConflictError):
            db.create_prompt_version(
                project["id"],
                {
                    "baseVersion": 0,
                    "baseUpdatedAt": stale_updated_at,
                    "positiveEn": "writer-a",
                },
                self.db_path,
            )

        stored = db.get_project(project["id"], self.db_path)
        self.assertEqual(stored["metadata"]["draftInput"], "writer-b")
        self.assertEqual(stored["updatedAt"], newer["updatedAt"])
        self.assertEqual(stored["versions"], [])

    def test_project_list_includes_version_count_and_latest_version(self):
        versioned = db.create_project({"name": "Versioned"}, self.db_path)
        empty = db.create_project({"name": "Empty"}, self.db_path)
        db.create_prompt_version(versioned["id"], {}, self.db_path)
        db.create_prompt_version(versioned["id"], {}, self.db_path)

        projects = {item["id"]: item for item in db.list_projects(self.db_path)}

        self.assertEqual(projects[versioned["id"]]["versionCount"], 2)
        self.assertEqual(projects[versioned["id"]]["latestVersion"], 2)
        self.assertEqual(projects[empty["id"]]["versionCount"], 0)
        self.assertIsNone(projects[empty["id"]]["latestVersion"])

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

    def test_base_version_rejects_stale_writes_inside_version_transaction(self):
        project = db.create_project({}, self.db_path)
        first = db.create_prompt_version(
            project["id"],
            {"baseVersion": 0, "source": "first"},
            self.db_path,
        )

        self.assertEqual(first["version"], 1)
        with self.assertRaises(db.VersionConflictError) as context:
            db.create_prompt_version(
                project["id"],
                {"baseVersion": 0, "source": "stale"},
                self.db_path,
            )
        self.assertEqual(context.exception.expected_base_version, 0)
        self.assertEqual(context.exception.current_version, 1)
        second = db.create_prompt_version(
            project["id"],
            {"baseVersion": 1, "source": "second"},
            self.db_path,
        )
        self.assertEqual(second["version"], 2)
        self.assertEqual(
            [item["source"] for item in db.get_project(project["id"], self.db_path)["versions"]],
            ["first", "second"],
        )
        for invalid in (None, True, -1, "1", 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                db.create_prompt_version(
                    project["id"],
                    {"baseVersion": invalid},
                    self.db_path,
                )

    def test_concurrent_writes_with_same_base_version_allow_only_one(self):
        project = db.create_project({}, self.db_path)

        def save(index):
            try:
                return db.create_prompt_version(
                    project["id"],
                    {"baseVersion": 0, "source": f"worker-{index}"},
                    self.db_path,
                )
            except db.VersionConflictError as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, range(2)))

        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(sum(isinstance(item, db.VersionConflictError) for item in results), 1)
        self.assertEqual(
            len(db.get_project(project["id"], self.db_path)["versions"]),
            1,
        )

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

    def test_project_create_idempotency_replays_and_rejects_changed_body(self):
        payload = {"id": "project-idem", "name": "First"}
        first = db.create_project(
            payload, self.db_path, idempotency_key="create-1"
        )
        replay = db.create_project(
            {"name": "First", "id": "project-idem"},
            self.db_path,
            idempotency_key="create-1",
        )

        self.assertEqual(replay, first)
        with self.assertRaises(db.IdempotencyConflictError):
            db.create_project(
                {"id": "project-idem-2", "name": "Changed"},
                self.db_path,
                idempotency_key="create-1",
            )
        with db.database(self.db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                1,
            )

    def test_version_idempotency_replays_before_stale_preconditions(self):
        project = db.create_project({"id": "project-version-idem"}, self.db_path)
        payload = {
            "id": "version-idem",
            "baseVersion": 0,
            "baseUpdatedAt": project["updatedAt"],
            "positiveEn": "one",
        }
        first = db.create_prompt_version(
            project["id"], payload, self.db_path, idempotency_key="version-1"
        )
        replay = db.create_prompt_version(
            project["id"], payload, self.db_path, idempotency_key="version-1"
        )

        self.assertEqual(replay, first)
        with db.database(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM prompt_versions WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()[0],
                1,
            )

    def test_project_update_idempotency_replays_after_timestamp_changes(self):
        project = db.create_project({"id": "project-update-idem"}, self.db_path)
        payload = {"name": "Saved", "baseUpdatedAt": project["updatedAt"]}
        first = db.update_project(
            project["id"], payload, self.db_path, idempotency_key="update-1"
        )
        replay = db.update_project(
            project["id"], payload, self.db_path, idempotency_key="update-1"
        )
        self.assertEqual(replay, first)

    def test_internal_idempotency_records_are_hidden_and_protected(self):
        db.create_project(
            {"id": "project-hidden-ledger"},
            self.db_path,
            idempotency_key="hidden-1",
        )
        self.assertEqual(db.get_settings(self.db_path), {})
        with self.assertRaises(ValueError):
            db.put_settings(
                {f"{db.INTERNAL_SETTINGS_PREFIX}attack": "value"}, self.db_path
            )

    def test_workspace_commit_is_atomic_and_replayable(self):
        payload = {
            "operationId": "save-operation-1",
            "createProject": True,
            "project": {
                "id": "project-atomic",
                "name": "Atomic",
                "mode": "text",
                "status": "draft",
                "metadata": {"workspaceBaseVersion": 1},
            },
            "version": {
                "id": "version-atomic-1",
                "baseVersion": 0,
                "source": "manual",
                "positiveEn": "masterpiece",
                "blocks": [],
                "metadata": {"recipe": {"schemaVersion": 1}},
            },
        }
        first = db.commit_workspace(payload, self.db_path)
        replay = db.commit_workspace(payload, self.db_path)

        self.assertEqual(replay, first)
        self.assertEqual(first["version"]["version"], 1)
        self.assertEqual(first["project"]["updatedAt"], first["version"]["projectUpdatedAt"])
        with db.database(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0], 1)

    def test_workspace_commit_changed_replay_and_failed_write_do_not_partially_mutate(self):
        payload = {
            "operationId": "save-operation-2",
            "createProject": True,
            "project": {"id": "project-atomic-2", "name": "Original"},
            "version": {
                "id": "version-atomic-2",
                "baseVersion": 0,
                "positiveEn": "one",
            },
        }
        db.commit_workspace(payload, self.db_path)
        changed = {
            **payload,
            "project": {**payload["project"], "name": "Changed"},
        }
        with self.assertRaises(db.IdempotencyConflictError):
            db.commit_workspace(changed, self.db_path)
        loaded = db.get_project("project-atomic-2", self.db_path)
        self.assertEqual(loaded["name"], "Original")
        self.assertEqual(len(loaded["versions"]), 1)

    def test_workspace_ledger_failure_rolls_back_project_and_version_together(self):
        payload = {
            "operationId": "save-ledger-failure",
            "createProject": True,
            "project": {"id": "project-ledger-failure", "name": "Rollback"},
            "version": {
                "id": "version-ledger-failure",
                "baseVersion": 0,
                "positiveEn": "must not survive",
            },
        }

        with patch.object(
            db,
            "_write_idempotency_result",
            side_effect=RuntimeError("simulated ledger failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "ledger failure"):
                db.commit_workspace(payload, self.db_path)

        self.assertIsNone(db.get_project("project-ledger-failure", self.db_path))
        with db.database(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM prompt_versions WHERE id = ?",
                    ("version-ledger-failure",),
                ).fetchone()[0],
                0,
            )

    def test_corrupt_or_dangling_idempotency_ledger_fails_closed(self):
        payload = {
            "operationId": "save-ledger-corrupt",
            "createProject": True,
            "project": {"id": "project-ledger-corrupt"},
            "version": {
                "id": "version-ledger-corrupt",
                "baseVersion": 0,
            },
        }
        db.commit_workspace(payload, self.db_path)
        with db.database(self.db_path) as connection:
            key = connection.execute(
                "SELECT key FROM settings WHERE key LIKE ?",
                (f"{db.INTERNAL_SETTINGS_PREFIX}%",),
            ).fetchone()[0]
            connection.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ('"corrupt"', key),
            )

        with self.assertRaises(db.IdempotencyLedgerError):
            db.commit_workspace(payload, self.db_path)
        self.assertEqual(
            len(db.get_project("project-ledger-corrupt", self.db_path)["versions"]),
            1,
        )

        # Restore a valid ledger in a fresh operation, then delete its referenced
        # rows to prove a replay cannot fabricate a successful response.
        second = {
            "operationId": "save-ledger-dangling",
            "createProject": True,
            "project": {"id": "project-ledger-dangling"},
            "version": {
                "id": "version-ledger-dangling",
                "baseVersion": 0,
            },
        }
        db.commit_workspace(second, self.db_path)
        with db.database(self.db_path) as connection:
            connection.execute(
                "DELETE FROM projects WHERE id = ?",
                ("project-ledger-dangling",),
            )
        with self.assertRaises(db.IdempotencyLedgerError):
            db.commit_workspace(second, self.db_path)

    def test_concurrent_changed_replays_have_one_winner_and_no_partial_rows(self):
        base = {
            "operationId": "save-concurrent-changed",
            "createProject": True,
            "project": {"id": "project-concurrent-changed", "name": "Alpha"},
            "version": {
                "id": "version-concurrent-changed",
                "baseVersion": 0,
            },
        }
        changed = {
            **base,
            "project": {**base["project"], "name": "Beta"},
        }

        def submit(payload):
            try:
                return db.commit_workspace(payload, self.db_path)
            except Exception as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, (base, changed)))

        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [
            item for item in results if isinstance(item, db.IdempotencyConflictError)
        ]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        stored = db.get_project("project-concurrent-changed", self.db_path)
        self.assertEqual(stored["name"], successes[0]["project"]["name"])
        self.assertEqual(len(stored["versions"]), 1)

    def test_extremely_large_block_numbers_are_rejected_as_validation_errors(self):
        huge = 10**400
        for key in ("weight", "confidence"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                db.normalize_blocks([{"id": "scene", key: huge}])


if __name__ == "__main__":
    unittest.main()
