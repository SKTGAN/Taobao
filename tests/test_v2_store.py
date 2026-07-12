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

    def test_rejects_unrelated_product_url(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_product("错误链接", "https://example.com/item")


if __name__ == "__main__":
    unittest.main()

