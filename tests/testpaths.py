import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.paths import STYLE_CATALOG_DIR_NAME, migrate_style_catalog_storage


class RuntimePathTest(unittest.TestCase):
    def test_style_catalog_storage_migrates_directory_and_database_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "daily_life.db"
            legacy = root / "style_catalog"
            legacy.mkdir()
            image = legacy / "style_test.jpg"
            image.write_bytes(b"test image")
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE style_catalog_items "
                    "(image_path TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO style_catalog_items(image_path) VALUES (?)",
                    (str(image),),
                )
                connection.commit()
            finally:
                connection.close()

            migrate_style_catalog_storage(database)

            current = root / STYLE_CATALOG_DIR_NAME
            self.assertTrue((current / image.name).is_file())
            self.assertFalse(legacy.exists())
            connection = sqlite3.connect(database)
            try:
                stored_path = connection.execute(
                    "SELECT image_path FROM style_catalog_items"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(stored_path, str(current / image.name))
