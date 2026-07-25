from __future__ import annotations

import statistics
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime


TAOBAO_TIME_URL = "https://www.taobao.com/"


@dataclass(frozen=True)
class TaobaoClockStatus:
    synchronized: bool
    offset_ms: int
    round_trip_ms: int
    synchronized_at: str
    source: str
    error: str = ""


class TaobaoClock:
    """Estimate Taobao-facing time from the official HTTPS Date response header.

    HTTP Date has one-second resolution, so this is an offset estimate rather
    than a claim of access to an internal millisecond clock. Scheduling still
    uses a monotonic deadline after converting with the latest estimate.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offset_seconds = 0.0
        self._round_trip_ms = 0
        self._synchronized_monotonic = 0.0
        self._synchronized_at = ""
        self._error = "尚未同步，将使用本机时间"

    def now(self) -> datetime:
        with self._lock:
            offset = self._offset_seconds
        return datetime.fromtimestamp(time.time() + offset)

    def is_fresh(self, max_age_seconds: float = 600.0) -> bool:
        with self._lock:
            synchronized = self._synchronized_monotonic
        return bool(synchronized and time.monotonic() - synchronized <= max_age_seconds)

    def status(self) -> TaobaoClockStatus:
        with self._lock:
            synchronized = bool(self._synchronized_monotonic)
            return TaobaoClockStatus(
                synchronized=synchronized,
                offset_ms=round(self._offset_seconds * 1000),
                round_trip_ms=self._round_trip_ms,
                synchronized_at=self._synchronized_at,
                source="淘宝 HTTPS Date（估算）" if synchronized else "本机时间",
                error=self._error,
            )

    @staticmethod
    def _request_date(opener, timeout: float) -> tuple[float, float]:
        request = urllib.request.Request(
            TAOBAO_TIME_URL,
            method="HEAD",
            headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "TaobaoAssistantTimeSync/1.0",
            },
        )
        started_wall = time.time()
        started_mono = time.monotonic()
        try:
            response = opener(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in {400, 403, 405}:
                raise
            request = urllib.request.Request(
                TAOBAO_TIME_URL,
                method="GET",
                headers={
                    "Cache-Control": "no-cache",
                    "Range": "bytes=0-0",
                    "User-Agent": "TaobaoAssistantTimeSync/1.0",
                },
            )
            started_wall = time.time()
            started_mono = time.monotonic()
            response = opener(request, timeout=timeout)
        with response:
            date_value = response.headers.get("Date", "")
        ended_mono = time.monotonic()
        ended_wall = time.time()
        if not date_value:
            raise RuntimeError("淘宝响应没有 Date 时间头")
        server_time = parsedate_to_datetime(date_value)
        if server_time.tzinfo is None:
            raise RuntimeError("淘宝 Date 时间头缺少时区")
        midpoint = (started_wall + ended_wall) / 2.0
        offset = server_time.timestamp() - midpoint
        round_trip = max(0.0, ended_mono - started_mono)
        return offset, round_trip

    def sync(
        self,
        *,
        samples: int = 5,
        timeout: float = 3.0,
        opener=urllib.request.urlopen,
    ) -> TaobaoClockStatus:
        measurements: list[tuple[float, float]] = []
        errors: list[str] = []
        for _ in range(max(1, min(7, int(samples)))):
            try:
                measurements.append(self._request_date(opener, timeout))
            except Exception as exc:
                errors.append(str(exc))
        if not measurements:
            message = errors[-1] if errors else "淘宝时间同步失败"
            with self._lock:
                self._error = message
            return self.status()

        # Prefer the lowest-RTT samples and take a median to reduce one slow
        # response from moving the deadline.
        best = sorted(measurements, key=lambda item: item[1])[: min(3, len(measurements))]
        offset = statistics.median(item[0] for item in best)
        round_trip_ms = round(min(item[1] for item in best) * 1000)
        with self._lock:
            self._offset_seconds = offset
            self._round_trip_ms = round_trip_ms
            self._synchronized_monotonic = time.monotonic()
            self._synchronized_at = datetime.now().isoformat(timespec="seconds")
            self._error = ""
        return self.status()
