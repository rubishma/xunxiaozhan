"""对同一数据库只允许一个应用进程，锁由操作系统随进程退出释放。"""
import os
from contextlib import contextmanager


@contextmanager
def single_instance(database_path):
    path = database_path.resolve().with_suffix(database_path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 不删除锁文件：删除再重建会让两个进程锁住不同的文件。
    with open(path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "无法取得数据库进程锁：可能已有巡小站实例运行。"
                "请使用 --workers 1，停止旧实例；不要删除锁文件。"
                "若没有其他实例，请检查 data 目录权限。"
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
