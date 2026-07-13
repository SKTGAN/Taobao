from __future__ import annotations

import unittest

from src.product_urls import normalize_product_url
from src.safe_browser import _new_target_url


class ProductUrlTests(unittest.TestCase):
    def test_normalizes_taobao_url_with_tracking_before_id(self) -> None:
        url = (
            "https://item.taobao.com/item.htm?abbucket=11&id=987250846319"
            "&spm=a21n57.1.hoverItem.11"
        )
        self.assertEqual(
            normalize_product_url(url),
            "https://item.taobao.com/item.htm?id=987250846319",
        )

    def test_normalizes_tmall_url(self) -> None:
        url = "https://detail.tmall.com/item.htm?spm=test&id=123456&skuId=987"
        self.assertEqual(
            normalize_product_url(url),
            "https://detail.tmall.com/item.htm?id=123456",
        )

    def test_keeps_supported_short_link(self) -> None:
        url = "https://m.tb.cn/h.test-token?tk=abc#fragment"
        self.assertEqual(
            normalize_product_url(url),
            "https://m.tb.cn/h.test-token?tk=abc",
        )

    def test_rejects_product_url_without_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "商品 ID"):
            normalize_product_url("https://item.taobao.com/item.htm?abbucket=11")

    def test_new_target_url_encodes_ampersands(self) -> None:
        target = "https://item.taobao.com/item.htm?abbucket=11&id=123"
        request_url = _new_target_url("http://127.0.0.1:9222", target)
        self.assertNotIn("&", request_url)
        self.assertIn("%26id%3D123", request_url)


if __name__ == "__main__":
    unittest.main()
