from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.safe_browser import (
    BrowserLaunchError,
    PersistentChromeSession,
    _read_devtools_active_port,
    find_google_chrome,
)


class ChromeDiscoveryTests(unittest.TestCase):
    def test_uses_configured_chrome_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chrome = Path(temp_dir) / "custom" / "chrome.exe"
            chrome.parent.mkdir()
            chrome.touch()
            self.assertEqual(find_google_chrome(str(chrome)), chrome.resolve())

    def test_rejects_missing_configured_chrome_path(self) -> None:
        with self.assertRaises(BrowserLaunchError):
            find_google_chrome(r"Z:\missing\chrome.exe")


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
    def test_ignores_privacy_rule_tab_and_keeps_confirm_order(self, _find_chrome) -> None:
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
                    "id": "privacy",
                    "openerId": "confirm",
                    "type": "page",
                    "url": "https://huodong.taobao.com/wow/z/mt/default/phone-privacy-1-0",
                },
            ]
            with patch.object(session, "_list_pages", return_value=pages):
                self.assertEqual(session._page_target("product")["id"], "confirm")
                auxiliary = session.auxiliary_pages()
                self.assertEqual([item["id"] for item in auxiliary], ["privacy"])

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_navigates_prepared_tab_to_exact_sku_url(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = PersistentChromeSession(Path(temp_dir))
            cdp_session = MagicMock()
            context = MagicMock()
            context.__enter__.return_value = cdp_session
            session.cdp = MagicMock()
            session.cdp.session.return_value = context
            pages = [{"id": "product", "webSocketDebuggerUrl": "ws://page/product"}]
            with patch.object(session, "_list_pages", return_value=pages):
                target_id = session.navigate_product(
                    "https://item.taobao.com/item.htm?id=1&skuId=606&spm=tracking",
                    "product",
                )
            self.assertEqual(target_id, "product")
            self.assertEqual(
                cdp_session.call.call_args_list[1].args,
                ("Page.navigate", {"url": "https://item.taobao.com/item.htm?id=1&skuId=606"}),
            )

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

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_click_brings_target_to_front_before_mouse_events(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = PersistentChromeSession(Path(temp_dir))
            cdp_session = MagicMock()
            cdp_session.call.side_effect = [
                {},
                {"result": {"type": "object", "value": {"visible": True, "focused": True}}},
                {
                    "result": {
                        "type": "object",
                        "value": {"found": True, "text": "提交订单 ￥0.01", "x": 100.0, "y": 200.0},
                    }
                },
                {},
                {},
                {},
            ]
            context = MagicMock()
            context.__enter__.return_value = cdp_session
            session.cdp = MagicMock()
            session.cdp.session.return_value = context
            target = {"id": "confirm", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/confirm"}

            with (
                patch.object(session, "_page_target", return_value=target),
                patch("src.safe_browser.time.sleep"),
            ):
                result = session.click_action(("提交订单",), "product")

            methods = [item.args[0] for item in cdp_session.call.call_args_list]
            self.assertEqual(methods[0], "Page.bringToFront")
            self.assertEqual(methods[1:3], ["Runtime.evaluate", "Runtime.evaluate"])
            self.assertEqual(methods[3:], ["Input.dispatchMouseEvent"] * 3)
            self.assertTrue(result["clicked"])

    @patch("src.safe_browser.find_google_chrome", return_value=Path("chrome.exe"))
    def test_reload_payment_page_brings_target_to_front_without_clicking(self, _find_chrome) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = PersistentChromeSession(Path(temp_dir))
            cdp_session = MagicMock()
            cdp_session.call.side_effect = [{}, {}]
            context = MagicMock()
            context.__enter__.return_value = cdp_session
            session.cdp = MagicMock()
            session.cdp.session.return_value = context
            target = {"id": "payment", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/payment"}

            with patch.object(session, "_page_target", return_value=target):
                session.reload_page("product")

            self.assertEqual(
                [item.args[0] for item in cdp_session.call.call_args_list],
                ["Page.bringToFront", "Page.reload"],
            )
            self.assertEqual(cdp_session.call.call_args_list[1].args[1], {"ignoreCache": True})


if __name__ == "__main__":
    unittest.main()
