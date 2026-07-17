import hashlib
import json
import sqlite3
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup  # noqa: E402
import db  # noqa: E402


class LogicalBackupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "source.db"
        db.init_db(self.source)
        self.project = db.create_project(
            {
                "id": "project-backup",
                "name": "Backup project",
                "mode": "text",
                "metadata": {"draftInput": "夜景", "apiTextKey": "must-be-scrubbed"},
            },
            self.source,
        )
        self.version = db.create_prompt_version(
            self.project["id"],
            {
                "id": "version-backup-v1",
                "baseVersion": 0,
                "source": "manual",
                "positiveEn": "1girl, night city",
                "positiveZh": "少女，夜晚城市",
                "negativeEn": "low quality",
                "negativeZh": "低质量",
                "blocks": [
                    {
                        "id": "scene",
                        "label": "场景",
                        "en": "night city",
                        "zh": "夜晚城市",
                        "weight": 100,
                        "locked": False,
                        "source": "manual",
                    }
                ],
                "metadata": {"recipeHash": "a" * 64, "localTextKey": "remove-me"},
            },
            self.source,
        )
        db.upsert_favorite(
            {
                "id": "favorite-character-1",
                "type": "characters",
                "name": "Character",
                "source": "AnimaDex",
                "sourceId": "character-1",
                "targetBlock": "subject",
                "en": "character tag",
                "thumbUrl": "data:image/png;base64,SECRET",
                "hasImage": True,
            },
            self.source,
        )
        db.put_settings(
            {
                "textProvider": "local",
                "autoCombine": True,
                "apiTextKey": "top-secret",
                "apiTextUrl": "https://user:password@example.invalid/v1",
            },
            self.source,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def _read_zip_json(path: Path, member: str):
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read(member).decode("utf-8"))

    @staticmethod
    def _replace_zip_member(path: Path, member: str, raw: bytes) -> None:
        with zipfile.ZipFile(path) as archive:
            members = {info.filename: archive.read(info) for info in archive.infolist()}
        members[member] = raw
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in members.items():
                archive.writestr(name, value)

    def test_project_zip_round_trip_restores_exact_history_into_new_database(self):
        archive = self.root / "project.zip"

        manifest = backup.export_project(
            archive,
            self.project["id"],
            db_path=self.source,
        )
        inspected = backup.inspect_backup(archive)
        target = self.root / "restored.db"
        restored = backup.restore_backup(archive, target)

        self.assertEqual(manifest["scope"], "project")
        self.assertEqual(inspected["counts"], {
            "projects": 1,
            "versions": 1,
            "resources": 0,
            "favorites": 0,
            "settings": 0,
        })
        self.assertTrue(restored["createdTarget"])
        loaded = db.get_project(self.project["id"], target)
        self.assertEqual(loaded["name"], "Backup project")
        self.assertEqual(loaded["versions"][0]["positiveEn"], "1girl, night city")
        self.assertNotIn("apiTextKey", loaded["metadata"])
        self.assertNotIn("localTextKey", loaded["versions"][0]["metadata"])

    def test_full_backup_excludes_secrets_internal_ledger_and_binary_data_urls(self):
        db.create_project(
            {"id": "idempotent-project"},
            self.source,
            idempotency_key="backup-ledger-key",
        )
        archive = self.root / "full.zip"

        backup.export_database(archive, db_path=self.source)

        settings = self._read_zip_json(archive, backup.SETTINGS_PATH)["items"]
        favorites = self._read_zip_json(archive, backup.FAVORITES_PATH)["items"]
        serialized = archive.read_bytes()
        self.assertEqual({item["key"] for item in settings}, {"autoCombine", "textProvider"})
        self.assertEqual(favorites[0]["thumbUrl"], "")
        self.assertNotIn(b"top-secret", serialized)
        self.assertNotIn(b"password", serialized)
        self.assertNotIn(b"backup-ledger-key", serialized)
        self.assertNotIn(b"SECRET", serialized)

    def test_hash_tampering_is_rejected_before_target_is_created(self):
        archive = self.root / "tampered.zip"
        backup.export_project(archive, self.project["id"], db_path=self.source)
        projects = self._read_zip_json(archive, backup.PROJECTS_PATH)
        projects["items"][0]["name"] = "Tampered"
        self._replace_zip_member(
            archive,
            backup.PROJECTS_PATH,
            json.dumps(projects, ensure_ascii=False).encode("utf-8"),
        )
        target = self.root / "must-not-exist.db"

        with self.assertRaisesRegex(backup.BackupValidationError, "hash or size mismatch"):
            backup.restore_backup(archive, target)

        self.assertFalse(target.exists())

    def test_zip_slip_duplicate_paths_and_compression_bomb_are_rejected(self):
        attacks = {
            "zip-slip.zip": [("manifest.json", b"{}"), ("../projects.json", b"{}")],
            "duplicate.zip": [("manifest.json", b"{}"), ("manifest.json", b"{}")],
            "bomb.zip": [("manifest.json", b"{}"), ("projects.json", b"0" * 100_000)],
        }
        for filename, members in attacks.items():
            with self.subTest(filename=filename):
                path = self.root / filename
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for name, raw in members:
                            archive.writestr(name, raw)
                if filename == "bomb.zip":
                    with patch.object(backup, "MAX_COMPRESSION_RATIO", 2):
                        with self.assertRaises(backup.BackupValidationError):
                            backup.inspect_backup(path)
                else:
                    with self.assertRaises(backup.BackupValidationError):
                        backup.inspect_backup(path)

    def test_conflict_reject_is_atomic_and_rename_preserves_both_projects(self):
        archive = self.root / "project.zip"
        backup.export_project(archive, self.project["id"], db_path=self.source)
        target = self.root / "target.db"
        db.init_db(target)
        existing = db.create_project(
            {"id": self.project["id"], "name": "Existing"},
            target,
        )
        before = hashlib.sha256(target.read_bytes()).hexdigest()

        with self.assertRaises(backup.BackupConflictError):
            backup.restore_backup(archive, target, conflict="reject")

        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        report = backup.restore_backup(archive, target, conflict="rename")
        renamed_id = report["renamedProjects"][0]["to"]
        self.assertEqual(db.get_project(existing["id"], target)["name"], "Existing")
        self.assertEqual(db.get_project(renamed_id, target)["name"], "Backup project")
        self.assertEqual(db.get_project(renamed_id, target)["versions"][0]["projectId"], renamed_id)

    def test_invalid_archive_does_not_modify_existing_target(self):
        archive = self.root / "invalid.zip"
        archive.write_bytes(b"not a backup")
        target = self.root / "target.db"
        db.init_db(target)
        db.create_project({"id": "keep-me"}, target)
        before = target.read_bytes()

        with self.assertRaises(backup.BackupValidationError):
            backup.restore_backup(archive, target)

        self.assertEqual(target.read_bytes(), before)
        self.assertIsNotNone(db.get_project("keep-me", target))

    def test_json_export_is_strict_and_round_trips(self):
        archive = self.root / "project.json"
        backup.export_project(
            archive,
            self.project["id"],
            db_path=self.source,
            archive_format="json",
        )

        inspected = backup.inspect_backup(archive)
        target = self.root / "json-restored.db"
        backup.restore_backup(archive, target)

        self.assertEqual(inspected["manifest"]["format"], backup.FORMAT_ID)
        self.assertIsNotNone(db.get_project(self.project["id"], target))

    def test_restore_refuses_the_configured_default_database(self):
        archive = self.root / "project.zip"
        backup.export_project(archive, self.project["id"], db_path=self.source)
        protected = self.root / "protected.db"
        db.init_db(protected)
        before = protected.read_bytes()

        with patch.object(db, "DEFAULT_DB_PATH", protected):
            with self.assertRaisesRegex(backup.BackupValidationError, "default"):
                backup.restore_backup(archive, protected)

        self.assertEqual(protected.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
