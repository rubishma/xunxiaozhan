"""SQLite 目标存取。每次操作独立连接，参数绑定避免拼接 SQL。"""
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "xunxiaozhan.sqlite3"


class TargetNotFound(Exception):
    pass


class DuplicateTarget(Exception):
    pass


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        existed = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'targets'"
        ).fetchone()
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1))
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(targets)")}
            if "paused" not in columns:
                connection.execute(
                    "ALTER TABLE targets ADD COLUMN paused INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (paused IN (0, 1))"
                )
            # 仅全新数据库放入演示目标；已有数据库（包括空表）不重新填充。
            if not existed:
                connection.execute(
                    "INSERT INTO targets (name, url) VALUES (?, ?)",
                    ("本机测试网站", "http://127.0.0.1:8765/"),
                )


def as_target(row):
    target = dict(row)
    target["paused"] = bool(target["paused"])
    return target


def list_targets():
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id, name, url, paused FROM targets ORDER BY id").fetchall()
        return [as_target(row) for row in rows]


def get_target(target_id):
    if not 1 <= target_id <= 9223372036854775807:
        raise TargetNotFound()
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, name, url, paused FROM targets WHERE id = ?", (target_id,)
        ).fetchone()
        if row is None:
            raise TargetNotFound()
        return as_target(row)


def create_target(name, url):
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            # 串行化“查重 + 写入”，防止两个请求同时插入同一网址。
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM targets WHERE url = ?", (url,)).fetchone():
                raise DuplicateTarget()
            cursor = connection.execute(
                "INSERT INTO targets (name, url) VALUES (?, ?)", (name, url)
            )
            target_id = cursor.lastrowid
    return get_target(target_id)


def save_target(target_id, name, url):
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT 1 FROM targets WHERE url = ? AND id != ?", (url, target_id)
            ).fetchone()
            if duplicate:
                raise DuplicateTarget()
            cursor = connection.execute(
                "UPDATE targets SET name = ?, url = ? WHERE id = ?", (name, url, target_id)
            )
            if cursor.rowcount == 0:
                raise TargetNotFound()
    return get_target(target_id)


def set_paused(target_id, paused):
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE targets SET paused = ? WHERE id = ?", (int(paused), target_id)
            )
            if cursor.rowcount == 0:
                raise TargetNotFound()
    return get_target(target_id)
