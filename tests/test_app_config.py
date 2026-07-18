from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app_config import AppConfig, load_config, save_config, validate_port


class AppConfigTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            expected = AppConfig(port=9123, chrome_path=r"D:\Apps\Chrome\chrome.exe", first_run_complete=True)
            path = save_config(expected, data_dir)
            self.assertEqual(load_config(data_dir), expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["port"], 9123)

    def test_environment_overrides_file_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            save_config(AppConfig(port=8550, chrome_path="old.exe"), data_dir)
            with patch.dict(
                os.environ,
                {"TAOBAO_ASSISTANT_PORT": "9001", "TAOBAO_ASSISTANT_CHROME": "new.exe"},
            ):
                loaded = load_config(data_dir)
            self.assertEqual(loaded.port, 9001)
            self.assertEqual(loaded.chrome_path, "new.exe")

    def test_invalid_file_port_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "config.json").write_text('{"port": 80}', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TAOBAO_ASSISTANT_PORT", None)
                self.assertEqual(load_config(data_dir).port, 8550)

    def test_validate_port_rejects_privileged_or_too_large_values(self) -> None:
        for value in (80, 65536, "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_port(value)


if __name__ == "__main__":
    unittest.main()

