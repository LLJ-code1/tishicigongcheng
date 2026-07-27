import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import managed_assets  # noqa: E402


def png_bytes(color=(12, 34, 56)):
    output = io.BytesIO()
    Image.new("RGB", (8, 4), color=color).save(output, format="PNG")
    return output.getvalue()


class ManagedAssetTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "prompt_studio.db"
        db.init_db(self.db_path)
        self.asset_root = self.root / "managed-assets"
        self.asset_root_patch = patch.object(
            managed_assets, "DEFAULT_ASSET_ROOT", self.asset_root
        )
        self.asset_root_patch.start()

    def tearDown(self):
        self.asset_root_patch.stop()
        self.tempdir.cleanup()

    def test_import_deduplicates_by_hash_and_creates_thumbnail_outside_sqlite(self):
        source = png_bytes()
        first, created = managed_assets.import_png(source, self.db_path)
        replay, replay_created = managed_assets.import_png(source, self.db_path)

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(replay["id"], first["id"])
        self.assertEqual(len(db.list_managed_assets(self.db_path)), 1)
        self.assertEqual(managed_assets.read_asset_file(first), source)
        self.assertGreater(len(managed_assets.read_asset_file(first, thumbnail=True)), 20)
        with db.database(self.db_path) as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info('managed_assets')")
            }
        self.assertNotIn("image_bytes", columns)

    def test_rejects_non_png_and_deletes_exactly_confirmed_asset_paths(self):
        with self.assertRaises(managed_assets.ManagedAssetError):
            managed_assets.import_png(b"not a png", self.db_path)
        asset, _ = managed_assets.import_png(png_bytes((1, 2, 3)), self.db_path)
        managed_assets.delete_asset_files(asset)
        self.assertFalse(
            (self.asset_root / asset["relativePath"]).exists()
        )
        self.assertFalse(
            (self.asset_root / asset["thumbnailRelativePath"]).exists()
        )
