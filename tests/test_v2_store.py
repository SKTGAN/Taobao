from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.v2_store import V2Store


class V2StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = V2Store(Path(self.temp_dir.name) / "assistant.db")

    def test_account_profile_contains_no_password(self) -> None:
        account_id = self.store.add_account("测试账号")
        account = self.store.get_account(account_id)
        self.assertEqual(account["nickname"], "测试账号")
        self.assertTrue(Path(account["profile_dir"]).is_dir())
        self.assertNotIn("password", account)

    def test_product_and_task_round_trip(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product(
            "测试商品",
            "https://item.taobao.com/item.htm?id=1",
            "黑色 / 256GB",
            2,
        )
        task_id = self.store.add_task(
            "辅助任务",
            account_id,
            product_id,
            "2026-07-12T20:00:00",
        )
        task = self.store.list_tasks()[0]
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["account_name"], "账号A")
        self.assertEqual(task["product_name"], "测试商品")
        self.assertEqual(task["mode"], "人工确认")
        self.assertEqual(task["authorized_at"], "")

    def test_authorize_and_reset_task(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        self.store.authorize_task(task_id)
        task = self.store.list_tasks()[0]
        self.assertEqual(task["status"], "已武装")
        self.assertTrue(task["authorized_at"])
        self.store.clear_task_authorization(task_id, "预检中")
        task = self.store.list_tasks()[0]
        self.assertEqual(task["status"], "预检中")
        self.assertEqual(task["authorized_at"], "")

    def test_updates_task_schedule_before_authorization(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        self.store.set_task_schedule(task_id, "2026-07-13T20:00:00.125")
        self.assertEqual(self.store.get_task(task_id)["scheduled_at"], "2026-07-13T20:00:00.125")

    def test_stores_task_specific_style_and_quantity(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        self.store.set_task_selection(
            task_id,
            "https://item.taobao.com/item.htm?id=1&skuId=606",
            "红色 / 大号",
            2,
        )
        task = self.store.get_task(task_id)
        self.assertEqual(task["product_url"], "https://item.taobao.com/item.htm?id=1&skuId=606")
        self.assertEqual(task["product_sku_note"], "红色 / 大号")
        self.assertEqual(task["product_quantity"], 2)

    def test_updates_product_url_with_sku(self) -> None:
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        self.store.update_product_url(
            product_id,
            "https://item.taobao.com/item.htm?id=1&skuId=606&spm=tracking",
        )
        self.assertEqual(
            self.store.get_product(product_id)["url"],
            "https://item.taobao.com/item.htm?id=1&skuId=606",
        )

    def test_restart_requires_fresh_authorization(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        self.store.authorize_task(task_id)
        reopened = V2Store(self.store.db_path)
        task = reopened.get_task(task_id)
        self.assertEqual(task["status"], "需重新准备")
        self.assertEqual(task["authorized_at"], "")

    def test_restart_invalidates_unfinished_checkout_review(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        self.store.set_task_status(task_id, "待核对订单", "")
        reopened = V2Store(self.store.db_path)
        task = reopened.get_task(task_id)
        self.assertEqual(task["status"], "需重新准备")

    def test_rejects_unrelated_product_url(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_product("错误链接", "https://example.com/item")

    def test_stores_canonical_product_url(self) -> None:
        product_id = self.store.add_product(
            "测试商品",
            "https://item.taobao.com/item.htm?abbucket=11&id=987250846319&spm=test",
        )
        product = self.store.get_product(product_id)
        self.assertEqual(
            product["url"],
            "https://item.taobao.com/item.htm?id=987250846319",
        )


if __name__ == "__main__":
    unittest.main()

