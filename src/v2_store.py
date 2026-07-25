from __future__ import annotations

import sqlite3
import re
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from src.product_urls import normalize_product_url


TASK_MODE_CHECKOUT = "确认页定时提交"
TASK_MODE_FLASH = "商品页定时抢购"
TASK_MODES = {"人工确认", TASK_MODE_CHECKOUT, TASK_MODE_FLASH}


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname TEXT NOT NULL UNIQUE,
    profile_dir TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT '未登录',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    sku_note TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity BETWEEN 1 AND 5),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '草稿',
    mode TEXT NOT NULL DEFAULT '人工确认',
    authorized_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    triggered_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    selected_url TEXT NOT NULL DEFAULT '',
    selected_sku_note TEXT NOT NULL DEFAULT '',
    selected_quantity INTEGER NOT NULL DEFAULT 0,
    address_keyword TEXT NOT NULL DEFAULT '',
    friend_pay_enabled INTEGER NOT NULL DEFAULT 0,
    friend_pay_account TEXT NOT NULL DEFAULT '',
    friend_pay_requested_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class V2Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_dir = self.db_path.parent / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            with connection:
                connection.executescript(SCHEMA)
                self._migrate_tasks(connection)
                connection.execute(
                    "UPDATE accounts SET status='未登录',updated_at=? WHERE status IN ('启动中','检查中')",
                    (now_iso(),),
                )
                connection.execute(
                    """UPDATE tasks SET status='需重新准备',authorized_at='',
                       last_error='程序重启后需重新预检并授权',updated_at=?
                       WHERE status IN (
                           '款式预检中','待核对订单','授权检查中',
                           '已武装','等待中','触发中','提交中','代付申请中'
                       )""",
                    (now_iso(),),
                )

    @staticmethod
    def _migrate_tasks(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)")}
        additions = {
            "mode": "TEXT NOT NULL DEFAULT '人工确认'",
            "authorized_at": "TEXT NOT NULL DEFAULT ''",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "triggered_at": "TEXT NOT NULL DEFAULT ''",
            "completed_at": "TEXT NOT NULL DEFAULT ''",
            "selected_url": "TEXT NOT NULL DEFAULT ''",
            "selected_sku_note": "TEXT NOT NULL DEFAULT ''",
            "selected_quantity": "INTEGER NOT NULL DEFAULT 0",
            "address_keyword": "TEXT NOT NULL DEFAULT ''",
            "friend_pay_enabled": "INTEGER NOT NULL DEFAULT 0",
            "friend_pay_account": "TEXT NOT NULL DEFAULT ''",
            "friend_pay_requested_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute(sql, params)
                return int(cursor.lastrowid)

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def add_account(self, nickname: str) -> int:
        nickname = nickname.strip()
        if not nickname:
            raise ValueError("账号备注不能为空")
        profile_dir = self.profiles_dir / f"account-{uuid.uuid4().hex[:12]}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_iso()
        return self._execute(
            "INSERT INTO accounts(nickname,profile_dir,created_at,updated_at) VALUES(?,?,?,?)",
            (nickname, str(profile_dir), stamp, stamp),
        )

    def list_accounts(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM accounts ORDER BY enabled DESC, id DESC")

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM accounts WHERE id=?", (account_id,))
        return rows[0] if rows else None

    def set_account_status(self, account_id: int, status: str) -> None:
        self._execute(
            "UPDATE accounts SET status=?,updated_at=? WHERE id=?",
            (status, now_iso(), account_id),
        )

    def toggle_account(self, account_id: int) -> None:
        self._execute(
            "UPDATE accounts SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END,updated_at=? WHERE id=?",
            (now_iso(), account_id),
        )

    def update_account(self, account_id: int, nickname: str) -> None:
        nickname = str(nickname or "").strip()
        if not nickname:
            raise ValueError("账号备注不能为空")
        self._execute(
            "UPDATE accounts SET nickname=?,updated_at=? WHERE id=?",
            (nickname, now_iso(), account_id),
        )

    def delete_account(self, account_id: int) -> str:
        account = self.get_account(account_id)
        if not account:
            return ""
        with closing(self.connect()) as connection:
            task_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE account_id=?",
                    (account_id,),
                ).fetchone()[0]
            )
            if task_count:
                raise ValueError(f"该账号仍有关联任务 {task_count} 个，请先删除关联任务或停用账号")
            with connection:
                connection.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        # Chrome profile contains the persistent login. Keep it as a local
        # backup instead of deleting user session data automatically.
        return str(account["profile_dir"])

    def add_product(self, name: str, url: str, sku_note: str = "", quantity: int = 1) -> int:
        name, url = name.strip(), url.strip()
        if not name or not url:
            raise ValueError("商品名称和链接不能为空")
        url = normalize_product_url(url)
        stamp = now_iso()
        return self._execute(
            "INSERT INTO products(name,url,sku_note,quantity,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (name, url, sku_note.strip(), max(1, min(5, int(quantity))), stamp, stamp),
        )

    def list_products(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM products ORDER BY enabled DESC, id DESC")

    def get_product(self, product_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM products WHERE id=?", (product_id,))
        return rows[0] if rows else None

    def update_product(
        self,
        product_id: int,
        name: str,
        url: str,
        sku_note: str = "",
        quantity: int = 1,
    ) -> None:
        name = name.strip()
        if not name or not str(url or "").strip():
            raise ValueError("商品名称和链接不能为空")
        normalized_url = normalize_product_url(url)
        normalized_quantity = int(quantity)
        if not 1 <= normalized_quantity <= 5:
            raise ValueError("商品数量必须在 1-5 之间")
        self._execute(
            """UPDATE products SET name=?,url=?,sku_note=?,quantity=?,updated_at=?
               WHERE id=?""",
            (
                name,
                normalized_url,
                sku_note.strip(),
                normalized_quantity,
                now_iso(),
                product_id,
            ),
        )

    def duplicate_product(self, product_id: int) -> int:
        product = self.get_product(product_id)
        if not product:
            raise ValueError("商品不存在")
        return self.add_product(
            f"{product['name']} - 副本",
            str(product["url"]),
            str(product["sku_note"]),
            int(product["quantity"]),
        )

    def toggle_product(self, product_id: int) -> None:
        self._execute(
            "UPDATE products SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END,updated_at=? WHERE id=?",
            (now_iso(), product_id),
        )

    def delete_product(self, product_id: int) -> None:
        with closing(self.connect()) as connection:
            task_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE product_id=?",
                    (product_id,),
                ).fetchone()[0]
            )
            if task_count:
                raise ValueError(f"该商品仍有关联任务 {task_count} 个，请先删除关联任务或停用商品")
            with connection:
                connection.execute("DELETE FROM products WHERE id=?", (product_id,))

    def add_task(
        self,
        name: str,
        account_id: int,
        product_id: int,
        scheduled_at: str,
        mode: str = "人工确认",
        address_keyword: str = "",
        friend_pay_enabled: bool = False,
        friend_pay_account: str = "",
    ) -> int:
        mode = str(mode or "").strip()
        if mode not in TASK_MODES:
            raise ValueError("不支持的任务模式")
        address_keyword = str(address_keyword or "").strip()
        friend_pay_account = self._validate_friend_pay(
            friend_pay_enabled,
            friend_pay_account,
        )
        stamp = now_iso()
        return self._execute(
            """INSERT INTO tasks(
                   name,account_id,product_id,scheduled_at,mode,address_keyword,
                   friend_pay_enabled,friend_pay_account,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                name.strip() or "辅助购买任务",
                account_id,
                product_id,
                scheduled_at,
                mode,
                address_keyword,
                int(bool(friend_pay_enabled)),
                friend_pay_account,
                stamp,
                stamp,
            ),
        )

    @staticmethod
    def _validate_friend_pay(enabled: bool, account: str) -> str:
        normalized = re.sub(r"\s+", "", str(account or ""))
        if not enabled:
            return ""
        if not re.fullmatch(r"1[3-9]\d{9}", normalized):
            raise ValueError("朋友代付账号必须填写有效的中国大陆手机号")
        return normalized

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._query(f"{self._task_select()} ORDER BY t.id DESC")

    @staticmethod
    def _task_select() -> str:
        return """SELECT t.*,a.nickname AS account_name,p.name AS product_name,
                         CASE WHEN t.selected_url<>'' THEN t.selected_url ELSE p.url END AS product_url,
                         CASE WHEN t.selected_sku_note<>'' THEN t.selected_sku_note ELSE p.sku_note END AS product_sku_note,
                         CASE WHEN t.selected_quantity>0 THEN t.selected_quantity ELSE p.quantity END AS product_quantity
                  FROM tasks t JOIN accounts a ON a.id=t.account_id
                  JOIN products p ON p.id=t.product_id"""

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        rows = self._query(f"{self._task_select()} WHERE t.id=?", (task_id,))
        return rows[0] if rows else None

    def delete_task(self, task_id: int) -> None:
        task = self.get_task(task_id)
        if not task:
            return
        if task["status"] in {"已武装", "等待中", "触发中", "提交中", "代付申请中"}:
            raise ValueError("运行中的任务不能删除，请先停止任务")
        self._execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def update_task(
        self,
        task_id: int,
        name: str,
        account_id: int,
        product_id: int,
        scheduled_at: str,
        mode: str,
        address_keyword: str = "",
        friend_pay_enabled: bool = False,
        friend_pay_account: str = "",
    ) -> None:
        task = self.get_task(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task["status"] in {"已武装", "等待中", "触发中", "提交中", "代付申请中"}:
            raise ValueError("运行中的任务不能编辑，请先停止任务")
        mode = str(mode or "").strip()
        if mode not in TASK_MODES:
            raise ValueError("不支持的任务模式")
        friend_pay_account = self._validate_friend_pay(
            friend_pay_enabled,
            friend_pay_account,
        )
        stamp = now_iso()
        self._execute(
            """UPDATE tasks SET
                   name=?,account_id=?,product_id=?,scheduled_at=?,mode=?,
                   address_keyword=?,friend_pay_enabled=?,friend_pay_account=?,
                   status='草稿',authorized_at='',last_error='',attempt_count=0,
                   triggered_at='',completed_at='',selected_url='',
                   selected_sku_note='',selected_quantity=0,
                   friend_pay_requested_at='',updated_at=?
               WHERE id=?""",
            (
                str(name or "").strip() or "辅助购买任务",
                account_id,
                product_id,
                scheduled_at,
                mode,
                str(address_keyword or "").strip(),
                int(bool(friend_pay_enabled)),
                friend_pay_account,
                stamp,
                task_id,
            ),
        )

    def set_task_status(self, task_id: int, status: str, last_error: str | None = None) -> None:
        if last_error is None:
            self._execute(
                "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                (status, now_iso(), task_id),
            )
        else:
            self._execute(
                "UPDATE tasks SET status=?,last_error=?,updated_at=? WHERE id=?",
                (status, last_error, now_iso(), task_id),
            )

    def clear_task_authorization(self, task_id: int, status: str = "待授权") -> None:
        self._execute(
            """UPDATE tasks SET authorized_at='',status=?,last_error='',attempt_count=0,
               triggered_at='',completed_at='',friend_pay_requested_at='',updated_at=? WHERE id=?""",
            (status, now_iso(), task_id),
        )

    def authorize_task(self, task_id: int) -> None:
        stamp = now_iso()
        self._execute(
            "UPDATE tasks SET authorized_at=?,status='已武装',last_error='',updated_at=? WHERE id=?",
            (stamp, stamp, task_id),
        )

    def set_task_schedule(self, task_id: int, scheduled_at: str) -> None:
        self._execute(
            "UPDATE tasks SET scheduled_at=?,updated_at=? WHERE id=?",
            (scheduled_at, now_iso(), task_id),
        )

    def set_task_selection(
        self,
        task_id: int,
        selected_url: str,
        selected_sku_note: str,
        selected_quantity: int,
    ) -> None:
        quantity = int(selected_quantity)
        if not 1 <= quantity <= 5:
            raise ValueError("商品数量必须在 1-5 之间")
        selected_url = normalize_product_url(selected_url)
        self._execute(
            """UPDATE tasks SET selected_url=?,selected_sku_note=?,selected_quantity=?,
               authorized_at='',updated_at=? WHERE id=?""",
            (selected_url, selected_sku_note.strip(), quantity, now_iso(), task_id),
        )

    def update_product_url(self, product_id: int, url: str) -> None:
        self._execute(
            "UPDATE products SET url=?,updated_at=? WHERE id=?",
            (normalize_product_url(url), now_iso(), product_id),
        )

    def mark_task_triggered(self, task_id: int) -> None:
        stamp = now_iso()
        self._execute(
            "UPDATE tasks SET status='触发中',triggered_at=?,updated_at=? WHERE id=?",
            (stamp, stamp, task_id),
        )

    def increment_task_attempt(self, task_id: int) -> None:
        self._execute(
            "UPDATE tasks SET attempt_count=attempt_count+1,updated_at=? WHERE id=?",
            (now_iso(), task_id),
        )

    def mark_task_completed(self, task_id: int, status: str) -> None:
        stamp = now_iso()
        self._execute(
            "UPDATE tasks SET status=?,completed_at=?,last_error='',updated_at=? WHERE id=?",
            (status, stamp, stamp, task_id),
        )

    def mark_friend_pay_requested(self, task_id: int) -> None:
        stamp = now_iso()
        self._execute(
            """UPDATE tasks SET status='代付待确认',friend_pay_requested_at=?,
               completed_at=?,last_error='',updated_at=? WHERE id=?""",
            (stamp, stamp, stamp, task_id),
        )

    def log(self, level: str, category: str, message: str, subject: str = "") -> None:
        self._execute(
            "INSERT INTO events(level,category,subject,message,created_at) VALUES(?,?,?,?,?)",
            (level, category, subject, message, now_iso()),
        )

    def list_events(self, limit: int = 300) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
