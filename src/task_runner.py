from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from src.page_automation import PageSnapshot, enabled_action_count
from src.safe_browser import BrowserSessionManager
from src.taobao_time import TaobaoClock
from src.v2_store import TASK_MODE_FLASH, V2Store


BUY_ACTIONS = ("立即购买", "马上抢", "立即抢购", "支付定金")
SUBMIT_ACTIONS = ("提交订单",)
PAYMENT_ACTIONS = ("免密支付", "立即支付", "立即付款", "确认付款", "支付订单")
FRIEND_PAY_OPTION_ACTIONS = ("朋友代付",)
FRIEND_PAY_ORDER_ACTIONS = ("找朋友帮忙付", "提交订单")
FRIEND_PAY_REQUEST_ACTIONS = ("请他付款",)
SUBMIT_READY_TIMEOUT_SECONDS = 30.0
BUY_NAVIGATION_TIMEOUT_SECONDS = 15.0
BUY_READY_TIMEOUT_SECONDS = 15.0


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
    action_count = enabled_action_count(snapshot, BUY_ACTIONS)
    if action_count == 0:
        raise TaskNeedsHuman("商品购买按钮当前不可用，请检查库存、活动时间和 SKU。")
    if action_count > 1:
        raise TaskNeedsHuman("商品页存在多个可用购买按钮，无法确认唯一目标，请人工关闭弹层或无关区域后重试。")


def _require_product_page(snapshot: PageSnapshot) -> None:
    if snapshot.kind in {"login", "challenge"}:
        raise TaskNeedsHuman("淘宝要求登录或安全验证，请在账号 Chrome 中处理后重新准备。")
    if snapshot.kind != "product":
        raise TaskNeedsHuman("触发前当前页面已不是已准备的商品页，请重新准备并授权。")


