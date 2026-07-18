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
SUBMIT_ACTIONS = ("提交订单", "提交并支付", "确认提交", "立即支付", "立即付款")
SUBMIT_READY_TIMEOUT_SECONDS = 30.0


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


def _open_auxiliary_pages(session) -> list[dict]:
    list_pages = getattr(session, "auxiliary_pages", None)
    if not callable(list_pages):
        return []
    try:
        pages = list_pages()
    except Exception:
        return []
    return pages if isinstance(pages, list) else []


def _require_no_auxiliary_pages(session) -> None:
    if _open_auxiliary_pages(session):
        raise TaskNeedsHuman(
            "检测到隐私、协议或规则说明标签仍然打开。请关闭这些标签，返回确认订单页人工处理相关选项，"
            "然后重新准备并授权；程序不会代替你同意协议。"
        )


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
            _require_no_auxiliary_pages(session)
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
            _require_no_auxiliary_pages(session)
            last_snapshot = session.inspect_page(target_id)
            if last_snapshot.kind in {"login", "challenge", "pending_payment", "payment_error"}:
                return last_snapshot
            if last_snapshot.kind == "confirm_order" and has_enabled_action(last_snapshot, labels):
                return last_snapshot
            time.sleep(0.2)
        if last_snapshot is None:
            raise TaskNeedsHuman("确认订单页没有返回可识别状态。")
        return last_snapshot

    @staticmethod
    def _retry_payment_page(
        session,
        target_id: str | None,
        cancel_event: threading.Event,
        attempts: int = 2,
        timeout_per_attempt: float = 8.0,
    ) -> PageSnapshot:
        """Retry only the payment document; never clicks the order-submit action again."""
        last_snapshot = session.inspect_page(target_id)
        reload_page = getattr(session, "reload_page", None)
        if not callable(reload_page):
            return last_snapshot
        for _ in range(attempts):
            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            reload_page(target_id)
            deadline = time.monotonic() + timeout_per_attempt
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    raise TaskCancelled("任务已由用户停止。")
                last_snapshot = session.inspect_page(target_id)
                if last_snapshot.kind in {"pending_payment", "login", "challenge"}:
                    return last_snapshot
                time.sleep(0.2)
        return last_snapshot

    @staticmethod
    def _open_order_list_fallback(session) -> bool:
        open_bought_items = getattr(session, "open_bought_items", None)
        if not callable(open_bought_items):
            return False
        try:
            open_bought_items()
            return True
        except Exception:
            return False

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
            _require_no_auxiliary_pages(session)
            initial_snapshot = session.inspect_page(target_id)
            prepared_confirm = initial_snapshot.kind == "confirm_order"
            if prepared_confirm:
                if not has_enabled_action(initial_snapshot, SUBMIT_ACTIONS):
                    raise TaskNeedsHuman(
                        "已授权的确认订单页提交按钮不再可用，请检查地址、价格、号码保护或协议选项。"
                    )
            else:
                _require_ready_product(initial_snapshot)
            self.store.set_task_status(task_id, "等待中", "")
            self.store.log("INFO", "调度", f"任务等待触发：{scheduled_at.isoformat(timespec='milliseconds')}", task["name"])
            self._wait_until(scheduled_at, cancel_event)

            self.store.mark_task_triggered(task_id)
            action_description = "提交已人工核对的订单" if prepared_confirm else "触发购买"
            self.store.log("INFO", "调度", f"到达计划时间，开始{action_description}", task["name"])

            _require_no_auxiliary_pages(session)
            snapshot = session.inspect_page(target_id)
            if prepared_confirm:
                if snapshot.kind in {"login", "challenge"}:
                    raise TaskNeedsHuman("触发时出现登录或安全验证，请人工处理。")
                if snapshot.kind == "pending_payment":
                    self.store.mark_task_completed(task_id, "待付款")
                    return TaskRunOutcome("待付款", "订单已进入待付款页面。")
                if snapshot.kind == "payment_error":
                    raise TaskNeedsHuman(
                        "订单已提交并进入支付宝支付地址，但支付页面网络连接失败。"
                        "请先到淘宝“待付款”核对订单；网络恢复后刷新当前支付页，不要重复创建任务。"
                    )
                if snapshot.kind != "confirm_order":
                    raise TaskNeedsHuman(
                        "预先核对并授权的确认订单页已经失效或被切换；为避免绕过人工核对，任务不会重新点击购买。"
                    )
            else:
                for _ in range(10):
                    if cancel_event.is_set():
                        raise TaskCancelled("任务已由用户停止。")
                    if snapshot.kind in {"login", "challenge"}:
                        raise TaskNeedsHuman("触发时出现登录或安全验证，请人工处理。")
                    if snapshot.kind == "pending_payment":
                        self.store.mark_task_completed(task_id, "待付款")
                        return TaskRunOutcome("待付款", "订单已进入待付款页面。")
                    if snapshot.kind == "payment_error":
                        raise TaskNeedsHuman(
                            "订单已提交并进入支付宝支付地址，但支付页面网络连接失败。"
                            "请先到淘宝“待付款”核对订单；网络恢复后刷新当前支付页，不要重复创建任务。"
                        )
                    if snapshot.kind == "confirm_order":
                        break
                    if snapshot.kind != "product":
                        raise TaskNeedsHuman("触发后进入了无法识别的页面，请人工检查。")
                    click_result = session.click_action(BUY_ACTIONS, target_id)
                    self.store.increment_task_attempt(task_id)
                    if not click_result.get("clicked"):
                        reason = str(click_result.get("reason") or "not_found")
                        raise TaskNeedsHuman(f"未找到可安全点击的“立即购买”按钮（{reason}）。")
                    snapshot = self._wait_for_state(
                        session,
                        target_id,
                        1.5,
                        {"confirm_order", "pending_payment", "payment_error", "login", "challenge"},
                        cancel_event,
                    )

            if snapshot.kind != "confirm_order":
                raise TaskNeedsHuman("多次触发后仍未进入确认订单页，请检查库存和 SKU。")
            snapshot = self._wait_for_enabled_action(
                session,
                target_id,
                SUBMIT_ACTIONS,
                SUBMIT_READY_TIMEOUT_SECONDS,
                cancel_event,
            )
            if snapshot.kind == "pending_payment":
                self.store.mark_task_completed(task_id, "待付款")
                return TaskRunOutcome("待付款", "订单已进入待付款页面。")
            if snapshot.kind == "payment_error":
                raise TaskNeedsHuman(
                    "订单已提交并进入支付宝支付地址，但支付页面网络连接失败。"
                    "请先到淘宝“待付款”核对订单；网络恢复后刷新当前支付页，不要重复创建任务。"
                )
            if snapshot.kind in {"login", "challenge"}:
                raise TaskNeedsHuman("确认订单时出现登录或安全验证，请人工处理。")
            if snapshot.kind != "confirm_order" or not has_enabled_action(snapshot, SUBMIT_ACTIONS):
                raise TaskNeedsHuman("确认订单页的提交按钮不可用，请检查地址、价格或协议选项。")

            _require_no_auxiliary_pages(session)
            self.store.set_task_status(task_id, "提交中", "")
            click_result = session.click_action(SUBMIT_ACTIONS, target_id)
            if not click_result.get("clicked"):
                reason = str(click_result.get("reason") or "not_found")
                raise TaskNeedsHuman(f"未能安全点击确认订单页的提交按钮（{reason}）。")
            snapshot = self._wait_for_state(
                session,
                target_id,
                20.0,
                {"pending_payment", "payment_error", "login", "challenge"},
                cancel_event,
            )
            if snapshot.kind in {"login", "challenge"}:
                raise TaskNeedsHuman("提交订单时出现安全验证，请立即在 Chrome 中人工处理。")
            if snapshot.kind == "payment_error":
                snapshot = self._retry_payment_page(session, target_id, cancel_event)
            if snapshot.kind in {"login", "challenge"}:
                raise TaskNeedsHuman("重新加载支付页时出现安全验证，请立即在 Chrome 中人工处理。")
            if snapshot.kind == "payment_error":
                opened_orders = self._open_order_list_fallback(session)
                fallback = "已为你打开淘宝订单列表，请从“待付款”进入。" if opened_orders else "请到淘宝“待付款”核对订单。"
                raise TaskNeedsHuman(
                    "订单已提交并进入支付宝支付地址，但支付页面网络连接失败，安全重载后仍未恢复。"
                    f"{fallback}不要重复创建任务。"
                )
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
