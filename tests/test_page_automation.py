from __future__ import annotations

import unittest

from src.page_automation import classify_page, has_enabled_action, product_precheck_finished


class PageAutomationTests(unittest.TestCase):
    def test_classifies_product_with_enabled_buy_action(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://item.taobao.com/item.htm?id=1",
                "title": "测试商品",
                "readyState": "complete",
                "bodyText": "库存充足",
                "controls": [{"text": "立即购买", "disabled": False}],
            }
        )
        self.assertEqual(snapshot.kind, "product")
        self.assertTrue(has_enabled_action(snapshot, ("立即购买",)))

    def test_challenge_takes_priority_over_product_domain(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://item.taobao.com/item.htm?id=1",
                "title": "安全验证",
                "readyState": "complete",
                "bodyText": "请拖动滑块完成验证码",
                "controls": [],
            }
        )
        self.assertEqual(snapshot.kind, "challenge")

    def test_pending_payment_text_in_product_navigation_is_not_a_success(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://item.taobao.com/item.htm?id=1",
                "title": "测试商品",
                "readyState": "complete",
                "bodyText": "我的订单 待付款 立即购买",
                "controls": [{"text": "立即购买", "disabled": False}],
            }
        )
        self.assertEqual(snapshot.kind, "product")

    def test_precheck_ignores_interim_about_blank(self) -> None:
        blank = classify_page(
            {
                "url": "about:blank",
                "title": "",
                "readyState": "complete",
                "bodyText": "",
                "controls": [],
            }
        )
        product = classify_page(
            {
                "url": "https://item.taobao.com/item.htm?id=1",
                "title": "测试商品",
                "readyState": "complete",
                "bodyText": "有货",
                "controls": [{"text": "立即购买", "disabled": False}],
            }
        )
        self.assertFalse(product_precheck_finished(blank, ("立即购买",)))
        self.assertTrue(product_precheck_finished(product, ("立即购买",)))

    def test_classifies_confirm_and_pending_payment_pages(self) -> None:
        confirm = classify_page(
            {
                "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
                "title": "确认订单",
                "readyState": "complete",
                "bodyText": "提交订单",
                "controls": [{"text": "提交订单", "disabled": False}],
            }
        )
        pending = classify_page(
            {
                "url": "https://cashier.alipay.com/standard/payment/cashier.htm",
                "title": "收银台",
                "readyState": "complete",
                "bodyText": "订单待付款",
                "controls": [],
            }
        )
        self.assertEqual(confirm.kind, "confirm_order")
        self.assertEqual(pending.kind, "pending_payment")

    def test_classifies_alipay_checkout_without_literal_pending_text(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://render.alipay.com/p/cashier/index.html",
                "title": "支付宝收银台",
                "readyState": "complete",
                "bodyText": "请确认订单后立即付款",
                "controls": [],
            }
        )
        self.assertEqual(snapshot.kind, "pending_payment")

    def test_local_mock_marker_is_limited_to_localhost(self) -> None:
        local = classify_page(
            {
                "url": "http://127.0.0.1:8000/product.html",
                "testMarker": "product",
                "controls": [{"text": "立即购买", "disabled": False}],
            }
        )
        remote = classify_page(
            {
                "url": "https://example.com/product.html",
                "testMarker": "pending_payment",
                "controls": [],
            }
        )
        self.assertEqual(local.kind, "product")
        self.assertEqual(remote.kind, "unknown")


if __name__ == "__main__":
    unittest.main()
