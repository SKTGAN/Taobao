from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from src.page_automation import PageSnapshot, has_enabled_action
from src.safe_browser import BrowserSessionManager
from src.v2_store import V2Store


BUY_ACTIONS = ("立即购买", "马上抢", "立即抢购", "支付定金")
SUBMIT_ACTIONS = ("提交订单", "提交并支付", "确认提交")


class TaskNeedsHuman(RuntimeError):
    pass


class TaskCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskRunOutcome:
    status: str
    message: str


def parse_scheduled_at(value: str) -> datetime:
    normalized = value.strip().replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("计划时间格式应为 YYYY-MM-DD HH:MM:SS 或带毫秒的 YYYY-MM-DD HH:MM:SS.fff") from exc


def _require_ready_product(snapshot: PageSnapshot) -> None:
    if snapshot.kind in {"login", "challenge"}:
        raise TaskNeedsHuman("淘宝要求登录或安全验证，请在账号 Chrome 中处理后重新准备。")
    if snapshot.kind != "product":
        raise TaskNeedsHuman("触发前当前页面已不是商品页，请重新准备并授权。")
    if not has_enabled_action(snapshot, BUY_ACTIONS):
        raise TaskNeedsHuman("商品购买按钮当前不可用，请检查库存、活动时间和 SKU。")


def safe_page_location(snapshot: PageSnapshot) -> str:
    parts = urlsplit(snapshot.url)
    location = f"{parts.netloc}{parts.path}" if parts.netloc else parts.path or snapshot.url
    return location[:240]


