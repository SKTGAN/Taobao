from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from src.cdp_client import CdpClient, CdpError
from src.page_automation import (
    PAGE_SNAPSHOT_SCRIPT,
    PageSnapshot,
    build_click_action_script,
    classify_page,
)
from src.product_urls import normalize_product_url


LOGIN_URL = "https://login.taobao.com/"
ACCOUNT_HOME_URL = "https://i.taobao.com/my_taobao.htm"
CART_URL = "https://cart.taobao.com/cart.htm"


class BrowserLaunchError(RuntimeError):
    pass


def _new_target_url(debug_base: str, target_url: str) -> str:
    # Chrome's /json/new endpoint treats a raw '&' as a separator and truncates
    # product URLs. Encode the entire target URL as one opaque query value.
    encoded = urllib.parse.quote(target_url, safe="")
    return f"{debug_base}/json/new?{encoded}"


def find_google_chrome() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    command = shutil.which("chrome") or shutil.which("chrome.exe")
    if command:
        candidates.insert(0, Path(command))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BrowserLaunchError("未找到 Google Chrome，请先安装 Chrome 浏览器。")


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_devtools_active_port(profile_dir: str | Path) -> int | None:
    """Read the port written by Chrome for an existing profile session."""
    active_port_file = Path(profile_dir) / "DevToolsActivePort"
    try:
        first_line = active_port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        port = int(first_line)
    except (OSError, ValueError, IndexError):
        return None
    return port if 1 <= port <= 65535 else None


