from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


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
                connection.execute(
                    "UPDATE accounts SET status='未登录',updated_at=? WHERE status IN ('启动中','检查中')",
                    (now_iso(),),
                )

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

    def add_product(self, name: str, url: str, sku_note: str = "", quantity: int = 1) -> int:
        name, url = name.strip(), url.strip()
        if not name or not url:
            raise ValueError("商品名称和链接不能为空")
        if not url.startswith(("https://item.taobao.com/", "https://detail.tmall.com/", "https://m.tb.cn/")):
            raise ValueError("首版只接受淘宝、天猫或 m.tb.cn 商品链接")
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

    def add_task(self, name: str, account_id: int, product_id: int, scheduled_at: str) -> int:
        stamp = now_iso()
        return self._execute(
            """INSERT INTO tasks(name,account_id,product_id,scheduled_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?)""",
            (name.strip() or "辅助购买任务", account_id, product_id, scheduled_at, stamp, stamp),
        )

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._query(
            """SELECT t.*,a.nickname AS account_name,p.name AS product_name,p.url AS product_url
               FROM tasks t JOIN accounts a ON a.id=t.account_id
               JOIN products p ON p.id=t.product_id ORDER BY t.id DESC"""
        )

    def set_task_status(self, task_id: int, status: str) -> None:
        self._execute(
            "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
            (status, now_iso(), task_id),
        )

    def log(self, level: str, category: str, message: str, subject: str = "") -> None:
        self._execute(
            "INSERT INTO events(level,category,subject,message,created_at) VALUES(?,?,?,?,?)",
            (level, category, subject, message, now_iso()),
        )

    def list_events(self, limit: int = 300) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
