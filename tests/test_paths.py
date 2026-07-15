from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.paths import default_data_dir, migrate_legacy_data
from src.v2_store import V2Store


class PortablePathTests(unittest.TestCase):
    def test_environment_override_controls_default_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"TAOBAO_ASSISTANT_DATA_DIR": temp_dir}):
                self.assertEqual(default_data_dir(), Path(temp_dir).resolve())

    def test_local_app_data_is_used_when_no_override_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}, clear=False):
                os.environ.pop("TAOBAO_ASSISTANT_DATA_DIR", None)
                self.assertEqual(default_data_dir(), (Path(temp_dir) / "TaobaoAssistant").resolve())

    def test_legacy_database_and_profile_are_copied_without_deleting_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "project-data"
            target = root / "user-data"
            store = V2Store(legacy / "taobao_assistant_v2.db")
            account_id = store.add_account("测试账号")
            account = store.get_account(account_id)
            source_profile = Path(account["profile_dir"])
            (source_profile / "profile-marker.txt").write_text("cookie-placeholder", encoding="utf-8")

            self.assertTrue(migrate_legacy_data(legacy, target))
            self.assertTrue((legacy / "taobao_assistant_v2.db").is_file())
            self.assertTrue(source_profile.is_dir())
            self.assertTrue((target / "migration-from-project-data.txt").is_file())

            with closing(sqlite3.connect(target / "taobao_assistant_v2.db")) as connection:
                migrated_profile = Path(
                    connection.execute("SELECT profile_dir FROM accounts WHERE id=?", (account_id,)).fetchone()[0]
                )
            self.assertTrue(migrated_profile.parent.samefile(target / "profiles"))
            self.assertEqual((migrated_profile / "profile-marker.txt").read_text(encoding="utf-8"), "cookie-placeholder")
            self.assertFalse(migrate_legacy_data(legacy, target))


if __name__ == "__main__":
    unittest.main()
