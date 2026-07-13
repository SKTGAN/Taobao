from __future__ import annotations

import itertools
import json
from contextlib import contextmanager
from typing import Any

from websockets.sync.client import connect


class CdpError(RuntimeError):
    pass


class CdpSession:
    def __init__(self, connection, timeout: float, ids):
        self.connection = connection
        self.timeout = timeout
        self._ids = ids

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        command_id = next(self._ids)
        payload = {"id": command_id, "method": method, "params": params or {}}
        try:
            self.connection.send(json.dumps(payload, ensure_ascii=False))
            while True:
                response = json.loads(self.connection.recv(timeout=self.timeout))
                if response.get("id") != command_id:
                    continue
                if "error" in response:
                    error = response["error"]
                    raise CdpError(str(error.get("message") or error))
                return dict(response.get("result") or {})
        except CdpError:
            raise
        except Exception as exc:
            raise CdpError(f"无法连接 Chrome 页面控制通道：{exc}") from exc


class CdpClient:
    """Small synchronous Chrome DevTools Protocol client for local tabs."""

    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout
        self._ids = itertools.count(1)

    @contextmanager
    def session(self, websocket_url: str):
        try:
            with connect(
                websocket_url,
                origin="http://127.0.0.1",
                open_timeout=self.timeout,
                close_timeout=1,
            ) as connection:
                yield CdpSession(connection, self.timeout, self._ids)
        except CdpError:
            raise
        except Exception as exc:
            raise CdpError(f"无法连接 Chrome 页面控制通道：{exc}") from exc

    def call(self, websocket_url: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.session(websocket_url) as session:
            return session.call(method, params)
