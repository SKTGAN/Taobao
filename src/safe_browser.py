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
    build_click_product_option_script,
    build_fill_friend_pay_script,
    build_select_address_script,
    build_set_quantity_script,
    classify_page,
)
from src.product_urls import normalize_product_url, product_id_from_url


LOGIN_URL = "https://login.taobao.com/"
ACCOUNT_HOME_URL = "https://i.taobao.com/my_taobao.htm"
CART_URL = "https://cart.taobao.com/cart.htm"
BOUGHT_ITEMS_URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"
ADDRESS_MANAGER_URL = "https://member1.taobao.com/member/fresh/deliver_address.htm"
AUXILIARY_PAGE_TOKENS = (
    "phone-privacy",
    "privacy-rule",
    "privacy_rule",
    "agreement",
    "rules.htm",
)


class BrowserLaunchError(RuntimeError):
    pass


def _is_auxiliary_page(url: str) -> bool:
    normalized = url.lower()
    return any(token in normalized for token in AUXILIARY_PAGE_TOKENS) and any(
        host in normalized for host in ("taobao.com", "tmall.com")
    )


def _is_task_transition_page(url: str) -> bool:
    """Allow only checkout/security result tabs when opener metadata is missing."""
    normalized = url.lower()
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.hostname or ""
    if host in {"127.0.0.1", "localhost"}:
        return any(
            token in parsed.path
            for token in ("confirm", "pending", "friend", "login", "challenge")
        )
    if not any(host == domain or host.endswith(f".{domain}") for domain in ("taobao.com", "tmall.com", "alipay.com")):
        return False
    return any(
        token in normalized
        for token in (
            "buy.",
            "confirm_order",
            "cashier",
            "pay.taobao.com",
            "excashier",
            "trade_payment",
            "peerpay",
            "shenghuo.alipay.com/send/payment",
            "friend",
            "sec.",
            "login.",
            "verify",
            "captcha",
        )
    )


def _new_target_url(debug_base: str, target_url: str) -> str:
    # Chrome's /json/new endpoint treats a raw '&' as a separator and truncates
    # product URLs. Encode the entire target URL as one opaque query value.
    encoded = urllib.parse.quote(target_url, safe="")
    return f"{debug_base}/json/new?{encoded}"


