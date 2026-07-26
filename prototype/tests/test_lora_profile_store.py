import tempfile
import unittest
from pathlib import Path

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import lora_profile_store as store  # noqa: E402


def profile_payload(**overrides):
    value = {
        "id": "lora-rain-style",
        "name": "Rain Style",
        "version": "v1.2",
        "sourceUrl": "https://civitai.com/models/123/rain-style",
        "triggerWords": ["rain style", "wet pavement"],
        "suggestedWeight": 0.8,
        "notes": "用于雨夜质感",
        "compatibleModelProfileIds": ["anima-1.1-v1"],
        "conflictLoraProfileIds": ["lora-dry-style"],
        "compatibilityNotes": "不要和干燥质感 LoRA 同用",
    }
    value.update(overrides)
    return value


class LoraProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "lora.db"
        db.init_db(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_list_update_and_delete_use_existing_resources_table(self):
        created = store.create_lora_profile(
            profile_payload(), db_path=self.database
        )
        self.assertEqual(created["id"], "lora-rain-style")
        self.assertEqual(created["suggestedWeight"], 0.8)
        self.assertEqual(
            store.get_lora_profile(created["id"], db_path=self.database),
            created,
        )
        self.assertEqual(
            store.list_lora_profiles(db_path=self.database), [created]
        )
        with db.database(self.database) as connection:
            row = connection.execute(
                "SELECT type FROM resources WHERE id = ?", (created["id"],)
            ).fetchone()
        self.assertEqual(row["type"], "lora_profile")

        updated = store.update_lora_profile(
            created["id"],
            {
                **profile_payload(name="Rain Style Revised"),
                "baseUpdatedAt": created["updatedAt"],
            },
            db_path=self.database,
        )
        self.assertEqual(updated["name"], "Rain Style Revised")
        self.assertNotEqual(updated["updatedAt"], created["updatedAt"])

        with self.assertRaises(store.LoraProfileConflictError):
            store.update_lora_profile(
                created["id"],
                {
                    **profile_payload(name="stale"),
                    "baseUpdatedAt": created["updatedAt"],
                },
                db_path=self.database,
            )

        with self.assertRaises(store.LoraProfileConflictError):
            store.delete_lora_profile(
                created["id"],
                base_updated_at=created["updatedAt"],
                db_path=self.database,
            )
        self.assertTrue(
            store.delete_lora_profile(
                created["id"],
                base_updated_at=updated["updatedAt"],
                db_path=self.database,
            )
        )
        self.assertIsNone(
            store.get_lora_profile(created["id"], db_path=self.database)
        )

    def test_normalization_rejects_unsafe_or_speculative_fields(self):
        cases = [
            profile_payload(sourceUrl="http://example.com/lora"),
            profile_payload(suggestedWeight=2.1),
            profile_payload(triggerWords=["same", "same"]),
            profile_payload(conflictLoraProfileIds=["lora-rain-style"]),
            profile_payload(filename="must-not-be-accepted.safetensors"),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    store.create_lora_profile(payload, db_path=self.database)

    def test_create_requires_unique_id_and_update_cannot_change_identity(self):
        created = store.create_lora_profile(
            profile_payload(), db_path=self.database
        )
        with self.assertRaises(store.LoraProfileConflictError):
            store.create_lora_profile(profile_payload(), db_path=self.database)
        with self.assertRaises(ValueError):
            store.update_lora_profile(
                created["id"],
                {
                    **profile_payload(id="lora-other"),
                    "baseUpdatedAt": created["updatedAt"],
                },
                db_path=self.database,
            )


if __name__ == "__main__":
    unittest.main()
