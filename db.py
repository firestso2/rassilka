"""
SQLite database layer.
"""

import sqlite3
import json
import os
from typing import Optional, List, Dict

DB_PATH = os.environ.get("DB_PATH", "spambot.db")


class Database:
    def __init__(self):
        self.path = DB_PATH

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    api_id INTEGER NOT NULL,
                    api_hash TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    session_string TEXT,
                    connected INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    acc_id INTEGER REFERENCES accounts(id),
                    task_type TEXT NOT NULL,
                    chats TEXT,
                    folder TEXT,
                    count INTEGER,
                    delay REAL,
                    text TEXT,
                    stop_time TEXT,
                    status TEXT DEFAULT 'running',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def ensure_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (user_id,))

    # ─── Accounts ───────────────────────────────────────────────────────────

    def add_account(self, user_id: int, api_id: int, api_hash: str, phone: str, session_string: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO accounts(user_id, api_id, api_hash, phone, session_string, connected) VALUES(?,?,?,?,?,1)",
                (user_id, api_id, api_hash, phone, session_string)
            )
            return cur.lastrowid

    def get_accounts(self, user_id: int, connected_only: bool = False) -> List[Dict]:
        with self._conn() as conn:
            if connected_only:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE user_id=? AND connected=1", (user_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE user_id=?", (user_id,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_account(self, acc_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()
            return dict(row) if row else None

    def get_all_accounts(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM accounts WHERE session_string IS NOT NULL").fetchall()
            return [dict(r) for r in rows]

    def delete_account(self, acc_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM accounts WHERE id=?", (acc_id,))

    def set_account_connected(self, acc_id: int, connected: bool):
        with self._conn() as conn:
            conn.execute("UPDATE accounts SET connected=? WHERE id=?", (int(connected), acc_id))

    def update_session(self, acc_id: int, session_string: str):
        with self._conn() as conn:
            conn.execute("UPDATE accounts SET session_string=? WHERE id=?", (session_string, acc_id))

    # ─── Tasks ──────────────────────────────────────────────────────────────

    def create_task(self, user_id: int, acc_id: int, task_type: str,
                    chats: Optional[List], folder: Optional[str],
                    count: int, delay: float, text: str,
                    stop_time: Optional[str]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO tasks(user_id, acc_id, task_type, chats, folder, count, delay, text, stop_time, status)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (user_id, acc_id, task_type,
                 json.dumps(chats) if chats else None,
                 folder, count, delay, text, stop_time, "running")
            )
            return cur.lastrowid

    def get_task(self, task_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get('chats'):
                d['chats'] = json.loads(d['chats'])
            return d

    def get_tasks(self, user_id: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id=? AND status != 'stopped' ORDER BY id DESC",
                (user_id,)
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get('chats'):
                    d['chats'] = json.loads(d['chats'])
                result.append(d)
            return result

    def set_task_status(self, task_id: int, status: str):
        with self._conn() as conn:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
