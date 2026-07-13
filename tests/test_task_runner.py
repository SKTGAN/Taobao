from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.page_automation import classify_page
from src.task_runner import SingleAccountTaskRunner, parse_scheduled_at, safe_page_location
from src.v2_store import V2Store


def snapshot(kind: str):
    fixtures = {
        "product": {
            "url": "https://item.taobao.com/item.htm?id=1",
            "title": "商品",
            "readyState": "complete",
            "bodyText": "库存充足",
            "controls": [{"text": "立即购买", "disabled": False}],
        },
        "confirm_order": {
            "url": "https://buy.taobao.com/auction/order/confirm_order.htm",
            "title": "确认订单",
            "readyState": "complete",
            "bodyText": "提交订单",
            "controls": [{"text": "提交订单", "disabled": False}],
        },
        "pending_payment": {
            "url": "https://cashier.alipay.com/standard/payment/cashier.htm",
            "title": "收银台",
            "readyState": "complete",
            "bodyText": "订单待付款",
            "controls": [],
        },
    }
    return classify_page(fixtures[kind])


class FakeSession:
    def __init__(self):
        self.state = "product"

    def inspect_page(self, _target_id=None):
        return snapshot(self.state)

    def click_action(self, labels, _target_id=None):
        if "立即购买" in labels and self.state == "product":
            self.state = "confirm_order"
            return {"clicked": True, "text": "立即购买"}
        if "提交订单" in labels and self.state == "confirm_order":
            self.state = "pending_payment"
            return {"clicked": True, "text": "提交订单"}
        return {"clicked": False, "text": ""}


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
        self.task_id = self.store.add_task(
            "任务A",
            account_id,
            product_id,
            (datetime.now() - timedelta(seconds=1)).isoformat(timespec="milliseconds"),
        )

    def test_parses_millisecond_schedule(self) -> None:
        parsed = parse_scheduled_at("2026-07-13 20:00:00.125")
        self.assertEqual(parsed.microsecond, 125000)

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


if __name__ == "__main__":
    unittest.main()
