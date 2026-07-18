from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import flet as ft

from src.app_config import AppConfig, load_config, save_config, validate_port
from src.environment_check import EnvironmentCheck, run_environment_checks
from src.page_automation import has_enabled_action, product_precheck_finished
from src.paths import prepare_data_dir
from src.product_urls import resolve_product_selection, sku_id_from_url
from src.safe_browser import BrowserLaunchError, BrowserSessionManager, find_google_chrome
from src.task_runner import BUY_ACTIONS, SUBMIT_ACTIONS, SingleAccountTaskRunner, parse_scheduled_at
from src.v2_store import V2Store


class FletGUI:
    APP_NAME = "TaoBao Assistant V2"

    def __init__(
        self,
        _legacy_config_path: Path | None = None,
        *,
        data_dir: Path | None = None,
        config: AppConfig | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else prepare_data_dir()
        self.config = config or load_config(self.data_dir)
        self.store = V2Store(self.data_dir / "taobao_assistant_v2.db")
        self.sessions = BrowserSessionManager(self.config.chrome_path)
        self.page: ft.Page | None = None
        self.content: ft.Container | None = None
        self.rail: ft.NavigationRail | None = None
        self._selected_index = 0
        self._task_targets: dict[int, str | None] = {}
        self._task_cancel_events: dict[int, threading.Event] = {}
        self._environment_results: list[EnvironmentCheck] = []
        self._environment_check_running = False
        self.runner = SingleAccountTaskRunner(self.store, self.sessions)

    def build(self, page: ft.Page) -> None:
        self.page = page
        page.title = "淘宝辅助购买 V2"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.bgcolor = "#F4F7FB"
        page.padding = 0
        page.window.width = 1280
        page.window.height = 820
        page.window.min_width = 980
        page.window.min_height = 680
        page.theme = ft.Theme(color_scheme_seed="#2563EB", font_family="Microsoft YaHei UI")

        self.rail = ft.NavigationRail(
            selected_index=0,
            extended=True,
            min_width=86,
            min_extended_width=210,
            bgcolor="#FFFFFF",
            leading=ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.SHOPPING_BAG_OUTLINED, color="#2563EB", size=30),
                    ft.Text("淘宝助手", weight=ft.FontWeight.BOLD, size=18),
                    ft.Text("V2 · 人工确认", color="#64748B", size=11),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.only(top=24, bottom=18),
            ),
            destinations=[
                ft.NavigationRailDestination(icon=ft.Icons.DASHBOARD_OUTLINED, selected_icon=ft.Icons.DASHBOARD, label="概览"),
                ft.NavigationRailDestination(icon=ft.Icons.PEOPLE_OUTLINE, selected_icon=ft.Icons.PEOPLE, label="账号管理"),
                ft.NavigationRailDestination(icon=ft.Icons.INVENTORY_2_OUTLINED, selected_icon=ft.Icons.INVENTORY_2, label="商品管理"),
                ft.NavigationRailDestination(icon=ft.Icons.SCHEDULE_OUTLINED, selected_icon=ft.Icons.SCHEDULE, label="任务中心"),
                ft.NavigationRailDestination(icon=ft.Icons.RECEIPT_LONG_OUTLINED, selected_icon=ft.Icons.RECEIPT_LONG, label="运行日志"),
                ft.NavigationRailDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS, label="设置"),
            ],
            on_change=self._navigate,
        )
        self.content = ft.Container(expand=True, padding=28)
        page.add(ft.Row([self.rail, ft.VerticalDivider(width=1), self.content], expand=True, spacing=0))
        self._render()
        if not self.config.first_run_complete:
            self._run_environment_check(first_run=True)

    def _navigate(self, event) -> None:
        self._selected_index = int(event.control.selected_index)
        self._render()

    def _render(self) -> None:
        builders = [
            self._dashboard,
            self._accounts,
            self._products,
            self._tasks,
            self._logs,
            self._settings,
        ]
        self.content.content = builders[self._selected_index]()
        self.page.update()

    @staticmethod
    def _header(title: str, subtitle: str, action: ft.Control | None = None) -> ft.Row:
        controls = [
            ft.Column([
                ft.Text(title, size=26, weight=ft.FontWeight.BOLD, color="#0F172A"),
                ft.Text(subtitle, size=13, color="#64748B"),
            ], spacing=4),
            ft.Container(expand=True),
        ]
        if action:
            controls.append(action)
        return ft.Row(controls, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    @staticmethod
    def _card(content: ft.Control, padding: int = 20) -> ft.Container:
        return ft.Container(
            content=content,
            bgcolor="#FFFFFF",
            border_radius=14,
            padding=padding,
            border=ft.border.all(1, "#E2E8F0"),
        )

    def _stat_card(self, title: str, value: str, icon, color: str) -> ft.Container:
        return self._card(ft.Row([
            ft.Container(ft.Icon(icon, color=color, size=26), bgcolor=f"{color}18", padding=12, border_radius=12),
            ft.Column([ft.Text(title, color="#64748B", size=12), ft.Text(value, size=24, weight=ft.FontWeight.BOLD)], spacing=2),
        ]))

    def _dashboard(self) -> ft.Control:
        accounts = self.store.list_accounts()
        products = self.store.list_products()
        tasks = self.store.list_tasks()
        logged_in = sum(1 for account in accounts if account["status"] == "已登录")
        ready = sum(
            1
            for task in tasks
            if task["status"] in {"待授权", "待核对订单", "已武装", "等待中"}
        )
        return ft.Column([
            self._header("运行概览", "扫码登录、商品准备和任务状态一目了然"),
            ft.Row([
                self._stat_card("账号", str(len(accounts)), ft.Icons.PERSON_OUTLINE, "#2563EB"),
                self._stat_card("已登录", str(logged_in), ft.Icons.VERIFIED_USER_OUTLINED, "#16A34A"),
                self._stat_card("商品", str(len(products)), ft.Icons.INVENTORY_2_OUTLINED, "#7C3AED"),
                self._stat_card("待处理任务", str(ready), ft.Icons.SCHEDULE, "#EA580C"),
            ], spacing=14),
            self._card(ft.Column([
                ft.Text("安全工作流", size=18, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "1. 扫码登录 → 2. 准备并固定 SKU → 3. 提前进入确认订单 → "
                    "4. 人工核对地址/价格/号码保护/协议 → 5. 单次定时授权。",
                    color="#475569",
                ),
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.SECURITY, color="#166534"),
                        ft.Text("不会保存淘宝密码，不处理验证码，不自动支付；自动提交订单前必须由你单次授权。", color="#166534", weight=ft.FontWeight.W_500),
                    ]),
                    bgcolor="#F0FDF4", padding=14, border_radius=10,
                ),
            ], spacing=14)),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    def _accounts(self) -> ft.Control:
        accounts = self.store.list_accounts()
        rows = []
        for account in accounts:
            account_id = int(account["id"])
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(account["nickname"], weight=ft.FontWeight.W_500)),
                ft.DataCell(self._status_badge(account["status"])),
                ft.DataCell(ft.Text("启用" if account["enabled"] else "停用")),
                ft.DataCell(ft.Row([
                    ft.OutlinedButton("扫码登录", icon=ft.Icons.QR_CODE_SCANNER, on_click=lambda e, aid=account_id: self._qr_login(aid)),
                    ft.TextButton("检查登录", on_click=lambda e, aid=account_id: self._check_login(aid)),
                    ft.IconButton(ft.Icons.POWER_SETTINGS_NEW, tooltip="启用/停用", on_click=lambda e, aid=account_id: self._toggle_account(aid)),
                ], spacing=4)),
            ]))
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("账号备注")), ft.DataColumn(ft.Text("登录状态")), ft.DataColumn(ft.Text("使用状态")), ft.DataColumn(ft.Text("操作"))],
            rows=rows,
            column_spacing=34,
        ) if rows else ft.Text("还没有账号。点击右上角添加，然后用淘宝 App 扫码登录。", color="#64748B")
        return ft.Column([
            self._header("账号管理", "一个账号对应一个独立、持久化的 Chrome 资料目录", ft.FilledButton("添加账号", icon=ft.Icons.ADD, on_click=self._show_add_account)),
            self._card(ft.Column([
                ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, color="#2563EB"), ft.Text("账号备注仅用于本地识别；软件不要求淘宝用户名或密码。", color="#334155")]),
                ft.Divider(color="#E2E8F0"),
                table,
            ], spacing=12)),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    def _products(self) -> ft.Control:
        products = self.store.list_products()
        rows = []
        for product in products:
            product_id = int(product["id"])
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(product["name"], weight=ft.FontWeight.W_500)),
                ft.DataCell(ft.Text(product["sku_note"] or "未记录")),
                ft.DataCell(ft.Text(str(product["quantity"]))),
                ft.DataCell(ft.Text(product["url"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)),
                ft.DataCell(ft.OutlinedButton("在账号中打开", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda e, pid=product_id: self._choose_account_for_product(pid))),
            ]))
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("商品")), ft.DataColumn(ft.Text("SKU 备注")), ft.DataColumn(ft.Text("数量")), ft.DataColumn(ft.Text("链接")), ft.DataColumn(ft.Text("操作"))],
            rows=rows,
            column_spacing=24,
        ) if rows else ft.Text("还没有商品。先添加一个普通、低价商品用于流程验证。", color="#64748B")
        return ft.Column([
            self._header("商品管理", "记录链接和 SKU 备注；实际 SKU 在淘宝页面中人工确认", ft.FilledButton("添加商品", icon=ft.Icons.ADD, on_click=self._show_add_product)),
            self._card(table),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    def _tasks(self) -> ft.Control:
        tasks = self.store.list_tasks()
        rows = []
        for task in tasks:
            task_id = int(task["id"])
            authorization_label = "核对后授权" if task["status"] == "待核对订单" else "授权"
            actions = [
                ft.FilledTonalButton("准备商品", icon=ft.Icons.PLAY_ARROW, on_click=lambda e, tid=task_id: self._prepare_task(tid)),
                ft.TextButton(authorization_label, icon=ft.Icons.VERIFIED_USER_OUTLINED, on_click=lambda e, tid=task_id: self._show_authorize_task(tid)),
                ft.TextButton("打开购物车", icon=ft.Icons.SHOPPING_CART_OUTLINED, on_click=lambda e, tid=task_id: self._open_task_cart(tid)),
            ]
            if task["status"] in {"已武装", "等待中", "触发中", "提交中"}:
                actions.append(
                    ft.IconButton(ft.Icons.STOP_CIRCLE_OUTLINED, tooltip="停止任务", on_click=lambda e, tid=task_id: self._cancel_task(tid))
                )
            status_controls = [self._status_badge(task["status"])]
            if task.get("last_error"):
                status_controls.append(ft.Text(task["last_error"], size=10, color="#991B1B", width=180, max_lines=2))
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(task["name"], weight=ft.FontWeight.W_500)),
                ft.DataCell(ft.Text(task["account_name"])),
                ft.DataCell(ft.Text(task["product_name"])),
                ft.DataCell(ft.Text(task["scheduled_at"].replace("T", " "))),
                ft.DataCell(ft.Column(status_controls, spacing=2, tight=True)),
                ft.DataCell(ft.Row(actions, spacing=4)),
            ]))
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("任务")), ft.DataColumn(ft.Text("账号")), ft.DataColumn(ft.Text("商品")), ft.DataColumn(ft.Text("计划时间")), ft.DataColumn(ft.Text("状态")), ft.DataColumn(ft.Text("操作"))],
            rows=rows,
            column_spacing=24,
        ) if rows else ft.Text("还没有任务。先创建单账号、单商品任务，再完成准备和授权。", color="#64748B")
        return ft.Column([
            self._header("任务中心", "人工预选并授权；到点提交到待付款，不自动支付", ft.FilledButton("创建任务", icon=ft.Icons.ADD_TASK, on_click=self._show_add_task)),
            self._card(table),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    def _logs(self) -> ft.Control:
        events = self.store.list_events()
        rows = [ft.DataRow(cells=[
            ft.DataCell(ft.Text(event["created_at"].replace("T", " "), size=12)),
            ft.DataCell(ft.Text(event["level"])),
            ft.DataCell(ft.Text(event["category"])),
            ft.DataCell(ft.Text(event["subject"])),
            ft.DataCell(ft.Text(event["message"])),
        ]) for event in events]
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("时间")), ft.DataColumn(ft.Text("级别")), ft.DataColumn(ft.Text("类别")), ft.DataColumn(ft.Text("对象")), ft.DataColumn(ft.Text("内容"))],
            rows=rows,
            column_spacing=20,
        ) if rows else ft.Text("暂无日志", color="#64748B")
        return ft.Column([
            self._header("运行日志", "扫码、登录检查、商品打开和异常都会记录在本地"),
            self._card(table),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    def _settings(self) -> ft.Control:
        chrome_path = ft.TextField(
            label="Google Chrome 路径（留空则自动查找）",
            value=self.config.chrome_path,
            hint_text=r"例如：C:\Program Files\Google\Chrome\Application\chrome.exe",
            expand=True,
        )
        port = ft.TextField(
            label="本地服务端口",
            value=str(self.config.port),
            keyboard_type=ft.KeyboardType.NUMBER,
            width=220,
        )
        result_controls: list[ft.Control] = []
        for item in self._environment_results:
            passed = item.passed
            result_controls.append(
                ft.ListTile(
                    leading=ft.Icon(
                        ft.Icons.CHECK_CIRCLE_OUTLINE if passed else ft.Icons.ERROR_OUTLINE,
                        color="#15803D" if passed else "#B91C1C",
                    ),
                    title=ft.Text(f"{item.name} · {item.status}"),
                    subtitle=ft.Text(item.message, selectable=True),
                )
            )
        if not result_controls:
            result_controls.append(ft.Text("尚未执行环境自检。", color="#64748B"))
        return ft.Column([
            self._header("设置", "Chrome、端口和首次运行环境检查"),
            self._card(ft.Column([
                ft.Text("运行配置", size=18, weight=ft.FontWeight.BOLD),
                ft.Row([chrome_path, port]),
                ft.Row([
                    ft.FilledButton(
                        "保存配置",
                        icon=ft.Icons.SAVE_OUTLINED,
                        on_click=lambda e: self._save_settings(chrome_path, port),
                    ),
                    ft.OutlinedButton(
                        "运行环境自检",
                        icon=ft.Icons.FACT_CHECK_OUTLINED,
                        disabled=self._environment_check_running,
                        on_click=lambda e: self._run_environment_check(),
                    ),
                ]),
                ft.Text("端口修改后需重新启动程序；Chrome 路径会用于新建或重新连接账号浏览器。", size=12, color="#64748B"),
                ft.ListTile(leading=ft.Icon(ft.Icons.LAN), title=ft.Text("代理"), subtitle=ft.Text("默认不使用代理；正常能访问淘宝时请保持为空")),
                ft.ListTile(leading=ft.Icon(ft.Icons.FINGERPRINT), title=ft.Text("浏览器指纹"), subtitle=ft.Text("使用真实浏览器默认值，不随机 UA，不伪造 Canvas/WebGL")),
                ft.ListTile(leading=ft.Icon(ft.Icons.PAYMENT), title=ft.Text("支付与提交"), subtitle=ft.Text("授权后只自动提交到待付款；支付始终由用户在淘宝官方页面完成")),
                ft.Divider(),
                ft.Text("环境自检结果", size=18, weight=ft.FontWeight.BOLD),
                *result_controls,
                ft.Divider(),
                ft.Text(f"通用数据目录：{self.data_dir}", size=12, color="#64748B", selectable=True),
            ], spacing=4)),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    def _save_settings(self, chrome_path, port) -> None:
        try:
            normalized_chrome = str(chrome_path.value or "").strip()
            if normalized_chrome:
                normalized_chrome = str(find_google_chrome(normalized_chrome))
            updated = AppConfig(
                port=validate_port(port.value),
                chrome_path=normalized_chrome,
                first_run_complete=self.config.first_run_complete,
            )
            save_config(updated, self.data_dir)
            self.config = updated
            self.sessions.close_all()
            self.sessions.chrome_path = updated.chrome_path
            self._notify("配置已保存。端口变更将在下次启动时生效。")
            self._render()
        except (ValueError, BrowserLaunchError) as exc:
            self._notify(str(exc), error=True)

    def _run_environment_check(self, _event=None, *, first_run: bool = False) -> None:
        if self._environment_check_running:
            return
        self._environment_check_running = True
        if self.page is not None:
            self._notify("正在检查 Python、Chrome、数据目录、端口和网络……")

        def operation() -> None:
            try:
                self._environment_results = run_environment_checks(self.config, self.data_dir)
                if first_run and not self.config.first_run_complete:
                    self.config = replace(self.config, first_run_complete=True)
                    save_config(self.config, self.data_dir)
                failures = [item for item in self._environment_results if not item.passed]
                message = (
                    "首次运行环境自检已通过。"
                    if not failures
                    else "环境自检完成，但有项目未通过。请进入“设置”查看处理建议。"
                )
                self._notify(message, error=bool(failures))
            except Exception as exc:
                self._notify(f"环境自检失败：{exc}", error=True)
            finally:
                self._environment_check_running = False
                self._render()

        threading.Thread(target=operation, daemon=True, name="environment-check").start()

    @staticmethod
    def _status_badge(status: str) -> ft.Container:
        colors = {
            "已登录": ("#DCFCE7", "#166534"),
            "等待扫码": ("#FEF3C7", "#92400E"),
            "预检中": ("#DBEAFE", "#1D4ED8"),
            "待授权": ("#FEF3C7", "#92400E"),
            "款式预检中": ("#DBEAFE", "#1D4ED8"),
            "待核对订单": ("#FFEDD5", "#9A3412"),
            "授权检查中": ("#DBEAFE", "#1D4ED8"),
            "已武装": ("#DCFCE7", "#166534"),
            "等待中": ("#DBEAFE", "#1D4ED8"),
            "触发中": ("#EDE9FE", "#6D28D9"),
            "提交中": ("#EDE9FE", "#6D28D9"),
            "待付款": ("#DCFCE7", "#166534"),
            "已取消": ("#F1F5F9", "#475569"),
            "需人工处理": ("#FFEDD5", "#9A3412"),
            "失败": ("#FEE2E2", "#991B1B"),
        }
        background, foreground = colors.get(status, ("#F1F5F9", "#475569"))
        return ft.Container(ft.Text(status, size=12, color=foreground), bgcolor=background, padding=ft.padding.symmetric(6, 10), border_radius=20)

    def _show_add_account(self, _event) -> None:
        field = ft.TextField(label="账号备注", hint_text="例如：我的淘宝账号")
        dialog = ft.AlertDialog(title=ft.Text("添加账号"), content=field)
        dialog.actions = [ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)), ft.FilledButton("添加", on_click=lambda e: self._confirm_add_account(dialog, field))]
        self._open_dialog(dialog)

    def _confirm_add_account(self, dialog, field) -> None:
        try:
            self.store.add_account(field.value)
            self.store.log("INFO", "账号", "已添加本地账号配置", field.value.strip())
            self._close_dialog(dialog)
            self._render()
        except Exception as exc:
            self._notify(str(exc), error=True)

    def _show_add_product(self, _event) -> None:
        name = ft.TextField(label="商品名称")
        url = ft.TextField(label="淘宝/天猫商品链接")
        sku = ft.TextField(label="SKU 备注", hint_text="例如：黑色 / 256GB；最终以淘宝页面为准")
        quantity = ft.TextField(label="数量 1-5", value="1", keyboard_type=ft.KeyboardType.NUMBER)
        dialog = ft.AlertDialog(title=ft.Text("添加商品"), content=ft.Column([name, url, sku, quantity], tight=True, width=520))
        dialog.actions = [ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)), ft.FilledButton("添加", on_click=lambda e: self._confirm_add_product(dialog, name, url, sku, quantity))]
        self._open_dialog(dialog)

    def _confirm_add_product(self, dialog, name, url, sku, quantity) -> None:
        try:
            self.store.add_product(name.value, url.value, sku.value, int(quantity.value or "1"))
            self.store.log("INFO", "商品", "已添加商品", name.value.strip())
            self._close_dialog(dialog)
            self._render()
        except Exception as exc:
            self._notify(str(exc), error=True)

    def _show_add_task(self, _event) -> None:
        accounts = [account for account in self.store.list_accounts() if account["enabled"]]
        products = [product for product in self.store.list_products() if product["enabled"]]
        if not accounts or not products:
            return self._notify("请先添加并启用至少一个账号和一个商品", error=True)
        name = ft.TextField(label="任务名称", value="辅助购买任务")
        account = ft.Dropdown(label="账号", options=[ft.dropdown.Option(str(item["id"]), item["nickname"]) for item in accounts], value=str(accounts[0]["id"]))
        product = ft.Dropdown(label="商品", options=[ft.dropdown.Option(str(item["id"]), item["name"]) for item in products], value=str(products[0]["id"]))
        scheduled = ft.TextField(
            label="计划时间（可填写毫秒）",
            value=(datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S.000"),
        )
        dialog = ft.AlertDialog(title=ft.Text("创建辅助购买任务"), content=ft.Column([name, account, product, scheduled], tight=True, width=500))
        dialog.actions = [ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)), ft.FilledButton("创建", on_click=lambda e: self._confirm_add_task(dialog, name, account, product, scheduled))]
        self._open_dialog(dialog)

    def _confirm_add_task(self, dialog, name, account, product, scheduled) -> None:
        try:
            value = parse_scheduled_at(scheduled.value).isoformat(timespec="milliseconds")
            self.store.add_task(name.value, int(account.value), int(product.value), value)
            self.store.log("INFO", "任务", "已创建人工确认任务", name.value.strip())
            self._close_dialog(dialog)
            self._render()
        except Exception as exc:
            self._notify(f"任务创建失败：{exc}", error=True)

    def _qr_login(self, account_id: int) -> None:
        account = self.store.get_account(account_id)
        if not account:
            return
        self.store.set_account_status(account_id, "启动中")
        self._render()

        def operation() -> None:
            try:
                session = self.sessions.get_or_create(account)
                session.open_login()
                self.store.set_account_status(account_id, "等待扫码")
                self.store.log("INFO", "登录", "已打开淘宝登录页，请在可见 Chrome 中扫码", account["nickname"])
                self._notify("淘宝登录页已打开，请使用淘宝 App 扫码。扫码后回到这里点击“检查登录”。")
            except Exception as exc:
                self.store.set_account_status(account_id, "启动失败")
                self.store.log("ERROR", "浏览器", str(exc), account["nickname"])
                self._notify(str(exc), error=True)
            self._render()

        threading.Thread(target=operation, daemon=True).start()

    def _check_login(self, account_id: int) -> None:
        account = self.store.get_account(account_id)
        if not account:
            return

        def operation() -> None:
            try:
                logged_in = self.sessions.get_or_create(account).check_login()
                status = "已登录" if logged_in else "未登录"
                self.store.set_account_status(account_id, status)
                self.store.log("INFO", "登录", f"登录检查结果：{status}", account["nickname"])
                self._notify(f"{account['nickname']}：{status}")
            except Exception as exc:
                self.store.set_account_status(account_id, "检查失败")
                self.store.log("ERROR", "登录", str(exc), account["nickname"])
                self._notify(str(exc), error=True)
            self._render()

        threading.Thread(target=operation, daemon=True).start()

    def _toggle_account(self, account_id: int) -> None:
        self.store.toggle_account(account_id)
        self._render()

    def _choose_account_for_product(self, product_id: int) -> None:
        accounts = [account for account in self.store.list_accounts() if account["enabled"]]
        if not accounts:
            return self._notify("请先添加并启用账号", error=True)
        selector = ft.Dropdown(label="选择账号", options=[ft.dropdown.Option(str(item["id"]), item["nickname"]) for item in accounts], value=str(accounts[0]["id"]))
        dialog = ft.AlertDialog(title=ft.Text("在哪个账号中打开商品"), content=selector)
        dialog.actions = [ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)), ft.FilledButton("打开", on_click=lambda e: self._open_product(dialog, int(selector.value), product_id))]
        self._open_dialog(dialog)

    def _open_product(self, dialog, account_id: int, product_id: int) -> None:
        self._close_dialog(dialog)
        account, product = self.store.get_account(account_id), self.store.get_product(product_id)
        if not account or not product:
            return

        def operation() -> None:
            try:
                self.sessions.get_or_create(account).open_product(product["url"])
                self.store.log("INFO", "商品", "已在可见 Chrome 中打开商品", product["name"])
                self._notify("商品已打开。请在淘宝页面中人工确认 SKU、数量和价格。")
            except Exception as exc:
                self.store.log("ERROR", "商品", str(exc), product["name"])
                self._notify(str(exc), error=True)
            self._render()

        threading.Thread(target=operation, daemon=True).start()

    def _prepare_task(self, task_id: int) -> None:
        task = next((item for item in self.store.list_tasks() if int(item["id"]) == task_id), None)
        if not task:
            return
        account = self.store.get_account(int(task["account_id"]))
        if not account:
            return
        self.store.clear_task_authorization(task_id, "预检中")
        self._render()

        def operation() -> None:
            try:
                session = self.sessions.get_or_create(account)
                target_id = session.open_product(task["product_url"])
                self._task_targets[task_id] = target_id
                deadline = time.monotonic() + 15
                snapshot = None
                while time.monotonic() < deadline:
                    try:
                        snapshot = session.inspect_page(target_id)
                        if product_precheck_finished(
                            snapshot,
                            ("立即购买", "马上抢", "立即抢购", "支付定金"),
                        ):
                            break
                    except BrowserLaunchError:
                        pass
                    time.sleep(0.3)

                if snapshot is None:
                    raise BrowserLaunchError("商品页打开后未能完成页面预检。")
                if snapshot.kind in {"login", "challenge"}:
                    message = "淘宝要求重新登录或安全验证，请在账号 Chrome 中人工处理后重新准备。"
                    self.store.set_task_status(task_id, "需人工处理", message)
                    self.store.log("WARNING", "任务", message, task["name"])
                    self._notify(message, error=True)
                elif snapshot.kind != "product":
                    message = f"当前页面不是可识别的商品页：{snapshot.title or snapshot.url}"
                    self.store.set_task_status(task_id, "需人工处理", message)
                    self.store.log("WARNING", "任务", message, task["name"])
                    self._notify(message, error=True)
                elif not has_enabled_action(snapshot, ("立即购买", "马上抢", "立即抢购", "支付定金")):
                    message = "商品页已打开，但暂未发现可用的购买按钮；请检查库存、活动时间和 SKU。"
                    self.store.set_task_status(task_id, "需人工处理", message)
                    self.store.log("WARNING", "任务", message, task["name"])
                    self._notify(message, error=True)
                else:
                    self.store.set_task_status(task_id, "待授权", "")
                    self.store.log("INFO", "任务", "商品页预检通过，等待用户核对并授权", task["name"])
                    self._notify("预检通过。请回到任务中心点击“授权”，为本次任务固定 SKU、数量和款式说明。")
            except BrowserLaunchError as exc:
                self.store.set_task_status(task_id, "失败", str(exc))
                self.store.log("ERROR", "任务", str(exc), task["name"])
                self._notify(str(exc), error=True)
            self._render()

        threading.Thread(target=operation, daemon=True).start()

    def _show_authorize_task(self, task_id: int) -> None:
        task = next((item for item in self.store.list_tasks() if int(item["id"]) == task_id), None)
        if not task:
            return
        if task["status"] == "待核对订单":
            return self._show_checkout_authorization(task)
        if task["status"] != "待授权":
            return self._notify("请先点击“准备商品”并完成页面预检。", error=True)
        current_sku_id = sku_id_from_url(str(task["product_url"]))
        has_variants = ft.Checkbox(
            label="此商品有颜色、口味、尺码等可选款式，本次必须固定 SKU",
            value=bool(current_sku_id or task["product_sku_note"]),
        )
        sku_input = ft.TextField(
            label="本次款式链接或 SKU ID",
            value=current_sku_id,
            hint_text="例如：6064684260474，或粘贴已选好款式的同一商品链接",
            helper_text="程序只保留商品 ID 与 SKU ID，不保存跟踪参数。",
        )
        sku_note = ft.TextField(
            label="本次款式说明",
            value=str(task["product_sku_note"] or ""),
            hint_text="例如：气血充足月经正常面若桃花 / 2盒",
        )
        quantity = ft.Dropdown(
            label="本次数量",
            value=str(task["product_quantity"]),
            options=[ft.dropdown.Option(str(value), str(value)) for value in range(1, 6)],
        )
        confirmed = ft.Checkbox(
            label="我已核对本次商品、SKU 和数量，允许程序现在进入确认订单页；本步骤不会提交订单。",
            value=False,
        )
        scheduled = ft.TextField(
            label="本次计划时间（可修改，支持毫秒）",
            value=task["scheduled_at"].replace("T", " "),
        )
        details = ft.Column([
            ft.Text(f"商品：{task['product_name']}", weight=ft.FontWeight.BOLD),
            has_variants,
            sku_input,
            sku_note,
            quantity,
            scheduled,
            ft.Text(
                "本步骤会应用 SKU 并立即进入确认订单页，但不会提交订单。进入后请人工核对"
                "收货地址、价格、数量、号码保护和协议选项，再回来完成最终授权。",
                color="#9A3412",
            ),
            confirmed,
        ], tight=True, width=620, height=570, scroll=ft.ScrollMode.AUTO)
        dialog = ft.AlertDialog(title=ft.Text("第一步：准备确认订单"), content=details)
        dialog.actions = [
            ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)),
            ft.FilledButton(
                "应用款式并进入确认订单",
                on_click=lambda e: self._confirm_authorize_task(
                    dialog,
                    task_id,
                    confirmed,
                    scheduled,
                    has_variants,
                    sku_input,
                    sku_note,
                    quantity,
                ),
            ),
        ]
        self._open_dialog(dialog)

    def _confirm_authorize_task(
        self,
        dialog,
        task_id: int,
        confirmed,
        scheduled,
        has_variants,
        sku_input,
        sku_note,
        quantity,
    ) -> None:
        if not confirmed.value:
            return self._notify("请先勾选授权确认。", error=True)
        if self._task_cancel_events:
            return self._notify("单账号阶段一次只能运行一个任务，请先停止当前任务。", error=True)
        task = next((item for item in self.store.list_tasks() if int(item["id"]) == task_id), None)
        if not task or task["status"] != "待授权":
            return self._notify("任务状态已经变化，请重新准备。", error=True)
        try:
            scheduled_at = parse_scheduled_at(scheduled.value)
            selected_url = resolve_product_selection(task["product_url"], sku_input.value)
            selected_quantity = int(quantity.value)
        except ValueError as exc:
            return self._notify(str(exc), error=True)
        selected_sku_id = sku_id_from_url(selected_url)
        if has_variants.value and not selected_sku_id:
            return self._notify("该商品已标记为多款式，请填写本次 SKU ID 或粘贴已选款式的商品链接。", error=True)
        account = self.store.get_account(int(task["account_id"]))
        if not account:
            return self._notify("任务账号不存在，请重新创建任务。", error=True)
        self.store.set_task_selection(
            task_id,
            selected_url,
            sku_note.value,
            selected_quantity,
        )
        self.store.set_task_schedule(task_id, scheduled_at.isoformat(timespec="milliseconds"))
        self.store.set_task_status(task_id, "款式预检中", "")
        self._close_dialog(dialog)
        self._notify("正在应用本次款式，并进入确认订单页供你人工核对……")
        self._render()

        def operation() -> None:
            try:
                session = self.sessions.get_or_create(account)
                target_id = session.navigate_product(selected_url, self._task_targets.get(task_id))
                self._task_targets[task_id] = target_id
                deadline = time.monotonic() + 15
                snapshot = None
                while time.monotonic() < deadline:
                    try:
                        snapshot = session.inspect_page(target_id)
                        if product_precheck_finished(snapshot, ("立即购买", "马上抢", "立即抢购", "支付定金")):
                            break
                    except BrowserLaunchError:
                        pass
                    time.sleep(0.3)
                if snapshot is None:
                    raise BrowserLaunchError("应用本次款式后未能读取商品页面。")
                if snapshot.kind in {"login", "challenge"}:
                    raise BrowserLaunchError("应用本次款式时出现登录或安全验证，请人工处理后重新准备。")
                if snapshot.kind != "product" or not has_enabled_action(
                    snapshot,
                    BUY_ACTIONS,
                ):
                    raise BrowserLaunchError("本次 SKU 当前不可购买，请检查款式、库存和活动时间。")
                auxiliary_pages = session.auxiliary_pages()
                if auxiliary_pages:
                    raise BrowserLaunchError(
                        "检测到仍打开的隐私或协议说明页。请先关闭这些标签，再重新执行本步骤。"
                    )
                click_result = session.click_action(BUY_ACTIONS, target_id)
                if not click_result.get("clicked"):
                    reason = str(click_result.get("reason") or "not_found")
                    raise BrowserLaunchError(f"未能安全点击“立即购买”进入确认订单页（{reason}）。")

                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    auxiliary_pages = session.auxiliary_pages()
                    if auxiliary_pages:
                        raise BrowserLaunchError(
                            "进入确认订单时打开了隐私或协议说明页。请关闭该标签，返回商品页后重新准备。"
                        )
                    snapshot = session.inspect_page(target_id)
                    if snapshot.kind in {"confirm_order", "pending_payment", "payment_error", "login", "challenge"}:
                        break
                    time.sleep(0.2)
                if snapshot.kind in {"login", "challenge"}:
                    raise BrowserLaunchError("进入确认订单时出现登录或安全验证，请人工处理后重新准备。")
                if snapshot.kind != "confirm_order":
                    raise BrowserLaunchError("点击购买后未进入确认订单页，请检查商品状态并重新准备。")

                self.store.set_task_status(task_id, "待核对订单", "")
                sku_summary = f"SKU {selected_sku_id}" if selected_sku_id else "无独立 SKU"
                note_summary = sku_note.value.strip() or "未填写款式说明"
                self.store.log(
                    "INFO",
                    "任务",
                    f"已进入确认订单页，等待人工核对：{sku_summary}；{note_summary}；数量 {selected_quantity}",
                    task["name"],
                )
                self._notify(
                    "已进入确认订单页。请在 Chrome 中核对地址、价格、数量、号码保护和协议选项；"
                    "关闭所有隐私/规则说明标签后，回到任务中心点击“核对后授权”。"
                )
                self._render()
            except (BrowserLaunchError, ValueError) as exc:
                self.store.set_task_status(task_id, "需人工处理", str(exc))
                self.store.log("WARNING", "任务", str(exc), task["name"])
                self._notify(str(exc), error=True)
                self._render()
            except Exception as exc:
                message = f"应用本次款式失败：{exc}"
                self.store.set_task_status(task_id, "失败", message)
                self.store.log("ERROR", "任务", message, task["name"])
                self._notify(message, error=True)
                self._render()

        threading.Thread(target=operation, daemon=True, name=f"sku-precheck-{task_id}").start()

    def _show_checkout_authorization(self, task: dict) -> None:
        task_id = int(task["id"])
        scheduled = ft.TextField(
            label="本次计划时间（可修改，支持毫秒）",
            value=task["scheduled_at"].replace("T", " "),
        )
        confirmed = ft.Checkbox(
            label=(
                "我已在确认订单页人工核对收货地址、商品款式、数量、价格、号码保护和协议选项，"
                "并确认页面中的“提交订单”按钮可用。"
            ),
            value=False,
        )
        details = ft.Column(
            [
                ft.Text(f"商品：{task['product_name']}", weight=ft.FontWeight.BOLD),
                ft.Text(f"款式：{task['product_sku_note'] or '未填写'}；数量：{task['product_quantity']}"),
                scheduled,
                ft.Container(
                    content=ft.Text(
                        "请先关闭 Chrome 中所有“隐私号保护规则说明”“协议”“规则”等辅助标签，"
                        "并停留在确认订单页。程序不会代替你同意协议，也不会自动支付。",
                        color="#9A3412",
                    ),
                    bgcolor="#FFF7ED",
                    padding=12,
                    border_radius=8,
                ),
                confirmed,
            ],
            tight=True,
            width=620,
            height=390,
            scroll=ft.ScrollMode.AUTO,
        )
        dialog = ft.AlertDialog(title=ft.Text("第二步：核对确认订单并授权"), content=details)
        dialog.actions = [
            ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)),
            ft.FilledButton(
                "确认并开始等待",
                on_click=lambda e: self._confirm_checkout_authorization(
                    dialog,
                    task_id,
                    confirmed,
                    scheduled,
                ),
            ),
        ]
        self._open_dialog(dialog)

    def _confirm_checkout_authorization(
        self,
        dialog,
        task_id: int,
        confirmed,
        scheduled,
    ) -> None:
        if not confirmed.value:
            return self._notify("请先完成确认订单页的人工核对并勾选确认。", error=True)
        if self._task_cancel_events:
            return self._notify("单账号阶段一次只能运行一个任务，请先停止当前任务。", error=True)
        task = self.store.get_task(task_id)
        if not task or task["status"] != "待核对订单":
            return self._notify("任务状态已经变化，请重新准备。", error=True)
        try:
            scheduled_at = parse_scheduled_at(scheduled.value)
        except ValueError as exc:
            return self._notify(str(exc), error=True)
        if scheduled_at <= datetime.now() + timedelta(seconds=2):
            return self._notify("计划时间至少要晚于当前时间 2 秒，请修改后再授权。", error=True)
        account = self.store.get_account(int(task["account_id"]))
        if not account:
            return self._notify("任务账号不存在，请重新创建任务。", error=True)

        self.store.set_task_schedule(task_id, scheduled_at.isoformat(timespec="milliseconds"))
        self.store.set_task_status(task_id, "授权检查中", "")
        self._close_dialog(dialog)
        self._notify("正在检查确认订单页和隐私/协议标签……")
        self._render()

        def operation() -> None:
            try:
                session = self.sessions.get_or_create(account)
                auxiliary_pages = session.auxiliary_pages()
                if auxiliary_pages:
                    raise BrowserLaunchError(
                        "检测到隐私、协议或规则说明标签仍然打开。请关闭这些标签，返回确认订单页处理相关选项，"
                        "然后再次点击“核对后授权”。"
                    )
                snapshot = session.inspect_page(self._task_targets.get(task_id))
                if snapshot.kind in {"login", "challenge"}:
                    raise BrowserLaunchError("最终授权时出现登录或安全验证，请人工处理后重新核对。")
                if snapshot.kind != "confirm_order":
                    raise BrowserLaunchError("当前不是确认订单页；确认页可能已失效，请重新点击“准备商品”。")
                if not has_enabled_action(snapshot, SUBMIT_ACTIONS):
                    raise BrowserLaunchError(
                        "确认订单页的“提交订单”按钮不可用。请检查地址、价格、号码保护或协议选项后重试。"
                    )
                if scheduled_at <= datetime.now() + timedelta(seconds=2):
                    raise BrowserLaunchError("页面检查完成时计划时间已过，请修改时间后再次授权。")

                self.store.authorize_task(task_id)
                self.store.log(
                    "INFO",
                    "任务",
                    "确认订单页人工核对完成，已授权到点仅点击提交订单",
                    task["name"],
                )
                self._notify("确认订单页检查通过，任务已武装；到点只会提交订单，不会自动支付。")
                self._render()
                self._start_task_runner(task_id)
            except (BrowserLaunchError, ValueError) as exc:
                self.store.set_task_status(task_id, "待核对订单", str(exc))
                self.store.log("WARNING", "任务", str(exc), task["name"])
                self._notify(str(exc), error=True)
                self._render()
            except Exception as exc:
                message = f"最终授权检查失败：{exc}"
                self.store.set_task_status(task_id, "待核对订单", message)
                self.store.log("ERROR", "任务", message, task["name"])
                self._notify(message, error=True)
                self._render()

        threading.Thread(target=operation, daemon=True, name=f"checkout-authorization-{task_id}").start()

    def _start_task_runner(self, task_id: int) -> None:
        cancel_event = threading.Event()
        self._task_cancel_events[task_id] = cancel_event

        def operation() -> None:
            outcome = self.runner.run(task_id, self._task_targets.get(task_id), cancel_event)
            self._task_cancel_events.pop(task_id, None)
            self._notify(outcome.message, error=outcome.status in {"失败", "需人工处理"})
            self._render()

        threading.Thread(target=operation, daemon=True, name=f"task-runner-{task_id}").start()

    def _cancel_task(self, task_id: int) -> None:
        cancel_event = self._task_cancel_events.get(task_id)
        if cancel_event is None:
            self.store.set_task_status(task_id, "已取消", "任务未在当前程序实例中运行。")
            self._render()
            return
        cancel_event.set()
        self._notify("已发送停止请求。")

    def _open_task_cart(self, task_id: int) -> None:
        task = next((item for item in self.store.list_tasks() if int(item["id"]) == task_id), None)
        if not task:
            return
        account = self.store.get_account(int(task["account_id"]))
        if not account:
            return

        def operation() -> None:
            try:
                self.sessions.get_or_create(account).open_cart()
                self.store.log("INFO", "任务", "已打开购物车，等待用户人工核对并结算", task["name"])
                self._notify("购物车已打开。请人工核对勾选商品、数量、地址和价格，再决定是否结算。")
            except Exception as exc:
                self.store.log("ERROR", "任务", str(exc), task["name"])
                self._notify(str(exc), error=True)
            self._render()

        threading.Thread(target=operation, daemon=True).start()

    def _open_dialog(self, dialog) -> None:
        self.page.open(dialog)

    def _close_dialog(self, dialog) -> None:
        self.page.close(dialog)

    def _notify(self, message: str, error: bool = False) -> None:
        self.page.open(
            ft.SnackBar(
                ft.Text(message),
                bgcolor="#B91C1C" if error else "#1D4ED8",
            )
        )