def find_google_chrome(configured_path: str | Path | None = None) -> Path:
    if configured_path and str(configured_path).strip():
        candidate = Path(str(configured_path).strip()).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        raise BrowserLaunchError(f"配置的 Chrome 路径不存在：{candidate}")

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

    def __init__(self, profile_dir: str | Path, chrome_path: str = ""):
        self.profile_dir = Path(profile_dir).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.chrome_path = find_google_chrome(chrome_path)
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

    def auxiliary_pages(self) -> list[dict[str, str]]:
        """Return open Taobao privacy/agreement/help tabs without interacting with them."""
        return [
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
            }
            for item in self._list_pages()
            if _is_auxiliary_page(str(item.get("url") or ""))
        ]

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
                and _is_task_transition_page(str(item.get("url") or ""))
            ]
            candidate_map = {
                str(item.get("id") or ""): item
                for item in [*descendants, *new_pages]
                if item.get("id") and not _is_auxiliary_page(str(item.get("url") or ""))
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
            raise BrowserLaunchError("任务绑定的 Chrome 标签页已关闭或失效，请重新准备商品。")
        candidates = [
            item
            for item in pages
            if any(
                host in str(item.get("url") or "").lower()
                for host in ("taobao.com", "tmall.com", "alipay.com")
            )
            and not _is_auxiliary_page(str(item.get("url") or ""))
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

    def _click_script(self, expression: str, target_id: str | None = None) -> dict:
        target = self._page_target(target_id)
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        try:
            with self.cdp.session(websocket_url) as cdp_session:
                # Newly opened checkout tabs can remain hidden behind the product
                # tab or the local GUI. Taobao's settlement component expects the
                # target tab to be visible before it accepts the pointer sequence.
                cdp_session.call("Page.bringToFront")
                visibility_deadline = time.monotonic() + 1.0
                while True:
                    visibility_result = cdp_session.call(
                        "Runtime.evaluate",
                        {
                            "expression": "({visible: document.visibilityState === 'visible', focused: document.hasFocus()})",
                            "returnByValue": True,
                        },
                    )
                    visibility = self._runtime_value(visibility_result)
                    if isinstance(visibility, dict) and visibility.get("visible"):
                        break
                    if time.monotonic() >= visibility_deadline:
                        raise BrowserLaunchError("Chrome 目标页面无法切换到前台，请保持账号 Chrome 窗口打开。")
                    time.sleep(0.05)
                runtime_result = cdp_session.call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                        "userGesture": True,
                    },
                )
                result = self._runtime_value(runtime_result)
                if not isinstance(result, dict) or not result.get("found"):
                    return {
                        "clicked": False,
                        "text": str(result.get("text") or "") if isinstance(result, dict) else "",
                        "reason": str(result.get("reason") or "not_found")
                        if isinstance(result, dict)
                        else "not_found",
                        "candidate_count": int(result.get("candidateCount") or 0)
                        if isinstance(result, dict)
                        else 0,
                    }
                x = float(result["x"])
                y = float(result["y"])
                cdp_session.call(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
                )
                time.sleep(0.03)
                cdp_session.call(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
                )
                time.sleep(0.03)
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

    def click_action(self, labels: tuple[str, ...], target_id: str | None = None) -> dict:
        return self._click_script(build_click_action_script(labels), target_id)

    def click_product_option(self, option_text: str, target_id: str | None = None) -> dict:
        return self._click_script(build_click_product_option_script(option_text), target_id)

    def set_product_quantity(self, quantity: int, target_id: str | None = None) -> dict:
        result = self.evaluate(build_set_quantity_script(quantity), target_id)
        if not isinstance(result, dict):
            return {"changed": False, "reason": "invalid_quantity_result"}
        return result

    def select_checkout_address(self, keyword: str, target_id: str | None = None) -> dict:
        return self._click_script(build_select_address_script(keyword), target_id)

    def fill_friend_pay_account(self, account: str, target_id: str | None = None) -> dict:
        result = self.evaluate(build_fill_friend_pay_script(account), target_id)
        if not isinstance(result, dict):
            return {"filled": False, "reason": "invalid_friend_account_result"}
        return result

    def reload_page(self, target_id: str | None = None) -> None:
        """Reload the selected checkout/payment tab without submitting another order."""
        target = self._page_target(target_id)
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            raise BrowserLaunchError("当前支付页面没有可用的控制通道。")
        try:
            with self.cdp.session(websocket_url) as cdp_session:
                cdp_session.call("Page.bringToFront")
                cdp_session.call("Page.reload", {"ignoreCache": True})
        except CdpError as exc:
            raise BrowserLaunchError(f"支付页面重新加载失败：{exc}") from exc

    def open_bought_items(self) -> None:
        """Open Taobao orders as a safe fallback; never selects or pays an order."""
        self._open_target(BOUGHT_ITEMS_URL)

    def open_address_manager(self) -> None:
        """Open Taobao's official address manager; address entry stays manual."""
        self._open_target(ADDRESS_MANAGER_URL)

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
        existing_ids: set[str] = set()
        try:
            if self._debug_is_available() or self._adopt_running_session():
                existing_ids = {
                    str(page.get("id") or "") for page in self._list_pages() if page.get("id")
                }
        except BrowserLaunchError:
            existing_ids = set()
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
        expected_product_id = product_id_from_url(normalized_url)
        expected_parts = urllib.parse.urlparse(normalized_url)
        while time.monotonic() < deadline:
            try:
                pages = self._list_pages()
                product_pages = []
                direct_short_pages = []
                for page in pages:
                    page_url = str(page.get("url") or "")
                    if page_url.startswith(("https://item.taobao.com/", "https://detail.tmall.com/")):
                        if not expected_product_id or product_id_from_url(page_url) == expected_product_id:
                            product_pages.append(page)
                    parsed_page = urllib.parse.urlparse(page_url)
                    if (
                        not expected_product_id
                        and parsed_page.hostname == expected_parts.hostname
                        and parsed_page.path == expected_parts.path
                    ):
                        direct_short_pages.append(page)

                candidates = product_pages if expected_product_id else direct_short_pages
                if not candidates and not expected_product_id:
                    candidates = [
                        page
                        for page in product_pages
                        if str(page.get("id") or "") not in existing_ids
                    ]
                if not candidates and not expected_product_id and len(product_pages) == 1:
                    candidates = product_pages
                candidate_ids = {str(page.get("id") or "") for page in candidates if page.get("id")}
                if len(candidate_ids) > 1:
                    raise BrowserLaunchError("发现多个可能的商品标签页，无法安全绑定；请关闭无关商品页后重试。")
                if len(candidate_ids) == 1:
                    found_target_id = next(iter(candidate_ids))
                    self._target_baselines[found_target_id] = {
                        str(item.get("id") or "") for item in pages if item.get("id")
                    }
                    return found_target_id
            except BrowserLaunchError as exc:
                if "多个可能的商品标签页" in str(exc):
                    raise
            time.sleep(0.1)
        raise BrowserLaunchError("未找到与当前商品链接匹配的 Chrome 标签页，请重新准备商品。")

    def navigate_product(self, url: str, target_id: str | None = None) -> str | None:
        """Navigate the prepared product tab to an exact product/SKU URL."""
        normalized_url = normalize_product_url(url)
        if not target_id:
            return self.open_product(normalized_url)
        target = next(
            (item for item in self._list_pages() if str(item.get("id") or "") == target_id),
            None,
        )
        if not target or not target.get("webSocketDebuggerUrl"):
            return self.open_product(normalized_url)
        try:
            with self.cdp.session(str(target["webSocketDebuggerUrl"])) as cdp_session:
                cdp_session.call("Page.bringToFront")
                cdp_session.call("Page.navigate", {"url": normalized_url})
        except CdpError as exc:
            raise BrowserLaunchError(f"无法打开本次选择的商品款式：{exc}") from exc
        return target_id

    def open_cart(self) -> None:
        self._open_target(CART_URL)

    def close(self) -> None:
        # 浏览器由用户控制；退出 GUI 时不强制关闭正在核对的淘宝页面。
        return None


class BrowserSessionManager:
    def __init__(self, chrome_path: str = ""):
        self.chrome_path = chrome_path
        self._sessions: dict[int, PersistentChromeSession] = {}

    def get_or_create(self, account: dict, driver_path: str = "") -> PersistentChromeSession:
        account_id = int(account["id"])
        session = self._sessions.get(account_id)
        if session is None:
            session = PersistentChromeSession(account["profile_dir"], driver_path or self.chrome_path)
            self._sessions[account_id] = session
        return session

    def close_all(self) -> None:
        self._sessions.clear()

    def forget(self, account_id: int) -> None:
        """Forget an account session without closing the user-controlled Chrome."""
        self._sessions.pop(int(account_id), None)

