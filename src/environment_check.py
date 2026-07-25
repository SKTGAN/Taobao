from __future__ import annotations

import os
import platform
import socket
import ssl
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.app_config import AppConfig
from src.safe_browser import BrowserLaunchError, find_google_chrome


@dataclass(frozen=True)
class EnvironmentCheck:
    name: str
    status: str
    message: str

    @property
    def passed(self) -> bool:
        return self.status == "通过"


PROXY_ENVIRONMENT_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def detect_proxy_sources(
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> tuple[str, ...]:
    """Detect configured proxy sources without reading or exposing credentials."""
    values = os.environ if environ is None else environ
    sources = [name for name in PROXY_ENVIRONMENT_VARIABLES if str(values.get(name) or "").strip()]
    if (system_name or platform.system()) == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                try:
                    proxy_enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0]) == 1
                except OSError:
                    proxy_enabled = False
                try:
                    proxy_server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
                except OSError:
                    proxy_server = ""
                try:
                    auto_config = str(winreg.QueryValueEx(key, "AutoConfigURL")[0] or "").strip()
                except OSError:
                    auto_config = ""
                if proxy_enabled and proxy_server:
                    sources.append("Windows 系统代理")
                if auto_config:
                    sources.append("Windows 自动配置脚本")
        except (ImportError, OSError, ValueError):
            pass
    return tuple(dict.fromkeys(sources))


def proxy_environment_check() -> EnvironmentCheck:
    sources = detect_proxy_sources()
    if sources:
        return EnvironmentCheck(
            "代理/VPN提醒",
            "警告",
            "检测到代理配置来源："
            + "、".join(sources)
            + "。真实淘宝测试前请关闭代理/VPN并重新运行自检；TUN/隧道型VPN可能无法自动识别。",
        )
    return EnvironmentCheck(
        "代理/VPN提醒",
        "通过",
        "未检测到常见系统或环境变量代理。TUN/隧道型VPN仍需由用户自行确认已关闭。",
    )


def _https_check(name: str, hostname: str, timeout: float = 5.0) -> EnvironmentCheck:
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=timeout) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=hostname) as tls_socket:
                protocol = tls_socket.version() or "TLS"
        return EnvironmentCheck(name, "通过", f"{hostname}:443 可连接（{protocol}）")
    except OSError as exc:
        return EnvironmentCheck(
            name,
            "失败",
            f"{hostname}:443 无法连接：{exc}。请检查代理、VPN、TUN、防火墙或 DNS。",
        )


def run_environment_checks(config: AppConfig, data_dir: Path, include_network: bool = True) -> list[EnvironmentCheck]:
    results: list[EnvironmentCheck] = []
    if platform.system() == "Windows":
        results.append(EnvironmentCheck("操作系统", "通过", f"Windows {platform.release()}"))
    else:
        results.append(EnvironmentCheck("操作系统", "失败", "当前交付版仅支持 Windows 10/11"))

    version = sys.version_info
    if version >= (3, 10):
        results.append(EnvironmentCheck("Python", "通过", f"Python {version.major}.{version.minor}.{version.micro}"))
    else:
        results.append(EnvironmentCheck("Python", "失败", "需要 Python 3.10 或更高版本"))

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".write-check-", dir=data_dir, delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
        writable = os.access(data_dir, os.W_OK)
    except OSError:
        writable = False
    results.append(
        EnvironmentCheck(
            "数据目录",
            "通过" if writable else "失败",
            str(data_dir) if writable else f"目录不可写：{data_dir}",
        )
    )

    try:
        chrome = find_google_chrome(config.chrome_path)
        results.append(EnvironmentCheck("Google Chrome", "通过", str(chrome)))
    except BrowserLaunchError as exc:
        results.append(EnvironmentCheck("Google Chrome", "失败", str(exc)))

    if 1024 <= config.port <= 65535:
        results.append(EnvironmentCheck("服务端口", "通过", f"已配置端口 {config.port}；冲突时启动脚本会自动换用空闲端口"))
    else:
        results.append(EnvironmentCheck("服务端口", "失败", "端口必须在 1024-65535 之间"))

    results.append(proxy_environment_check())

    if include_network:
        results.extend(
            [
                _https_check("淘宝网络", "www.taobao.com"),
                _https_check("支付宝网络", "tbapi.alipay.com"),
            ]
        )
    return results
