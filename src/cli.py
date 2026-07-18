from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

import flet as ft

from src.app_config import load_config
from src.gui_v2 import FletGUI
from src.paths import prepare_data_dir


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="淘宝安全辅助购买工具 V2")
    parser.add_argument("--host", default="127.0.0.1", help="GUI 监听地址")
    parser.add_argument("--port", type=int, default=None, help="GUI 监听端口（默认读取本地配置）")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动服务但不自动打开默认浏览器",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = prepare_data_dir()
    config = load_config(data_dir)
    port = args.port if args.port is not None else config.port
    if args.no_browser:
        # Python webbrowser 会读取该变量；服务仍正常运行。
        os.environ["BROWSER"] = "none"
    ft.app(
        target=lambda page: FletGUI(data_dir=data_dir, config=config).build(page),
        host=args.host,
        port=port,
        view=ft.AppView.WEB_BROWSER,
    )
    return 0

