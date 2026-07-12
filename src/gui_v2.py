from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import flet as ft

from src.paths import PROJECT_ROOT
from src.safe_browser import BrowserLaunchError, BrowserSessionManager
from src.v2_store import V2Store


class FletGUI:
    APP_NAME = "TaoBao Assistant V2"

    def __init__(self, _legacy_config_path: Path | None = None):
        self.store = V2Store(PROJECT_ROOT / "data" / "taobao_assistant_v2.db")
        self.sessions = BrowserSessionManager()
        self.page: ft.Page | None = None
        self.content: ft.Container | None = None
        self.rail: ft.NavigationRail | None = None
        self._selected_index = 0

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
        ready = sum(1 for task in tasks if task["status"] in {"待准备", "已准备"})
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
                ft.Text("1. 添加账号备注 → 2. 打开扫码登录 → 3. 添加商品 → 4. 创建辅助任务 → 5. 在可见 Chrome 中确认 SKU、地址和价格。", color="#475569"),
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.SECURITY, color="#166534"),
                        ft.Text("不会保存淘宝密码，不处理验证码，不自动支付，最终提交由你确认。", color="#166534", weight=ft.FontWeight.W_500),
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
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(task["name"], weight=ft.FontWeight.W_500)),
                ft.DataCell(ft.Text(task["account_name"])),
                ft.DataCell(ft.Text(task["product_name"])),
                ft.DataCell(ft.Text(task["scheduled_at"].replace("T", " "))),
                ft.DataCell(self._status_badge(task["status"])),
                ft.DataCell(ft.Row([
                    ft.FilledTonalButton("打开商品", icon=ft.Icons.PLAY_ARROW, on_click=lambda e, tid=task_id: self._prepare_task(tid)),
                    ft.TextButton("打开购物车", icon=ft.Icons.SHOPPING_CART_OUTLINED, on_click=lambda e, tid=task_id: self._open_task_cart(tid)),
                ], spacing=4)),
            ]))
        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("任务")), ft.DataColumn(ft.Text("账号")), ft.DataColumn(ft.Text("商品")), ft.DataColumn(ft.Text("计划时间")), ft.DataColumn(ft.Text("状态")), ft.DataColumn(ft.Text("操作"))],
            rows=rows,
            column_spacing=24,
        ) if rows else ft.Text("还没有任务。任务只负责打开商品和记录准备状态，不会自动付款。", color="#64748B")
        return ft.Column([
            self._header("任务中心", "首版为辅助购买模式：可见浏览器 + 人工确认", ft.FilledButton("创建任务", icon=ft.Icons.ADD_TASK, on_click=self._show_add_task)),
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
        return ft.Column([
            self._header("设置", "当前版本使用保守、稳定的浏览器策略"),
            self._card(ft.Column([
                ft.ListTile(leading=ft.Icon(ft.Icons.CHROME_READER_MODE), title=ft.Text("Google Chrome"), subtitle=ft.Text("直接启动本机 Chrome；V2 不需要 ChromeDriver 或 Selenium Manager")),
                ft.ListTile(leading=ft.Icon(ft.Icons.LAN), title=ft.Text("代理"), subtitle=ft.Text("默认不使用代理；正常能访问淘宝时请保持为空")),
                ft.ListTile(leading=ft.Icon(ft.Icons.FINGERPRINT), title=ft.Text("浏览器指纹"), subtitle=ft.Text("使用真实浏览器默认值，不随机 UA，不伪造 Canvas/WebGL")),
                ft.ListTile(leading=ft.Icon(ft.Icons.PAYMENT), title=ft.Text("支付与提交"), subtitle=ft.Text("不保存支付信息；最终提交和支付由用户在淘宝页面确认")),
                ft.Divider(),
                ft.Text(f"本地数据：{self.store.db_path}", size=12, color="#64748B"),
            ], spacing=4)),
        ], spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)

    @staticmethod
    def _status_badge(status: str) -> ft.Container:
        colors = {"已登录": ("#DCFCE7", "#166534"), "等待扫码": ("#FEF3C7", "#92400E"), "已准备": ("#DBEAFE", "#1D4ED8"), "失败": ("#FEE2E2", "#991B1B")}
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
        scheduled = ft.TextField(label="计划时间", value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        dialog = ft.AlertDialog(title=ft.Text("创建辅助购买任务"), content=ft.Column([name, account, product, scheduled], tight=True, width=500))
        dialog.actions = [ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)), ft.FilledButton("创建", on_click=lambda e: self._confirm_add_task(dialog, name, account, product, scheduled))]
        self._open_dialog(dialog)

    def _confirm_add_task(self, dialog, name, account, product, scheduled) -> None:
        try:
            value = datetime.strptime(scheduled.value.strip(), "%Y-%m-%d %H:%M:%S").isoformat()
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
        self.store.set_task_status(task_id, "待准备")
        self._render()

        def operation() -> None:
            try:
                session = self.sessions.get_or_create(account)
                session.open_product(task["product_url"])
                self.store.set_task_status(task_id, "已准备")
                self.store.log("INFO", "任务", "商品页已打开，等待用户人工确认", task["name"])
                self._notify("商品页已打开。请人工选择 SKU；需要结算时在淘宝页面操作购物车。")
            except BrowserLaunchError as exc:
                self.store.set_task_status(task_id, "失败")
                self.store.log("ERROR", "任务", str(exc), task["name"])
                self._notify(str(exc), error=True)
            self._render()

        threading.Thread(target=operation, daemon=True).start()

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
