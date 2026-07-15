from __future__ import annotations

import os
import platform
import socket
import ssl
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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

    if include_network:
        results.extend(
            [
                _https_check("淘宝网络", "www.taobao.com"),
                _https_check("支付宝网络", "tbapi.alipay.com"),
            ]
        )
    return results
