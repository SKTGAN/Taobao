from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from src.gui_v2 import FletGUI
from src.v2_store import TASK_MODE_FLASH


class _FakePage:
    def __init__(self) -> None:
        self.opened: list[object] = []

    def open(self, control) -> None:
        self.opened.append(control)

    def close(self, _control) -> None:
        return None


class AuthorizationDialogTests(unittest.TestCase):
    def test_task_operation_lock_blocks_parallel_prepare_and_running_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gui = FletGUI(data_dir=Path(temp_dir))
            self.assertTrue(gui._begin_task_operation(1))
            self.assertFalse(gui._begin_task_operation(2))
            gui._finish_task_operation(1)
            gui._task_cancel_events[1] = threading.Event()
            self.assertFalse(gui._begin_task_operation(2))

    def test_prefills_task_sku_note_and_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gui = FletGUI(data_dir=Path(temp_dir))
            gui.page = _FakePage()
            account_id = gui.store.add_account("账号A")
            product_id = gui.store.add_product(
                "多款式商品",
                "https://item.taobao.com/item.htm?id=1042716758379&skuId=6064684260474",
                "气血充足 / 2盒",
                2,
            )
            task_id = gui.store.add_task(
                "授权弹窗测试",
                account_id,
                product_id,
                "2099-07-15T12:00:00.000",
            )
            gui.store.set_task_status(task_id, "待授权", "")

            gui._show_authorize_task(task_id)

            self.assertEqual(len(gui.page.opened), 1)
            dialog = gui.page.opened[0]
            controls = dialog.content.controls
            self.assertTrue(controls[1].value)
            self.assertEqual(controls[2].value, "6064684260474")
            self.assertEqual(controls[3].value, "气血充足 / 2盒")
            self.assertEqual(controls[4].value, "2")

    def test_waiting_checkout_uses_second_stage_authorization_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gui = FletGUI(data_dir=Path(temp_dir))
            gui.page = _FakePage()
            account_id = gui.store.add_account("账号A")
            product_id = gui.store.add_product(
                "多款式商品",
                "https://item.taobao.com/item.htm?id=1042716758379&skuId=6064684260474",
                "1盒试用装",
                1,
            )
            task_id = gui.store.add_task(
                "确认订单授权测试",
                account_id,
                product_id,
                "2099-07-15T12:00:00.000",
            )
            gui.store.set_task_status(task_id, "待核对订单", "")

            gui._show_authorize_task(task_id)

            self.assertEqual(len(gui.page.opened), 1)
            dialog = gui.page.opened[0]
            self.assertEqual(dialog.title.value, "第二步：核对确认订单并授权")
            self.assertIn("号码保护", dialog.content.controls[4].label)
            self.assertIn("代理/VPN", dialog.content.controls[6].label)

    def test_flash_task_uses_product_page_authorization_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gui = FletGUI(data_dir=Path(temp_dir))
            gui.page = _FakePage()
            account_id = gui.store.add_account("账号A")
            product_id = gui.store.add_product(
                "开售商品",
                "https://item.taobao.com/item.htm?id=1042716758379&skuId=6064684260474",
                "套餐1",
                1,
            )
            task_id = gui.store.add_task(
                "商品页抢购授权测试",
                account_id,
                product_id,
                "2099-07-15T12:00:00.000",
                TASK_MODE_FLASH,
            )
            gui.store.set_task_status(task_id, "待授权", "")

            gui._show_authorize_task(task_id)

            self.assertEqual(len(gui.page.opened), 1)
            dialog = gui.page.opened[0]
            self.assertEqual(dialog.title.value, "商品页定时抢购：核对并授权")
            self.assertIn("到点后程序只检查", dialog.content.controls[5].content.value)


if __name__ == "__main__":
    unittest.main()
