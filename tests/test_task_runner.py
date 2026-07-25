from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.page_automation import classify_page
from src.task_runner import (
    BUY_READY_TIMEOUT_SECONDS,
    SUBMIT_READY_TIMEOUT_SECONDS,
    SingleAccountTaskRunner,
    parse_scheduled_at,
    safe_page_location,
)
from src.v2_store import TASK_MODE_FLASH, V2Store


def snapshot(kind: str):
    fixtures = {
        "product": {
            "url": "https://item.taobao.com/item.htm?id=1",
            "title": "商品",
            "readyState": "complete",
            "bodyText": "库存充足",
            "controls": [{"text": "立即购买", "disabled": False}],
        },
        "product_disabled": {
            "url": "https://item.taobao.com/item.htm?id=1",
            "title": "商品",
            "readyState": "complete",
            "bodyText": "活动尚未开始",
            "controls": [{"text": "立即购买", "disabled": True}],
        },
        "confirm_order": {
            "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
            "title": "确认订单",
            "readyState": "complete",
            "bodyText": "提交订单",
            "controls": [{"text": "提交订单", "disabled": False}],
        },
        "confirm_friend_pay": {
            "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
            "title": "确认订单",
            "readyState": "complete",
            "bodyText": "朋友代付 找朋友帮忙付",
            "controls": [{"text": "找朋友帮忙付", "disabled": False}],
        },
        "friend_pay_request": {
            "url": "https://shenghuo.alipay.com/send/payment/fill.htm",
            "title": "申请代付",
            "readyState": "complete",
            "bodyText": "好友的账户 支付宝账户 请他付款",
            "controls": [{"text": "请他付款", "disabled": False}],
        },
        "friend_pay_sent": {
            "url": "https://shenghuo.alipay.com/send/payment/result.htm",
            "title": "申请代付",
            "readyState": "complete",
            "bodyText": "代付申请已提交 已通知好友付款",
            "controls": [],
        },
        "pending_payment": {
            "url": "https://cashier.alipay.com/standard/payment/cashier.htm",
            "title": "收银台",
            "readyState": "complete",
            "bodyText": "订单待付款",
            "controls": [],
        },
        "payment_error": {
            "url": "chrome-error://chromewebdata/",
            "title": "tbapi.alipay.com",
            "readyState": "complete",
            "bodyText": "无法访问此网站 tbapi.alipay.com 意外终止了连接 ERR_CONNECTION_CLOSED",
            "controls": [],
        },
    }
    return classify_page(fixtures[kind])


class FakeSession:
    def __init__(self):
        self.state = "product"
        self.buy_clicks = 0
        self.submit_clicks = 0

    def inspect_page(self, _target_id=None):
        return snapshot(self.state)

    def click_action(self, labels, _target_id=None):
        if "立即购买" in labels and self.state == "product":
            self.buy_clicks += 1
            self.state = "confirm_order"
            return {"clicked": True, "text": "立即购买"}
        if "提交订单" in labels and self.state == "confirm_order":
            self.submit_clicks += 1
            self.state = "pending_payment"
            return {"clicked": True, "text": "提交订单"}
        return {"clicked": False, "text": ""}


class PreparedConfirmSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.state = "confirm_order"


class AuxiliaryPageSession(PreparedConfirmSession):
    def auxiliary_pages(self):
        return [
            {
                "id": "privacy",
                "title": "隐私号保护规则说明",
                "url": "https://huodong.taobao.com/wow/z/mt/default/phone-privacy-1-0",
            }
        ]


class MultipleSubmitSession(PreparedConfirmSession):
    def inspect_page(self, _target_id=None):
        return classify_page(
            {
                "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
                "title": "确认订单",
                "readyState": "complete",
                "bodyText": "提交订单",
                "controls": [
                    {"text": "提交订单", "disabled": False},
                    {"text": "提交订单", "disabled": False},
                ],
            }
        )


class SlowBuyNavigationSession(FakeSession):
    def click_action(self, labels, _target_id=None):
        if "立即购买" in labels and self.state == "product":
            self.buy_clicks += 1
            return {"clicked": True, "text": "立即购买"}
        return super().click_action(labels, _target_id)