def _payment_action_message() -> str:
    return (
        "当前确认页显示的是“免密支付/立即支付”类按钮，点击后可能直接扣款，程序不会点击。"
        "请在淘宝/支付宝中关闭该订单使用的免密支付，或改为需要进入收银台的支付方式后重新准备。"
    )


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
    def __init__(
        self,
        store: V2Store,
        sessions: BrowserSessionManager,
        clock: TaobaoClock | None = None,
    ):
        self.store = store
        self.sessions = sessions
        self.clock = clock or TaobaoClock()

    def _wait_until(self, target: datetime, cancel_event: threading.Event) -> None:
        remaining = (target - self.clock.now()).total_seconds()
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
            if last_snapshot.kind == "confirm_order":
                if enabled_action_count(last_snapshot, PAYMENT_ACTIONS) > 0:
                    raise TaskNeedsHuman(_payment_action_message())
                action_count = enabled_action_count(last_snapshot, labels)
                if action_count > 1:
                    raise TaskNeedsHuman("确认订单页存在多个提交按钮，程序已停止，请人工核对页面。")
                if action_count == 1:
                    return last_snapshot
            time.sleep(0.2)
        if last_snapshot is None:
            raise TaskNeedsHuman("确认订单页没有返回可识别状态。")
        return last_snapshot

    @staticmethod
    def _apply_checkout_preferences(
        session,
        target_id: str | None,
        task: dict,
    ) -> PageSnapshot:
        snapshot = session.inspect_page(target_id)
        address_keyword = str(task.get("address_keyword") or "").strip()
        if address_keyword:
            select_address = getattr(session, "select_checkout_address", None)
            if not callable(select_address):
                raise TaskNeedsHuman("当前浏览器层不支持选择任务地址，请人工处理。")
            result = select_address(address_keyword, target_id)
            if not result.get("clicked"):
                reason = str(result.get("reason") or "address_not_found")
                raise TaskNeedsHuman(
                    f"未能唯一匹配任务配置的已有收货地址（{reason}），请人工核对地址。"
                )
            time.sleep(0.2)
            snapshot = session.inspect_page(target_id)

        if task.get("friend_pay_enabled"):
            friend_action_count = enabled_action_count(
                snapshot,
                FRIEND_PAY_ORDER_ACTIONS,
            )
            if friend_action_count == 0:
                result = session.click_action(FRIEND_PAY_OPTION_ACTIONS, target_id)
                if not result.get("clicked"):
                    reason = str(result.get("reason") or "friend_pay_option_not_found")
                    raise TaskNeedsHuman(f"未能唯一选择“朋友代付”（{reason}）。")
                time.sleep(0.3)
                snapshot = session.inspect_page(target_id)
            friend_action_count = enabled_action_count(
                snapshot,
                FRIEND_PAY_ORDER_ACTIONS,
            )
            if friend_action_count != 1:
                raise TaskNeedsHuman(
                    "选择朋友代付后未发现唯一的“找朋友帮忙付/提交订单”按钮，请人工核对。"
                )
        return snapshot

    def _request_friend_payment(
        self,
        session,
        target_id: str | None,
        task: dict,
        cancel_event: threading.Event,
    ) -> TaskRunOutcome:
        account = str(task.get("friend_pay_account") or "")
        if not account:
            raise TaskNeedsHuman("朋友代付手机号为空，程序不会发起代付请求。")
        self.store.set_task_status(int(task["id"]), "代付申请中", "")
        snapshot = self._wait_for_state(
            session,
            target_id,
            20.0,
            {
                "friend_pay_request",
                "friend_pay_sent",
                "login",
                "challenge",
                "payment_error",
            },
            cancel_event,
        )
        if snapshot.kind == "friend_pay_sent":
            self.store.mark_friend_pay_requested(int(task["id"]))
            return TaskRunOutcome("代付待确认", "朋友代付申请已发出，等待对方在官方页面确认。")
        if snapshot.kind in {"login", "challenge"}:
            raise TaskNeedsHuman("进入朋友代付页面时出现登录或安全验证，请人工处理。")
        if snapshot.kind == "payment_error":
            raise TaskNeedsHuman("朋友代付页面网络连接失败，请到淘宝订单页核对订单状态。")
        if snapshot.kind != "friend_pay_request":
            raise TaskNeedsHuman("下单后未进入可识别的朋友代付申请页，请人工核对订单。")

        fill_account = getattr(session, "fill_friend_pay_account", None)
        if not callable(fill_account):
            raise TaskNeedsHuman("当前浏览器层不支持填写代付账号，请人工处理。")
        fill_result = fill_account(account, target_id)
        if not fill_result.get("filled"):
            reason = str(fill_result.get("reason") or "friend_account_input_not_found")
            raise TaskNeedsHuman(f"未能安全填写代付人账号（{reason}）。")
        click_result = session.click_action(FRIEND_PAY_REQUEST_ACTIONS, target_id)
        if not click_result.get("clicked"):
            reason = str(click_result.get("reason") or "friend_pay_request_not_found")
            raise TaskNeedsHuman(f"未能唯一点击“请他付款”（{reason}）。")
        snapshot = self._wait_for_state(
            session,
            target_id,
            15.0,
            {"friend_pay_sent", "login", "challenge", "payment_error"},
            cancel_event,
        )
        if snapshot.kind == "friend_pay_sent":
            self.store.mark_friend_pay_requested(int(task["id"]))
            self.store.log(
                "INFO",
                "代付",
                "朋友代付申请已发出，等待对方确认；代付账号未写入日志",
                str(task["name"]),
            )
            return TaskRunOutcome("代付待确认", "朋友代付申请已发出，等待对方在官方页面确认。")
        if snapshot.kind in {"login", "challenge"}:
            raise TaskNeedsHuman("发起朋友代付时出现登录或安全验证，请人工处理。")
        raise TaskNeedsHuman("点击“请他付款”后未识别到成功提示，请人工核对，程序不会重复发送。")

    @staticmethod
    def _wait_for_buy_action(
        session,
        target_id: str | None,
        timeout: float,
        cancel_event: threading.Event,
    ) -> PageSnapshot:
        """Poll only the already-open DOM; this does not refresh or request inventory."""
        deadline = time.monotonic() + timeout
        last_snapshot = session.inspect_page(target_id)
        while True:
            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            if last_snapshot.kind in {"login", "challenge"}:
                return last_snapshot
            if last_snapshot.kind != "product":
                return last_snapshot
            action_count = enabled_action_count(last_snapshot, BUY_ACTIONS)
            if action_count > 1:
                raise TaskNeedsHuman("商品页存在多个购买按钮，无法确认唯一目标，请人工检查。")
            if action_count == 1:
                return last_snapshot
            if time.monotonic() >= deadline:
                return last_snapshot
            time.sleep(0.05)
            last_snapshot = session.inspect_page(target_id)

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

    def _require_current_authorization(
        self,
        task_id: int,
        authorization_stamp: str,
        scheduled_at: datetime,
        allowed_statuses: set[str],
    ) -> dict:
        latest = self.store.get_task(task_id)
        if not latest:
            raise TaskCancelled("任务已被删除，原授权自动失效。")
        same_schedule = parse_scheduled_at(str(latest["scheduled_at"])) == scheduled_at
        if (
            str(latest.get("authorized_at") or "") != authorization_stamp
            or str(latest.get("status") or "") not in allowed_statuses
            or not same_schedule
        ):
            raise TaskCancelled("任务状态、计划时间或授权已经变化，程序不会继续点击。")
        return latest

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
            authorization_stamp = str(task["authorized_at"])
            scheduled_at = parse_scheduled_at(str(task["scheduled_at"]))
            session = self.sessions.get_or_create(account)
            _require_no_auxiliary_pages(session)
            initial_snapshot = session.inspect_page(target_id)
            prepared_confirm = initial_snapshot.kind == "confirm_order"
            flash_mode = str(task.get("mode") or "") == TASK_MODE_FLASH
            if prepared_confirm:
                checkout_actions = (
                    FRIEND_PAY_ORDER_ACTIONS
                    if task.get("friend_pay_enabled")
                    else SUBMIT_ACTIONS
                )
                if (
                    not task.get("friend_pay_enabled")
                    and enabled_action_count(initial_snapshot, PAYMENT_ACTIONS) > 0
                ):
                    raise TaskNeedsHuman(_payment_action_message())
                initial_submit_count = enabled_action_count(initial_snapshot, checkout_actions)
                if initial_submit_count == 0:
                    raise TaskNeedsHuman(
                        "已授权的确认订单页目标按钮不再可用，请检查地址、价格、代付或协议选项。"
                    )
                if initial_submit_count > 1:
                    raise TaskNeedsHuman("已授权的确认订单页出现多个提交按钮，程序已停止，请重新核对页面。")
            else:
                if flash_mode:
                    _require_product_page(initial_snapshot)
                else:
                    _require_ready_product(initial_snapshot)
            self.store.set_task_status(task_id, "等待中", "")
            self.store.log("INFO", "调度", f"任务等待触发：{scheduled_at.isoformat(timespec='milliseconds')}", task["name"])
            self._wait_until(scheduled_at, cancel_event)

            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            self._require_current_authorization(
                task_id,
                authorization_stamp,
                scheduled_at,
                {"等待中"},
            )
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
                if flash_mode:
                    snapshot = self._wait_for_buy_action(
                        session,
                        target_id,
                        BUY_READY_TIMEOUT_SECONDS,
                        cancel_event,
                    )
                _require_ready_product(snapshot)
                click_result = session.click_action(BUY_ACTIONS, target_id)
                self.store.increment_task_attempt(task_id)
                if not click_result.get("clicked"):
                    reason = str(click_result.get("reason") or "not_found")
                    raise TaskNeedsHuman(f"未找到唯一且可安全点击的“立即购买”按钮（{reason}）。")
                snapshot = self._wait_for_state(
                    session,
                    target_id,
                    BUY_NAVIGATION_TIMEOUT_SECONDS,
                    {"confirm_order", "pending_payment", "payment_error", "login", "challenge"},
                    cancel_event,
                )

            if snapshot.kind != "confirm_order":
                raise TaskNeedsHuman("单次点击购买后仍未进入确认订单页；为避免重复下单，程序不会再次点击。")
            if not prepared_confirm:
                snapshot = self._apply_checkout_preferences(
                    session,
                    target_id,
                    task,
                )
            checkout_actions = (
                FRIEND_PAY_ORDER_ACTIONS
                if task.get("friend_pay_enabled")
                else SUBMIT_ACTIONS
            )
            snapshot = self._wait_for_enabled_action(
                session,
                target_id,
                checkout_actions,
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
            submit_count = enabled_action_count(snapshot, checkout_actions)
            if (
                not task.get("friend_pay_enabled")
                and enabled_action_count(snapshot, PAYMENT_ACTIONS) > 0
            ):
                raise TaskNeedsHuman(_payment_action_message())
            if snapshot.kind != "confirm_order" or submit_count == 0:
                raise TaskNeedsHuman("确认订单页的提交按钮不可用，请检查地址、价格或协议选项。")
            if submit_count > 1:
                raise TaskNeedsHuman("确认订单页存在多个提交按钮，程序已停止，请人工核对页面。")

            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            self._require_current_authorization(
                task_id,
                authorization_stamp,
                scheduled_at,
                {"触发中"},
            )
            _require_no_auxiliary_pages(session)
            if cancel_event.is_set():
                raise TaskCancelled("任务已由用户停止。")
            self.store.set_task_status(task_id, "提交中", "")
            click_result = session.click_action(checkout_actions, target_id)
            if not click_result.get("clicked"):
                reason = str(click_result.get("reason") or "not_found")
                raise TaskNeedsHuman(f"未能安全点击确认订单页的提交按钮（{reason}）。")
            if task.get("friend_pay_enabled"):
                return self._request_friend_payment(
                    session,
                    target_id,
                    task,
                    cancel_event,
                )
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
