from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from src.paths import DATA_DIR


DEFAULT_PORT = 8550


@dataclass(frozen=True)
class AppConfig:
    port: int = DEFAULT_PORT
    chrome_path: str = ""
    first_run_complete: bool = False


def validate_port(value: object) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("服务端口必须是数字") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("服务端口必须在 1024-65535 之间")
    return port


def config_path(data_dir: str | Path = DATA_DIR) -> Path:
    return Path(data_dir) / "config.json"


def load_config(data_dir: str | Path = DATA_DIR) -> AppConfig:
    path = config_path(data_dir)
    payload: dict[str, object] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except (OSError, ValueError, TypeError):
        payload = {}

    port_value: object = os.environ.get("TAOBAO_ASSISTANT_PORT") or payload.get("port", DEFAULT_PORT)
    try:
        port = validate_port(port_value)
    except ValueError:
        port = DEFAULT_PORT
    chrome_path = os.environ.get("TAOBAO_ASSISTANT_CHROME", "").strip() or str(
        payload.get("chrome_path") or ""
    ).strip()
    return AppConfig(
        port=port,
        chrome_path=chrome_path,
        first_run_complete=bool(payload.get("first_run_complete", False)),
    )


def save_config(config: AppConfig, data_dir: str | Path = DATA_DIR) -> Path:
    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
