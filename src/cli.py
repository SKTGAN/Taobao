from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import flet as ft

from src.gui_v2 import FletGUI


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="淘宝安全辅助购买工具 V2")
    parser.add_argument("--host", default="127.0.0.1", help="GUI 监听地址")
    parser.add_argument("--port", type=int, default=8550, help="GUI 监听端口")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动服务但不自动打开默认浏览器",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.no_browser:
        # Python webbrowser 会读取该变量；服务仍正常运行。
        os.environ["BROWSER"] = "none"
    ft.app(
        target=lambda page: FletGUI().build(page),
        host=args.host,
        port=args.port,
        view=ft.AppView.WEB_BROWSER,
    )
    return 0

