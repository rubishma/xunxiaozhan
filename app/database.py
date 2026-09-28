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
                    url TEXT NOT NULL
                )
            """)
            # 本轮只管理 id=1 的一个目标；已有值不会在启动时被覆盖。
            connection.execute(
                "INSERT OR IGNORE INTO targets (id, name, url) VALUES (1, ?, ?)",
                ("本机测试网站", "http://127.0.0.1:8765/"),
            )


def get_target():
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT id, name, url FROM targets WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("默认目标不存在")
        return dict(row)


def save_target(name, url):
    with closing(sqlite3.connect(DB_PATH, timeout=3)) as connection:
        with connection:
            # 参数绑定让名称和网址作为数据处理，而不是拼接为 SQL。
            connection.execute(
                "UPDATE targets SET name = ?, url = ? WHERE id = 1", (name, url)
            )
    return {"id": 1, "name": name, "url": url}