class SingleAccountTaskRunner:
    def __init__(self, store: V2Store, sessions: BrowserSessionManager):
        self.store = store
        self.sessions = sessions

    @staticmethod
    def _wait_until(target: datetime, cancel_event: threading.Event) -> None:
        remaining = (target - datetime.now()).total_seconds()
        target_monotonic = time.monotonic() + max(0.0, remaining)
        while True:
            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            remaining = target_monotonic - time.monotonic()
            if remaining <= 0:
                return
            if remaining > 0.25:
                cancel_event.wait(min(0.5, remaining - 0.1))
            else:
                time.sleep(min(0.005, remaining))

    @staticmethod
    def _wait_for_state(
        session,
        target_id: str | None,
        timeout: float,
        stop_kinds: set[str],
        cancel_event: threading.Event,
    ) -> PageSnapshot:
        deadline = time.monotonic() + timeout
        last_snapshot = None
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            last_snapshot = session.inspect_page(target_id)
            if last_snapshot.kind in stop_kinds:
                return last_snapshot
            time.sleep(0.1)
        if last_snapshot is None:
            raise TaskNeedsHuman("页面在等待期间没有返回可识别状态。")
        return last_snapshot

    @staticmethod
    def _wait_for_enabled_action(
        session,
        target_id: str | None,
        labels: tuple[str, ...],
        timeout: float,
        cancel_event: threading.Event,
    ) -> PageSnapshot:
        deadline = time.monotonic() + timeout
        last_snapshot = None
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            last_snapshot = session.inspect_page(target_id)
            if last_snapshot.kind in {"login", "challenge", "pending_payment"}:
                return last_snapshot
            if last_snapshot.kind == "confirm_order" and has_enabled_action(last_snapshot, labels):
                return last_snapshot
            time.sleep(0.2)
        if last_snapshot is None:
            raise TaskNeedsHuman("确认订单页没有返回可识别状态。")
        return last_snapshot

    def run(
        self,
        task_id: int,
        target_id: str | None,
        cancel_event: threading.Event | None = None,
    ) -> TaskRunOutcome:
        cancel_event = cancel_event or threading.Event()
        task = self.store.get_task(task_id)
        if not task:
            return TaskRunOutcome("失败", "任务不存在。")
        account = self.store.get_account(int(task["account_id"]))
        if not account:
            return TaskRunOutcome("失败", "任务账号不存在。")

        try:
            if not task["authorized_at"] or task["status"] != "已武装":
                raise TaskNeedsHuman("任务尚未完成本次人工授权。")
            scheduled_at = parse_scheduled_at(str(task["scheduled_at"]))
            session = self.sessions.get_or_create(account)
            _require_ready_product(session.inspect_page(target_id))
            self.store.set_task_status(task_id, "等待中", "")
            self.store.log("INFO", "调度", f"任务等待触发：{scheduled_at.isoformat(timespec='milliseconds')}", task["name"])
            self._wait_until(scheduled_at, cancel_event)

            self.store.mark_task_triggered(task_id)
            self.store.log("INFO", "调度", "到达计划时间，开始触发购买", task["name"])

            snapshot = session.inspect_page(target_id)
            for _ in range(10):
                if cancel_event.is_set():
                    raise TaskCancelled("任务已由用户停止。")
                if snapshot.kind in {"login", "challenge"}:
                    raise TaskNeedsHuman("触发时出现登录或安全验证，请人工处理。")
                if snapshot.kind == "pending_payment":
                    self.store.mark_task_completed(task_id, "待付款")
                    return TaskRunOutcome("待付款", "订单已进入待付款页面。")
                if snapshot.kind == "confirm_order":
                    break
                if snapshot.kind != "product":
                    raise TaskNeedsHuman("触发后进入了无法识别的页面，请人工检查。")
                click_result = session.click_action(BUY_ACTIONS, target_id)
                self.store.increment_task_attempt(task_id)
                if not click_result.get("clicked"):
                    raise TaskNeedsHuman("未找到可点击的“立即购买”按钮。")
                snapshot = self._wait_for_state(
                    session,
                    target_id,
                    1.5,
                    {"confirm_order", "pending_payment", "login", "challenge"},
                    cancel_event,
                )

            if snapshot.kind != "confirm_order":
                raise TaskNeedsHuman("多次触发后仍未进入确认订单页，请检查库存和 SKU。")
            snapshot = self._wait_for_enabled_action(
                session,
                target_id,
                SUBMIT_ACTIONS,
                15.0,
                cancel_event,
            )
            if snapshot.kind == "pending_payment":
                self.store.mark_task_completed(task_id, "待付款")
                return TaskRunOutcome("待付款", "订单已进入待付款页面。")
            if snapshot.kind in {"login", "challenge"}:
                raise TaskNeedsHuman("确认订单时出现登录或安全验证，请人工处理。")
            if snapshot.kind != "confirm_order" or not has_enabled_action(snapshot, SUBMIT_ACTIONS):
                raise TaskNeedsHuman("确认订单页的提交按钮不可用，请检查地址、价格或协议选项。")

            self.store.set_task_status(task_id, "提交中", "")
            click_result = session.click_action(SUBMIT_ACTIONS, target_id)
            if not click_result.get("clicked"):
                raise TaskNeedsHuman("未能点击确认订单页的提交按钮。")
            snapshot = self._wait_for_state(
                session,
                target_id,
                20.0,
                {"pending_payment", "login", "challenge"},
                cancel_event,
            )
            if snapshot.kind in {"login", "challenge"}:
                raise TaskNeedsHuman("提交订单时出现安全验证，请立即在 Chrome 中人工处理。")
            if snapshot.kind != "pending_payment":
                raise TaskNeedsHuman(
                    "订单提交后未识别到待付款页面，请人工核对订单状态。"
                    f"页面类型：{snapshot.kind}；页面路径：{safe_page_location(snapshot)}"
                )

            self.store.mark_task_completed(task_id, "待付款")
            self.store.log("INFO", "订单", "订单已进入待付款，程序不会自动支付", task["name"])
            return TaskRunOutcome("待付款", "订单已进入待付款页面，程序未执行支付。")
        except TaskCancelled as exc:
            self.store.set_task_status(task_id, "已取消", str(exc))
            self.store.log("WARNING", "调度", str(exc), task["name"])
            return TaskRunOutcome("已取消", str(exc))
        except TaskNeedsHuman as exc:
            self.store.set_task_status(task_id, "需人工处理", str(exc))
            self.store.log("WARNING", "任务", str(exc), task["name"])
            return TaskRunOutcome("需人工处理", str(exc))
        except Exception as exc:
            message = f"任务执行失败：{exc}"
            self.store.set_task_status(task_id, "失败", message)
            self.store.log("ERROR", "任务", message, task["name"])
            return TaskRunOutcome("失败", message)
