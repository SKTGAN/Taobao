from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.v2_store import TASK_MODE_FLASH, V2Store


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

    def test_account_can_be_renamed_and_deleted_without_removing_profile(self) -> None:
        account_id = self.store.add_account("旧备注")
        profile_dir = Path(self.store.get_account(account_id)["profile_dir"])
        self.store.update_account(account_id, "新备注")
        self.assertEqual(self.store.get_account(account_id)["nickname"], "新备注")
        retained = self.store.delete_account(account_id)
        self.assertEqual(Path(retained), profile_dir)
        self.assertTrue(profile_dir.is_dir())
        self.assertIsNone(self.store.get_account(account_id))

    def test_account_with_task_cannot_be_deleted(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        with self.assertRaisesRegex(ValueError, "关联任务"):
            self.store.delete_account(account_id)

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

    def test_product_management_update_duplicate_and_toggle(self) -> None:
        product_id = self.store.add_product(
            "商品A",
            "https://item.taobao.com/item.htm?id=1",
            "旧款式",
            1,
        )
        self.store.update_product(
            product_id,
            "商品B",
            "https://item.taobao.com/item.htm?id=1&skuId=606&spm=tracking",
            "新款式",
            3,
        )
        updated = self.store.get_product(product_id)
        self.assertEqual(updated["name"], "商品B")
        self.assertEqual(updated["url"], "https://item.taobao.com/item.htm?id=1&skuId=606")
        self.assertEqual(updated["sku_note"], "新款式")
        self.assertEqual(updated["quantity"], 3)

        duplicated_id = self.store.duplicate_product(product_id)
        duplicated = self.store.get_product(duplicated_id)
        self.assertEqual(duplicated["name"], "商品B - 副本")
        self.assertEqual(duplicated["url"], updated["url"])

        self.store.toggle_product(product_id)
        self.assertEqual(self.store.get_product(product_id)["enabled"], 0)

    def test_product_delete_requires_related_task_to_be_deleted_first(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        with self.assertRaisesRegex(ValueError, "关联任务"):
            self.store.delete_product(product_id)
        self.store.delete_task(task_id)
        self.store.delete_product(product_id)
        self.assertIsNone(self.store.get_product(product_id))

    def test_running_task_must_be_stopped_before_delete(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task("任务A", account_id, product_id, "2026-07-13T20:00:00")
        self.store.authorize_task(task_id)
        with self.assertRaisesRegex(ValueError, "先停止"):
            self.store.delete_task(task_id)

    def test_stores_flash_purchase_mode(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task(
            "开售任务",
            account_id,
            product_id,
            "2026-07-13T20:00:00",
            TASK_MODE_FLASH,
        )
        self.assertEqual(self.store.get_task(task_id)["mode"], TASK_MODE_FLASH)

    def test_task_friend_pay_address_and_edit_round_trip(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        task_id = self.store.add_task(
            "代付任务",
            account_id,
            product_id,
            "2026-07-13T20:00:00",
            TASK_MODE_FLASH,
            "张三 9364",
            True,
            "13800138000",
        )
        task = self.store.get_task(task_id)
        self.assertEqual(task["address_keyword"], "张三 9364")
        self.assertEqual(task["friend_pay_enabled"], 1)
        self.assertEqual(task["friend_pay_account"], "13800138000")

        self.store.set_task_status(task_id, "失败", "旧错误")
        self.store.update_task(
            task_id,
            "已编辑任务",
            account_id,
            product_id,
            "2026-07-13T20:01:00.125",
            TASK_MODE_FLASH,
            "",
            False,
            "",
        )
        updated = self.store.get_task(task_id)
        self.assertEqual(updated["name"], "已编辑任务")
        self.assertEqual(updated["status"], "草稿")
        self.assertEqual(updated["last_error"], "")
        self.assertEqual(updated["friend_pay_enabled"], 0)

    def test_friend_pay_requires_valid_mobile_number(self) -> None:
        account_id = self.store.add_account("账号A")
        product_id = self.store.add_product("商品A", "https://item.taobao.com/item.htm?id=1")
        with self.assertRaisesRegex(ValueError, "有效的中国大陆手机号"):
            self.store.add_task(
                "错误代付",
                account_id,
                product_id,
                "2026-07-13T20:00:00",
                TASK_MODE_FLASH,
                "",
                True,
                "123",
            )


if __name__ == "__main__":
    unittest.main()

