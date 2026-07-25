from __future__ import annotations

import unittest
from datetime import datetime, timezone
from email.utils import format_datetime

from src.taobao_time import TaobaoClock


class _Response:
    def __init__(self, date_value: str) -> None:
        self.headers = {"Date": date_value}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TaobaoTimeTests(unittest.TestCase):
    def test_syncs_from_https_date_header_and_becomes_fresh(self) -> None:
        date_value = format_datetime(datetime.now(timezone.utc), usegmt=True)

        def opener(_request, timeout):
            self.assertGreater(timeout, 0)
            return _Response(date_value)

        clock = TaobaoClock()
        status = clock.sync(samples=3, opener=opener)
        self.assertTrue(status.synchronized)
        self.assertTrue(clock.is_fresh())
        self.assertEqual(status.source, "淘宝 HTTPS Date（估算）")
        self.assertLess(abs(status.offset_ms), 1500)

    def test_missing_date_header_keeps_local_fallback(self) -> None:
        def opener(_request, timeout):
            return _Response("")

        clock = TaobaoClock()
        status = clock.sync(samples=1, opener=opener)
        self.assertFalse(status.synchronized)
        self.assertIn("Date", status.error)


if __name__ == "__main__":
    unittest.main()
