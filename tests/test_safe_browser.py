from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.safe_browser import PersistentChromeSession, _read_devtools_active_port


class DevToolsPortTests(unittest.TestCase):
    def test_reads_port_written_by_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir)
            (profile_dir / "DevToolsActivePort").write_text(
                "49321\n/devtools/browser/test\n",
                encoding="utf-8",
            )
            self.assertEqual(_read_devtools_active_port(profile_dir), 49321)

    def test_ignores_missing_or_invalid_port_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir)
            self.assertIsNone(_read_devtools_active_port(profile_dir))
            (profile_dir / "DevToolsActivePort").write_text("not-a-port\n", encoding="utf-8")
            self.assertIsNone(_read_devtools_active_port(profile_dir))

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_adopts_existing_profile_debug_port(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir)
            (profile_dir / "DevToolsActivePort").write_text("41234\n", encoding="utf-8")
            session = PersistentChromeSession(profile_dir)
            with patch.object(session, "_debug_is_available", return_value=True):
                self.assertTrue(session._adopt_running_session())
            self.assertEqual(session.debug_port, 41234)

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_prefers_nested_cashier_tab_over_confirm_tab(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = PersistentChromeSession(Path(temp_dir))
            pages = [
                {"id": "product", "type": "page", "url": "https://item.taobao.com/item.htm?id=1"},
                {
                    "id": "confirm",
                    "openerId": "product",
                    "type": "page",
                    "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
                },
                {
                    "id": "cashier",
                    "openerId": "confirm",
                    "type": "page",
                    "url": "https://cashier.alipay.com/standard/payment/cashier.htm",
                },
            ]
            with patch.object(session, "_list_pages", return_value=pages):
                self.assertEqual(session._page_target("product")["id"], "cashier")

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_prefers_deep_unknown_result_tab_over_parent_confirm_tab(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = PersistentChromeSession(Path(temp_dir))
            pages = [
                {"id": "product", "type": "page", "url": "http://127.0.0.1/product.html"},
                {"id": "confirm", "openerId": "product", "type": "page", "url": "http://127.0.0.1/confirm.html"},
                {"id": "result", "openerId": "confirm", "type": "page", "url": "http://127.0.0.1/pending.html"},
            ]
            with patch.object(session, "_list_pages", return_value=pages):
                self.assertEqual(session._page_target("product")["id"], "result")

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_uses_new_tab_baseline_when_chrome_omits_opener_id(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = PersistentChromeSession(Path(temp_dir))
            session._target_baselines["product"] = {"product"}
            pages = [
                {"id": "cashier", "type": "page", "url": "https://render.alipay.com/p/cashier/index.html"},
                {"id": "confirm", "type": "page", "url": "https://buy.taobao.com/confirm_order.htm"},
                {"id": "product", "type": "page", "url": "https://item.taobao.com/item.htm?id=1"},
            ]
            with patch.object(session, "_list_pages", return_value=pages):
                self.assertEqual(session._page_target("product")["id"], "cashier")


if __name__ == "__main__":
    unittest.main()
