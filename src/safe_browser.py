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


LOGIN_URL = "https://login.taobao.com/"
ACCOUNT_HOME_URL = "https://i.taobao.com/my_taobao.htm"
CART_URL = "https://cart.taobao.com/cart.htm"


class BrowserLaunchError(RuntimeError):
    pass


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


class PersistentChromeSession:
    """直接启动可见 Google Chrome；不依赖 ChromeDriver，不修改浏览器指纹。"""

    def __init__(self, profile_dir: str | Path, _driver_path: str = ""):
        self.profile_dir = Path(profile_dir).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.chrome_path = find_google_chrome()
        self.debug_port = _free_local_port()
        self.process: subprocess.Popen | None = None

    @property
    def _debug_base(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}"

    def _launch(self, url: str) -> None:
        args = [
            str(self.chrome_path),
            f"--user-data-dir={self.profile_dir}",
            "--profile-directory=Default",
            f"--remote-debugging-port={self.debug_port}",
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
            try:
                with urllib.request.urlopen(f"{self._debug_base}/json/version", timeout=0.5):
                    return
            except Exception:
                if self.process.poll() is not None:
                    break
                time.sleep(0.2)
        raise BrowserLaunchError("Chrome 已启动但本地状态端口不可用，请关闭该账号的旧 Chrome 窗口后重试。")

    def _ensure_started(self, initial_url: str) -> bool:
        if self.process is not None and self.process.poll() is None:
            return False
        self._launch(initial_url)
        return True

    def _open_target(self, url: str) -> str | None:
        if self._ensure_started(url):
            return None
        encoded = urllib.parse.quote(url, safe=":/?=&")
        request = urllib.request.Request(f"{self._debug_base}/json/new?{encoded}", method="PUT")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return str(payload.get("id") or "") or None
        except Exception as exc:
            raise BrowserLaunchError("无法在账号 Chrome 中打开新页面，请关闭旧窗口后重试。") from exc

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

    def open_product(self, url: str) -> None:
        self._open_target(url)

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

