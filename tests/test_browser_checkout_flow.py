from __future__ import annotations

import http.server
import os
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

from src.safe_browser import BrowserLaunchError, PersistentChromeSession, find_google_chrome
from src.task_runner import SingleAccountTaskRunner
from src.v2_store import V2Store


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args) -> None:
        return None


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, _request, _client_address) -> None:
        return None


class FixedSessions:
    def __init__(self, session):
        self.session = session

    def get_or_create(self, _account):
        return self.session


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1")
class BrowserCheckoutFlowTests(unittest.TestCase):
    def test_real_chrome_reaches_mock_pending_payment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_dir = Path(__file__).parent / "mock_shop"
            handler = partial(QuietHandler, directory=str(mock_dir))
            server = QuietServer(("127.0.0.1", 0), handler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()

            profile_dir = root / "chrome-profile"
            profile_dir.mkdir()
            product_url = f"http://127.0.0.1:{server.server_port}/product.html"
            process = subprocess.Popen(
                [
                    str(find_google_chrome()),
                    "--headless=new",
                    "--disable-gpu",
                    "--disable-background-networking",
                    "--no-first-run",
                    f"--user-data-dir={profile_dir}",
                    "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-allow-origins=http://127.0.0.1",
                    product_url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 15
                session = PersistentChromeSession(profile_dir)
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("headless Chrome exited before DevTools became available")
                    # Chrome can create DevToolsActivePort shortly before its
                    # HTTP discovery endpoint starts accepting connections.
                    if (profile_dir / "DevToolsActivePort").exists() and session._adopt_running_session():
                        break
                    time.sleep(0.1)
                else:
                    self.fail("headless Chrome DevTools endpoint did not become available")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        if session.inspect_page().kind == "product":
                            break
                    except BrowserLaunchError:
                        # The first page WebSocket can be replaced once while
                        # headless Chrome finishes creating its initial tab.
                        pass
                    time.sleep(0.1)
                else:
                    self.fail("mock product page did not become ready")
                product_target_id = next(
                    str(page.get("id") or "")
                    for page in session._list_pages()
                    if str(page.get("url") or "").endswith("/product.html")
                )

                store = V2Store(root / "assistant.db")
                account_id = store.add_account("浏览器测试账号")
                product_id = store.add_product("模拟商品", "https://item.taobao.com/item.htm?id=1")
                task_id = store.add_task(
                    "浏览器结算流程测试",
                    account_id,
                    product_id,
                    (datetime.now() - timedelta(seconds=1)).isoformat(timespec="milliseconds"),
                )
                store.authorize_task(task_id)
                outcome = SingleAccountTaskRunner(store, FixedSessions(session)).run(task_id, product_target_id)
                self.assertEqual(outcome.status, "待付款", outcome.message)
                self.assertEqual(session.inspect_page().kind, "pending_payment")
                self.assertFalse(
                    any(str(page.get("url") or "").endswith("/privacy.html") for page in session._list_pages()),
                    "submit click opened the privacy-help page instead of the payment page",
                )
            finally:
                server.shutdown()
                server.server_close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
