from __future__ import annotations

import unittest

from src.product_urls import (
    normalize_product_url,
    product_id_from_url,
    resolve_product_selection,
    sku_id_from_url,
    with_sku_id,
)
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
            "https://detail.tmall.com/item.htm?id=123456&skuId=987",
        )

    def test_keeps_only_product_and_sku_from_real_style_url(self) -> None:
        url = (
            "https://item.taobao.com/item.htm?id=1042716758379"
            "&mi_id=tracking&skuId=6064684260474&spm=a21bo.test"
        )
        normalized = normalize_product_url(url)
        self.assertEqual(
            normalized,
            "https://item.taobao.com/item.htm?id=1042716758379&skuId=6064684260474",
        )
        self.assertEqual(product_id_from_url(normalized), "1042716758379")
        self.assertEqual(sku_id_from_url(normalized), "6064684260474")

    def test_resolves_sku_id_or_same_product_url(self) -> None:
        base = "https://item.taobao.com/item.htm?id=123"
        self.assertEqual(
            with_sku_id(base, "456"),
            "https://item.taobao.com/item.htm?id=123&skuId=456",
        )
        self.assertEqual(
            resolve_product_selection(base, "https://item.taobao.com/item.htm?id=123&skuId=789&spm=x"),
            "https://item.taobao.com/item.htm?id=123&skuId=789",
        )

    def test_rejects_style_url_for_another_product(self) -> None:
        with self.assertRaisesRegex(ValueError, "当前商品"):
            resolve_product_selection(
                "https://item.taobao.com/item.htm?id=123",
                "https://item.taobao.com/item.htm?id=999&skuId=789",
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