class CancelBeforeSubmitSession(PreparedConfirmSession):
    def __init__(self, cancel_event: threading.Event):
        super().__init__()
        self.cancel_event = cancel_event
        self.inspect_count = 0

    def inspect_page(self, _target_id=None):
        self.inspect_count += 1
        if self.inspect_count >= 3:
            self.cancel_event.set()
        return super().inspect_page(_target_id)


class DelayedSubmitSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.pending_after_inspections = 0

    def inspect_page(self, _target_id=None):
        if self.pending_after_inspections:
            self.pending_after_inspections -= 1
            if self.pending_after_inspections == 0:
                self.state = "pending_payment"
        return snapshot(self.state)

    def click_action(self, labels, _target_id=None):
        if "提交订单" in labels and self.state == "confirm_order":
            self.pending_after_inspections = 3
            return {"clicked": True, "text": "提交订单"}
        return super().click_action(labels, _target_id)


class DelayedSubmitButtonSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.disabled_inspections = 0

    def inspect_page(self, _target_id=None):
        if self.state == "confirm_order" and self.disabled_inspections > 0:
            self.disabled_inspections -= 1
            return classify_page(
                {
                    "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
                    "title": "确认订单",
                    "readyState": "complete",
                    "bodyText": "地址和价格加载中 提交订单",
                    "controls": [{"text": "提交订单", "disabled": True}],
                }
            )
        return super().inspect_page(_target_id)

    def click_action(self, labels, _target_id=None):
        result = super().click_action(labels, _target_id)
        if "立即购买" in labels and result.get("clicked"):
            self.disabled_inspections = 3
        return result


class DelayedFlashBuyButtonSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.disabled_inspections = 4

    def inspect_page(self, _target_id=None):
        if self.state == "product" and self.disabled_inspections > 0:
            self.disabled_inspections -= 1
            return snapshot("product_disabled")
        return super().inspect_page(_target_id)


class PasswordlessPaymentSession(PreparedConfirmSession):
    def inspect_page(self, _target_id=None):
        return classify_page(
            {
                "url": "https://buy.taobao.com/auction/buy_now.jhtml?x-itemid=1",
                "title": "确认订单",
                "readyState": "complete",
                "bodyText": "支付宝免密支付",
                "controls": [{"text": "免密支付 ¥0.98", "disabled": False}],
            }
        )


class FriendPaySession(FakeSession):
    def __init__(self):
        super().__init__()
        self.state = "confirm_friend_pay"
        self.filled_account = ""
        self.friend_request_clicks = 0

    def click_action(self, labels, _target_id=None):
        if "找朋友帮忙付" in labels and self.state == "confirm_friend_pay":
            self.submit_clicks += 1
            self.state = "friend_pay_request"
            return {"clicked": True, "text": "找朋友帮忙付"}
        if "请他付款" in labels and self.state == "friend_pay_request":
            self.friend_request_clicks += 1
            self.state = "friend_pay_sent"
            return {"clicked": True, "text": "请他付款"}
        return super().click_action(labels, _target_id)

    def fill_friend_pay_account(self, account, _target_id=None):
        self.filled_account = account
        return {"filled": True}


class PaymentErrorSession(FakeSession):
    def click_action(self, labels, _target_id=None):
        if "提交订单" in labels and self.state == "confirm_order":
            self.state = "payment_error"
            return {"clicked": True, "text": "提交订单"}
        return super().click_action(labels, _target_id)


class RecoveringPaymentSession(PaymentErrorSession):
    def __init__(self):
        super().__init__()
        self.reload_count = 0

    def reload_page(self, _target_id=None):
        self.reload_count += 1
        self.state = "pending_payment"


class FakeSessions:
    def __init__(self, session):
        self.session = session

    def get_or_create(self, _account):
        return self.session


class TaskRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = V2Store(Path(self.temp_dir.name) / "assistant.db")
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        self.account_id = account_id
        self.product_id = product_id
        self.task_id = self.store.add_task(
            "任务A",
            account_id,
            product_id,
            (datetime.now() - timedelta(seconds=1)).isoformat(timespec="milliseconds"),
        )

    def test_parses_millisecond_schedule(self) -> None:
        parsed = parse_scheduled_at("2026-07-13 20:00:00.125")
        self.assertEqual(parsed.microsecond, 125000)

    def test_submit_ready_timeout_allows_slow_confirm_page(self) -> None:
        self.assertGreaterEqual(SUBMIT_READY_TIMEOUT_SECONDS, 30.0)

    def test_flash_mode_waits_briefly_for_buy_button(self) -> None:
        self.assertGreaterEqual(BUY_READY_TIMEOUT_SECONDS, 15.0)

    def test_safe_page_location_removes_sensitive_query(self) -> None:
        page = classify_page(
            {
                "url": "https://cashier.example.com/pay/result?order_id=secret#token",
                "title": "结果",
                "controls": [],
            }
        )
        self.assertEqual(safe_page_location(page), "cashier.example.com/pay/result")

    def test_runs_authorized_task_to_pending_payment(self) -> None:
        self.store.authorize_task(self.task_id)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(FakeSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "待付款")
        task = self.store.get_task(self.task_id)
        self.assertEqual(task["status"], "待付款")
        self.assertEqual(task["attempt_count"], 1)
        self.assertTrue(task["triggered_at"])
        self.assertTrue(task["completed_at"])

    def test_submits_prepared_confirm_page_without_clicking_buy_again(self) -> None:
        self.store.authorize_task(self.task_id)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(PreparedConfirmSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "待付款")
        self.assertEqual(self.store.get_task(self.task_id)["attempt_count"], 0)

    def test_refuses_to_submit_while_privacy_rule_tab_is_open(self) -> None:
        self.store.authorize_task(self.task_id)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(AuxiliaryPageSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")
        self.assertIn("隐私、协议或规则说明标签", outcome.message)

    def test_refuses_multiple_submit_buttons(self) -> None:
        self.store.authorize_task(self.task_id)
        session = MultipleSubmitSession()
        runner = SingleAccountTaskRunner(self.store, FakeSessions(session))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")
        self.assertIn("多个提交按钮", outcome.message)
        self.assertEqual(session.submit_clicks, 0)

    def test_refuses_task_without_authorization(self) -> None:
        runner = SingleAccountTaskRunner(self.store, FakeSessions(FakeSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")

    def test_waits_for_delayed_navigation_after_submit(self) -> None:
        self.store.authorize_task(self.task_id)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(DelayedSubmitSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "待付款")

    def test_waits_for_submit_button_to_become_enabled(self) -> None:
        self.store.authorize_task(self.task_id)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(DelayedSubmitButtonSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "待付款")

    def test_reports_alipay_network_error_without_encouraging_duplicate_order(self) -> None:
        self.store.authorize_task(self.task_id)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(PaymentErrorSession()))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")
        self.assertIn("支付页面网络连接失败", outcome.message)
        self.assertIn("不要重复创建任务", outcome.message)
        self.assertEqual(self.store.get_task(self.task_id)["status"], "需人工处理")

    def test_recovers_alipay_network_error_by_reloading_payment_page_only(self) -> None:
        self.store.authorize_task(self.task_id)
        session = RecoveringPaymentSession()
        runner = SingleAccountTaskRunner(self.store, FakeSessions(session))
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "待付款")
        self.assertEqual(session.reload_count, 1)
        self.assertEqual(self.store.get_task(self.task_id)["attempt_count"], 1)

    def test_cancelled_task_does_not_click(self) -> None:
        self.store.set_task_schedule(
            self.task_id,
            (datetime.now() + timedelta(minutes=1)).isoformat(timespec="milliseconds"),
        )
        self.store.authorize_task(self.task_id)
        cancel_event = threading.Event()
        cancel_event.set()
        runner = SingleAccountTaskRunner(self.store, FakeSessions(FakeSession()))
        outcome = runner.run(self.task_id, "target-1", cancel_event)
        self.assertEqual(outcome.status, "已取消")

    def test_authorization_is_rechecked_after_wait(self) -> None:
        self.store.authorize_task(self.task_id)
        session = PreparedConfirmSession()
        runner = SingleAccountTaskRunner(self.store, FakeSessions(session))

        def revoke_during_wait(_target, _cancel_event) -> None:
            self.store.clear_task_authorization(self.task_id, "预检中")

        runner._wait_until = revoke_during_wait
        outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "已取消")
        self.assertEqual(session.submit_clicks, 0)

    def test_cancel_between_final_inspection_and_submit_prevents_click(self) -> None:
        self.store.authorize_task(self.task_id)
        cancel_event = threading.Event()
        session = CancelBeforeSubmitSession(cancel_event)
        runner = SingleAccountTaskRunner(self.store, FakeSessions(session))
        outcome = runner.run(self.task_id, "target-1", cancel_event)
        self.assertEqual(outcome.status, "已取消")
        self.assertEqual(session.submit_clicks, 0)

    def test_slow_navigation_never_clicks_buy_twice(self) -> None:
        self.store.authorize_task(self.task_id)
        session = SlowBuyNavigationSession()
        runner = SingleAccountTaskRunner(self.store, FakeSessions(session))
        with patch.object(runner, "_wait_for_state", return_value=snapshot("product")):
            outcome = runner.run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")
        self.assertEqual(session.buy_clicks, 1)
        self.assertIn("不会再次点击", outcome.message)

    def test_flash_mode_waits_for_button_then_buys_and_submits_once(self) -> None:
        task_id = self.store.add_task(
            "到点开售",
            self.account_id,
            self.product_id,
            (datetime.now() - timedelta(seconds=1)).isoformat(timespec="milliseconds"),
            TASK_MODE_FLASH,
        )
        self.store.authorize_task(task_id)
        session = DelayedFlashBuyButtonSession()
        outcome = SingleAccountTaskRunner(self.store, FakeSessions(session)).run(task_id, "target-1")
        self.assertEqual(outcome.status, "待付款")
        self.assertEqual(session.buy_clicks, 1)
        self.assertEqual(session.submit_clicks, 1)

    def test_flash_mode_times_out_without_clicking_disabled_button(self) -> None:
        task_id = self.store.add_task(
            "未开售",
            self.account_id,
            self.product_id,
            (datetime.now() - timedelta(seconds=1)).isoformat(timespec="milliseconds"),
            TASK_MODE_FLASH,
        )
        self.store.authorize_task(task_id)
        session = DelayedFlashBuyButtonSession()
        session.disabled_inspections = 100
        with patch("src.task_runner.BUY_READY_TIMEOUT_SECONDS", 0):
            outcome = SingleAccountTaskRunner(self.store, FakeSessions(session)).run(task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")
        self.assertEqual(session.buy_clicks, 0)

    def test_never_clicks_passwordless_payment_action(self) -> None:
        self.store.authorize_task(self.task_id)
        session = PasswordlessPaymentSession()
        outcome = SingleAccountTaskRunner(self.store, FakeSessions(session)).run(self.task_id, "target-1")
        self.assertEqual(outcome.status, "需人工处理")
        self.assertIn("可能直接扣款", outcome.message)
        self.assertEqual(session.submit_clicks, 0)

    def test_friend_pay_request_is_filled_and_sent_once(self) -> None:
        task_id = self.store.add_task(
            "朋友代付任务",
            self.account_id,
            self.product_id,
            (datetime.now() - timedelta(seconds=1)).isoformat(timespec="milliseconds"),
            "人工确认",
            "",
            True,
            "13800138000",
        )
        self.store.authorize_task(task_id)
        session = FriendPaySession()
        outcome = SingleAccountTaskRunner(self.store, FakeSessions(session)).run(
            task_id,
            "target-1",
        )
        self.assertEqual(outcome.status, "代付待确认")
        self.assertEqual(session.filled_account, "13800138000")
        self.assertEqual(session.submit_clicks, 1)
        self.assertEqual(session.friend_request_clicks, 1)
        task = self.store.get_task(task_id)
        self.assertTrue(task["friend_pay_requested_at"])
        self.assertNotIn(
            "13800138000",
            " ".join(event["message"] for event in self.store.list_events()),
        )


if __name__ == "__main__":
    unittest.main()
