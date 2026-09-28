"""目标配置的 SQLite 存取。每次操作自行打开并关闭连接。"""
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "xunxiaozhan.sqlite3"


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1))
                )
            """)
            # 给旧版表补一列，不重建表，也不覆盖名称与网址。
            columns = {row[1] for row in connection.execute("PRAGMA table_info(targets)")}
            if "paused" not in columns:
                connection.execute(
                    "ALTER TABLE targets ADD COLUMN paused INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (paused IN (0, 1))"
                )
            # 本轮只管理 id=1 的一个目标；已有值不会在启动时被覆盖。
            connection.execute(
                "INSERT OR IGNORE INTO targets (id, name, url) VALUES (1, ?, ?)",
                ("本机测试网站", "http://127.0.0.1:8765/"),
            )


def get_target():
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT id, name, url, paused FROM targets WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("默认目标不存在")
        target = dict(row)
        target["paused"] = bool(target["paused"])
        return target


def save_target(name, url):
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            # 参数绑定让名称和网址作为数据处理，而不是拼接为 SQL。
            connection.execute(
                "UPDATE targets SET name = ?, url = ? WHERE id = 1", (name, url)
            )
    return get_target()


def set_paused(paused):
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            connection.execute(
                "UPDATE targets SET paused = ? WHERE id = 1", (int(paused),)
            )
    return get_target()
