"""本地数据库备份、校验及恢复到新文件；不覆盖已有数据。"""
import argparse
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.database import DB_PATH
from app.process_lock import single_instance

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = {
    "targets": "id name url paused",
    "target_id_sequence": "singleton last_id",
    "checks": "id target_id target_name target_url status status_code response_ms checked_at error source",
    "incidents": "id target_id target_name target_url started_at last_failed_at failure_count first_error last_error recovered_at ended_at end_reason",
}


def open_readonly(path):
    # mode=ro 避免路径写错时意外创建空数据库；不使用 immutable，兼容正在运行的 WAL。
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


def inspect_connection(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("数据库完整性检查失败")
    for table, expected in COLUMNS.items():
        kind = connection.execute("SELECT type FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if kind != ("table",) or not set(expected.split()).issubset(actual):
            raise ValueError(f"不支持的数据库结构：{table}；请使用当前版本生成的备份")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("存在无效的外键引用")
    maximum = connection.execute("SELECT COALESCE(MAX(id), 0) FROM targets").fetchone()[0]
    sequence = connection.execute("SELECT singleton, last_id FROM target_id_sequence").fetchall()
    if len(sequence) != 1 or sequence[0][0] != 1 or sequence[0][1] < maximum:
        raise ValueError("目标编号序列不完整")
    counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("targets", "checks", "incidents")}
    counts["paused_targets"] = connection.execute("SELECT COUNT(*) FROM targets WHERE paused = 1").fetchone()[0]
    counts["last_target_id"] = sequence[0][1]
    return counts


def verify(path):
    with closing(open_readonly(path)) as connection:
        # 在一个读事务中核对统计，后台写入不会让几项统计跨越不同快照。
        connection.execute("BEGIN")
        return inspect_connection(connection)


def copy_snapshot(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError("源文件和目标文件不能相同")
    if not source.is_file():
        raise ValueError("源数据库不存在；请核对路径")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 与应用使用同一锁；防止在应用准备启动的路径上同时恢复。
    with single_instance(output):
        if output.exists() or any(Path(str(output) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("目标或其日志文件已存在，拒绝覆盖；请选择新的恢复/备份文件名")
        handle, temporary_name = tempfile.mkstemp(prefix=".xun-backup-", suffix=".sqlite3", dir=output.parent)
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            deadline = time.monotonic() + 30

            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError("备份超过 30 秒；可暂停写入后重试")

            with closing(open_readonly(source)) as origin, closing(sqlite3.connect(temporary)) as target:
                origin.backup(target, pages=128, progress=progress, sleep=0.05)
                # 输出独立文件，不依赖源库的 WAL 或 SHM 文件。
                target.execute("PRAGMA journal_mode = DELETE")
            report = verify(temporary)
            with temporary.open("r+b") as saved:
                os.fsync(saved.fileno())
            # 同目录硬链接发布完整副本；目标已存在时失败，不使用会覆盖的 replace。
            os.link(temporary, output)
            return report
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="巡小站数据库备份与恢复（不覆盖已有文件）")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="备份运行数据库，可在后端运行时执行")
    backup.add_argument("--source", type=Path, default=DB_PATH)
    backup.add_argument("--output", type=Path)
    check = commands.add_parser("verify", help="只读校验并显示记录数量")
    check.add_argument("source", type=Path)
    restore = commands.add_parser("restore", help="从备份恢复到一个不存在的新文件")
    restore.add_argument("source", type=Path)
    restore.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            output = args.source.resolve()
            report = verify(output)
        else:
            output = args.output
            if output is None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                output = ROOT / "backups" / f"xunxiaozhan-{stamp}.sqlite3"
            output = output.resolve()
            report = copy_snapshot(args.source, output)
        print(json.dumps({"operation": args.command, "file": str(output), "verified": True, **report}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print(f"操作失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
