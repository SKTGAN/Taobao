from __future__ import annotations

import unittest

from src.page_automation import (
    build_click_product_option_script,
    build_fill_friend_pay_script,
    build_select_address_script,
    build_set_quantity_script,
    build_click_action_script,
    classify_page,
    enabled_action_count,
    has_enabled_action,
    has_unique_enabled_action,
    parse_spec_terms,
    product_precheck_finished,
)


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

    def test_classifies_tbapi_trade_payment_by_url(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://tbapi.alipay.com/trade/trade_payment.htm?sign=redacted",
                "title": "",
                "readyState": "loading",
                "bodyText": "",
                "controls": [],
            }
        )
        self.assertEqual(snapshot.kind, "pending_payment")

    def test_classifies_phone_privacy_rules_as_auxiliary(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://huodong.taobao.com/wow/z/mt/default/phone-privacy-1-0",
                "title": "隐私号保护规则说明",
                "readyState": "complete",
                "bodyText": "服务介绍",
                "controls": [],
            }
        )
        self.assertEqual(snapshot.kind, "auxiliary")

    def test_submit_script_never_clicks_the_whole_checkout_region(self) -> None:
        script = build_click_action_script(("提交订单",))
        self.assertNotIn("    '#submitOrder',", script)
        self.assertIn("unsafe_submit_hit_target", script)
        self.assertIn("ambiguous_action", script)

    def test_multiple_enabled_actions_are_not_unique(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://item.taobao.com/item.htm?id=1",
                "title": "测试商品",
                "readyState": "complete",
                "bodyText": "库存充足",
                "controls": [
                    {"text": "立即购买", "disabled": False},
                    {"text": "立即购买", "disabled": False},
                ],
            }
        )
        self.assertEqual(enabled_action_count(snapshot, ("立即购买",)), 2)
        self.assertTrue(has_enabled_action(snapshot, ("立即购买",)))
        self.assertFalse(has_unique_enabled_action(snapshot, ("立即购买",)))

    def test_exact_buy_button_ignores_combined_action_bar_wrapper(self) -> None:
        snapshot = classify_page(
            {
                "url": "https://item.taobao.com/item.htm?id=1",
                "title": "测试商品",
                "readyState": "complete",
                "bodyText": "库存充足",
                "controls": [
                    {"text": "加入购物车立即购买", "disabled": False},
                    {"text": "立即购买", "disabled": False},
                ],
            }
        )
        self.assertEqual(enabled_action_count(snapshot, ("立即购买",)), 1)
        self.assertTrue(has_unique_enabled_action(snapshot, ("立即购买",)))

    def test_classifies_alipay_chrome_network_error(self) -> None:
        snapshot = classify_page(
            {
                "url": "chrome-error://chromewebdata/",
                "title": "tbapi.alipay.com",
                "readyState": "complete",
                "bodyText": "无法访问此网站 tbapi.alipay.com 意外终止了连接 ERR_CONNECTION_CLOSED",
                "controls": [],
            }
        )
        self.assertEqual(snapshot.kind, "payment_error")

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

    def test_parses_multiple_exact_specification_terms(self) -> None:
        self.assertEqual(
            parse_spec_terms(" 黑色 | 256GB； 官方标配\n黑色 "),
            ("黑色", "256GB", "官方标配"),
        )

    def test_builds_scoped_configuration_scripts(self) -> None:
        self.assertIn("ambiguous_option", build_click_product_option_script("黑色"))
        self.assertIn("quantity_input_not_found", build_set_quantity_script(3))
        self.assertIn("ambiguous_address", build_select_address_script("张三 9364"))
        friend_script = build_fill_friend_pay_script("13800138000")
        self.assertIn("friend_account_input_not_found", friend_script)
        self.assertIn("13800138000", friend_script)

    def test_classifies_friend_pay_request_and_success(self) -> None:
        request_page = classify_page(
            {
                "url": "https://shenghuo.alipay.com/send/payment/fill.htm",
                "title": "申请代付",
                "bodyText": "好友的账户 支付宝账户 请他付款",
                "controls": [{"text": "请他付款", "disabled": False}],
            }
        )
        sent_page = classify_page(
            {
                "url": "https://shenghuo.alipay.com/send/payment/result.htm",
                "title": "申请代付",
                "bodyText": "代付申请已提交 已通知好友付款",
                "controls": [],
            }
        )
        self.assertEqual(request_page.kind, "friend_pay_request")
        self.assertEqual(sent_page.kind, "friend_pay_sent")


if __name__ == "__main__":
    unittest.main()