class PersistentChromeSession:
    """直接启动可见 Google Chrome；不依赖 ChromeDriver，不修改浏览器指纹。"""

    def __init__(self, profile_dir: str | Path, _driver_path: str = ""):
        self.profile_dir = Path(profile_dir).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.chrome_path = find_google_chrome()
        self.debug_port = _free_local_port()
        self.process: subprocess.Popen | None = None
        self.cdp = CdpClient(timeout=8.0)
        self._target_baselines: dict[str, set[str]] = {}

    @property
    def _debug_base(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}"

    def _debug_is_available(self, timeout: float = 0.5) -> bool:
        if not self.debug_port:
            return False
        try:
            with urllib.request.urlopen(f"{self._debug_base}/json/version", timeout=timeout):
                return True
        except Exception:
            return False

    def _adopt_running_session(self) -> bool:
        active_port = _read_devtools_active_port(self.profile_dir)
        if active_port is None:
            return False
        previous_port = self.debug_port
        self.debug_port = active_port
        if self._debug_is_available():
            return True
        self.debug_port = previous_port
        return False

    def _launch(self, url: str) -> None:
        active_port_file = self.profile_dir / "DevToolsActivePort"
        try:
            active_port_file.unlink(missing_ok=True)
        except OSError:
            pass
        self.debug_port = _free_local_port()
        args = [
            str(self.chrome_path),
            f"--user-data-dir={self.profile_dir}",
            "--profile-directory=Default",
            f"--remote-debugging-port={self.debug_port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=http://127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "--new-window",
            url,
        ]
        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise BrowserLaunchError(f"Google Chrome 启动失败：{exc}") from exc

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._debug_is_available():
                return
            if self._adopt_running_session():
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.2)
        raise BrowserLaunchError(
            "无法连接该账号的 Chrome。请只关闭这个账号的旧 Chrome 窗口后重试；"
            "不要关闭你平时使用的普通 Chrome。"
        )

    def _ensure_started(self, initial_url: str) -> bool:
        if self._debug_is_available() or self._adopt_running_session():
            return False
        if self.process is not None and self.process.poll() is None:
            raise BrowserLaunchError("账号 Chrome 仍在启动，但调试端口暂不可用，请稍后重试。")
        self._launch(initial_url)
        return True

    def _open_target(self, url: str) -> str | None:
        if self._ensure_started(url):
            return None
        request = urllib.request.Request(_new_target_url(self._debug_base, url), method="PUT")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return str(payload.get("id") or "") or None
        except Exception as exc:
            raise BrowserLaunchError("无法在账号 Chrome 中打开新页面，请关闭旧窗口后重试。") from exc

    def _list_pages(self) -> list[dict]:
        if not (self._debug_is_available() or self._adopt_running_session()):
            raise BrowserLaunchError("账号 Chrome 当前不可用，请先打开登录或商品页面。")
        try:
            with urllib.request.urlopen(f"{self._debug_base}/json", timeout=2) as response:
                targets = json.loads(response.read().decode("utf-8"))
            return [item for item in targets if item.get("type") == "page"]
        except Exception as exc:
            raise BrowserLaunchError("无法读取账号 Chrome 的页面列表。") from exc

    def _page_target(self, target_id: str | None = None) -> dict:
        pages = self._list_pages()
        if target_id:
            related_depths = {target_id: 0}
            changed = True
            while changed:
                changed = False
                for item in pages:
                    item_id = str(item.get("id") or "")
                    opener_id = str(item.get("openerId") or "")
                    if item_id and opener_id in related_depths and item_id not in related_depths:
                        related_depths[item_id] = related_depths[opener_id] + 1
                        changed = True
            descendants = [
                item
                for item in pages
                if str(item.get("id") or "") in related_depths and item.get("id") != target_id
            ]
            baseline_ids = self._target_baselines.get(target_id, set())
            new_pages = [
                item
                for item in pages
                if str(item.get("id") or "") not in baseline_ids and item.get("id") != target_id
            ]
            candidate_map = {
                str(item.get("id") or ""): item for item in [*descendants, *new_pages] if item.get("id")
            }
            candidates = list(candidate_map.values())

            def related_priority(item: dict) -> tuple[int, int, int]:
                url = str(item.get("url") or "").lower()
                depth = related_depths.get(str(item.get("id") or ""), 0)
                page_index = pages.index(item)
                if any(
                    token in url
                    for token in ("cashier", "alipay.com", "pay.taobao.com", "excashier", "/pending.html")
                ):
                    return (0, -depth, 0)
                if any(token in url for token in ("sec.", "login.", "verify", "captcha")):
                    return (1, -depth, 0)
                is_confirm = "buy." in url or "confirm_order" in url or "/confirm.html" in url
                return (2, -depth, 0 if is_confirm else page_index + 1)

            if candidates:
                return sorted(candidates, key=related_priority)[0]
            match = next((item for item in pages if item.get("id") == target_id), None)
            if match:
                return match
        candidates = [
            item
            for item in pages
            if any(
                host in str(item.get("url") or "").lower()
                for host in ("taobao.com", "tmall.com", "alipay.com")
            )
        ]
        if candidates:
            def priority(item: dict) -> int:
                url = str(item.get("url") or "").lower()
                if any(token in url for token in ("cashier", "alipay.com")):
                    return 0
                if "buy." in url or "confirm_order" in url:
                    return 1
                if any(token in url for token in ("sec.", "login.", "verify", "captcha")):
                    return 2
                if any(token in url for token in ("item.taobao.com", "detail.tmall.com")):
                    return 3
                return 4

            return sorted(candidates, key=priority)[0]
        if pages:
            return pages[0]
        raise BrowserLaunchError("账号 Chrome 中没有可控制的网页。")

    @staticmethod
    def _runtime_value(result: dict) -> object:
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise BrowserLaunchError(str(remote.get("description") or "页面脚本执行失败"))
        return remote.get("value")

    def _evaluate_target(self, target: dict, expression: str) -> object:
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            raise BrowserLaunchError("当前 Chrome 页面没有可用的控制通道。")
        try:
            result = self.cdp.call(
                websocket_url,
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                    "userGesture": True,
                },
            )
        except CdpError as exc:
            raise BrowserLaunchError(str(exc)) from exc
        return self._runtime_value(result)

    def evaluate(self, expression: str, target_id: str | None = None) -> object:
        return self._evaluate_target(self._page_target(target_id), expression)

    def inspect_page(self, target_id: str | None = None) -> PageSnapshot:
        payload = self.evaluate(PAGE_SNAPSHOT_SCRIPT, target_id)
        if not isinstance(payload, dict):
            raise BrowserLaunchError("无法识别当前淘宝页面状态。")
        return classify_page(payload)

    def click_action(self, labels: tuple[str, ...], target_id: str | None = None) -> dict:
        target = self._page_target(target_id)
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        try:
            with self.cdp.session(websocket_url) as cdp_session:
                runtime_result = cdp_session.call(
                    "Runtime.evaluate",
                    {
                        "expression": build_click_action_script(labels),
                        "returnByValue": True,
                        "awaitPromise": True,
                        "userGesture": True,
                    },
                )
                result = self._runtime_value(runtime_result)
                if not isinstance(result, dict) or not result.get("found"):
                    return {"clicked": False, "text": ""}
                x = float(result["x"])
                y = float(result["y"])
                cdp_session.call(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
                )
                cdp_session.call(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
                )
                try:
                    cdp_session.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
                    )
                except CdpError:
                    # Navigation can close the old target immediately after mouse release.
                    # The release was already sent; let the task runner inspect the new page.
                    pass
        except (CdpError, KeyError, TypeError, ValueError) as exc:
            raise BrowserLaunchError(f"无法向 Chrome 发送鼠标点击：{exc}") from exc
        return {"clicked": True, "text": str(result.get("text") or ""), "method": "cdp_mouse"}

    def open_login(self) -> None:
        self._open_target(LOGIN_URL)

    def check_login(self) -> bool:
        target_id = self._open_target(ACCOUNT_HOME_URL)
        # 首次启动时无法获得新建页 id，按当前所有页面判断。
        deadline = time.monotonic() + 12
        last_urls: list[str] = []
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self._debug_base}/json", timeout=1) as response:
                    targets = json.loads(response.read().decode("utf-8"))
                pages = [item for item in targets if item.get("type") == "page"]
                if target_id:
                    pages = [item for item in pages if item.get("id") == target_id] or pages
                last_urls = [str(item.get("url") or "").lower() for item in pages]
                if any("i.taobao.com" in url for url in last_urls):
                    return True
                if any("login.taobao.com" in url for url in last_urls):
                    return False
            except Exception:
                pass
            time.sleep(0.5)
        return any("i.taobao.com" in url for url in last_urls)

    def open_product(self, url: str) -> str | None:
        normalized_url = normalize_product_url(url)
        target_id = self._open_target(normalized_url)
        if target_id:
            try:
                self._target_baselines[target_id] = {
                    str(page.get("id") or "") for page in self._list_pages() if page.get("id")
                }
            except BrowserLaunchError:
                self._target_baselines[target_id] = {target_id}
            return target_id
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                for page in self._list_pages():
                    page_url = str(page.get("url") or "")
                    if page_url.startswith(("https://item.taobao.com/", "https://detail.tmall.com/")):
                        found_target_id = str(page.get("id") or "") or None
                        if found_target_id:
                            self._target_baselines[found_target_id] = {
                                str(item.get("id") or "") for item in self._list_pages() if item.get("id")
                            }
                        return found_target_id
            except BrowserLaunchError:
                pass
            time.sleep(0.1)
        return None

    def open_cart(self) -> None:
        self._open_target(CART_URL)

    def close(self) -> None:
        # 浏览器由用户控制；退出 GUI 时不强制关闭正在核对的淘宝页面。
        return None


class BrowserSessionManager:
    def __init__(self):
        self._sessions: dict[int, PersistentChromeSession] = {}

    def get_or_create(self, account: dict, driver_path: str = "") -> PersistentChromeSession:
        account_id = int(account["id"])
        session = self._sessions.get(account_id)
        if session is None:
            session = PersistentChromeSession(account["profile_dir"], driver_path)
            self._sessions[account_id] = session
        return session

    def close_all(self) -> None:
        self._sessions.clear()

