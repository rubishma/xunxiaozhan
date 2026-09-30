"""SQLite 目标存取。每次操作独立连接，参数绑定避免拼接 SQL。"""
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "xunxiaozhan.sqlite3"


class TargetNotFound(Exception):
    pass


class DuplicateTarget(Exception):
    pass


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=3)
    # SQLite 外键约束按连接启用，删除目标时一并删除其历史。
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as connection:
        existed = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'targets'"
        ).fetchone()
        with connection:
            connection.execute("BEGIN IMMEDIATE")
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

            # 记录已分配的最大编号。删除目标不减少它，防止旧页面误操作新目标。
            # 单独加表即可兼容旧数据库，无需重建或清空 targets。
            connection.execute("""
                CREATE TABLE IF NOT EXISTS target_id_sequence (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    last_id INTEGER NOT NULL CHECK (last_id >= 0)
                )
            """)
            connection.execute("""
                INSERT OR IGNORE INTO target_id_sequence (singleton, last_id)
                SELECT 1, COALESCE(MAX(id), 0) FROM targets
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY,
                    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
                    target_name TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('up', 'down')),
                    status_code INTEGER,
                    response_ms REAL NOT NULL CHECK (response_ms >= 0),
                    checked_at TEXT NOT NULL,
                    error TEXT
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS checks_target_recent ON checks(target_id, id DESC)")


def as_target(row):
    target = dict(row)
    target["paused"] = bool(target["paused"])
    return target


def list_targets():
    with closing(connect()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id, name, url, paused FROM targets ORDER BY id").fetchall()
        return [as_target(row) for row in rows]


def get_target(target_id):
    if not 1 <= target_id <= 9223372036854775807:
        raise TargetNotFound()
    with closing(connect()) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, name, url, paused FROM targets WHERE id = ?", (target_id,)
        ).fetchone()
        if row is None:
            raise TargetNotFound()
        return as_target(row)


def create_target(name, url):
    with closing(connect()) as connection:
        with connection:
            # 串行化“查重 + 写入”，防止两个请求同时插入同一网址。
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM targets WHERE url = ?", (url,)).fetchone():
                raise DuplicateTarget()
            allocated = connection.execute("""
                UPDATE target_id_sequence SET last_id = last_id + 1
                WHERE singleton = 1 AND last_id < 9223372036854775807
                RETURNING last_id
            """).fetchone()
            if allocated is None:
                raise sqlite3.IntegrityError("Target ID sequence unavailable or exhausted")
            target_id = allocated[0]
            connection.execute(
                "INSERT INTO targets (id, name, url) VALUES (?, ?, ?)", (target_id, name, url)
            )
    return get_target(target_id)


def save_target(target_id, name, url):
    with closing(connect()) as connection:
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
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE targets SET paused = ? WHERE id = ?", (int(paused), target_id)
            )
            if cursor.rowcount == 0:
                raise TargetNotFound()
    return get_target(target_id)


def delete_target(target_id):
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute("DELETE FROM targets WHERE id = ?", (target_id,))
            if cursor.rowcount == 0:
                raise TargetNotFound()


def save_check(result):
    # 保留当时的配置快照；只有提交成功，接口才报告检查结果已保存。
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute("""
                INSERT INTO checks
                    (target_id, target_name, target_url, status, status_code, response_ms, checked_at, error)
                VALUES (:target_id, :target_name, :target_url, :status, :status_code, :response_ms, :checked_at, :error)
            """, result)
            check_id = cursor.lastrowid
    return {"id": check_id, **result}


def list_checks(target_id):
    get_target(target_id)  # 无记录返回空列表，不存在的目标返回 404。
    with closing(connect()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM checks WHERE target_id = ? ORDER BY id DESC LIMIT 20", (target_id,)
        ).fetchall()
        return [dict(row) for row in rows]
