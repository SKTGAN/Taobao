from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app_config import AppConfig
from src.environment_check import run_environment_checks


class EnvironmentCheckTests(unittest.TestCase):
    @patch("src.environment_check.find_google_chrome", return_value=Path("C:/Chrome/chrome.exe"))
    @patch("src.environment_check.platform.system", return_value="Windows")
    def test_offline_checks_cover_required_local_dependencies(self, _system, _chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_environment_checks(
                AppConfig(port=8550),
                Path(temp_dir),
                include_network=False,
            )
        names = {item.name for item in results}
        self.assertEqual(names, {"操作系统", "Python", "数据目录", "Google Chrome", "服务端口"})
        self.assertTrue(all(item.passed for item in results))


if __name__ == "__main__":
    unittest.main()

