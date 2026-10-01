"""备份恢复测试只操作临时数据库，不操作运行数据。"""
import sqlite3
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import backup, database
from app.process_lock import single_instance


class BackupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xun-backup-test-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "source.sqlite3"
        override = patch.object(database, "DB_PATH", self.source)
        override.start()
        self.addCleanup(override.stop)
        database.initialize()
        database.set_paused(1, True)
        removed = database.create_target("删除练习", "http://127.0.0.1/removed")
        database.delete_target(removed["id"])
        for minute, status in enumerate(("down", "down", "up")):
            database.save_check(dict(target_id=1, target_name="测试", target_url="http://127.0.0.1/",
                status=status, status_code=200 if status == "up" else None, response_ms=12.5,
                checked_at=f"2026-10-01T00:0{minute}:00+00:00", error=None if status == "up" else "测试失败", source="manual"))

    def test_round_trip_preserves_all_rows_and_deleted_id_sequence(self):
        saved, restored = self.root / "backup.sqlite3", self.root / "restored.sqlite3"
        expected = backup.copy_snapshot(self.source, saved)
        # 备份之后的改动不能进入恢复副本。
        database.save_target(1, "备份之后改名", "http://127.0.0.1:8765/")
        self.assertEqual(backup.copy_snapshot(saved, restored), expected)
        self.assertEqual(expected, dict(targets=1, checks=3, incidents=1, paused_targets=1, last_target_id=2))
        with closing(backup.open_readonly(saved)) as a, closing(backup.open_readonly(restored)) as b:
            for table in backup.COLUMNS:
                self.assertEqual(a.execute(f"SELECT * FROM {table}").fetchall(), b.execute(f"SELECT * FROM {table}").fetchall())
        with patch.object(database, "DB_PATH", restored):
            database.initialize()
            self.assertTrue(database.get_target(1)["paused"])
            self.assertEqual(database.list_incidents(1)[0]["end_reason"], "recovered")
            self.assertEqual(database.create_target("新目标", "http://127.0.0.1/new")["id"], 3)
        self.assertEqual(backup.verify(saved), expected)

    def test_running_wal_database_includes_committed_rows(self):
        with single_instance(self.source), closing(sqlite3.connect(self.source)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE targets SET name='WAL记录' WHERE id=1")
            writer.commit()
            output = self.root / "wal-backup.sqlite3"
            backup.copy_snapshot(self.source, output)
            with closing(backup.open_readonly(output)) as copy:
                self.assertEqual(copy.execute("SELECT name FROM targets").fetchone()[0], "WAL记录")
                self.assertEqual(copy.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_existing_same_source_and_sidecar_are_refused(self):
        before = self.source.read_bytes()
        for output in (self.source, self.root / "existing.sqlite3", self.root / "sidecar.sqlite3"):
            if output.name == "existing.sqlite3":
                output.write_bytes(b"keep")
            if output.name == "sidecar.sqlite3":
                Path(str(output) + "-wal").write_bytes(b"keep")
            with self.assertRaises(ValueError):
                backup.copy_snapshot(self.source, output)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((self.root / "existing.sqlite3").read_bytes(), b"keep")

    def test_bad_missing_and_wrong_schema_do_not_publish(self):
        corrupt, wrong = self.root / "bad.sqlite3", self.root / "wrong.sqlite3"
        corrupt.write_bytes(b"not sqlite")
        with closing(sqlite3.connect(wrong)) as connection:
            connection.execute("CREATE TABLE unrelated(id INTEGER)")
        for source in (corrupt, wrong, self.root / "missing.sqlite3"):
            output = self.root / "output.sqlite3"
            with self.assertRaises((ValueError, sqlite3.Error)):
                backup.copy_snapshot(source, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(self.root.glob(".xun-backup-*")), [])
        self.assertFalse((self.root / "missing.sqlite3").exists())

    def test_restore_refuses_destination_used_by_application(self):
        output = self.root / "busy.sqlite3"
        with single_instance(output), self.assertRaises(RuntimeError):
            backup.copy_snapshot(self.source, output)
        self.assertFalse(output.exists())

    def test_cli_backup_verify_restore_and_failure_exit_code(self):
        saved, restored = self.root / "cli.sqlite3", self.root / "恢复 演练.sqlite3"
        for arguments in (("backup", "--source", str(self.source), "--output", str(saved)),
                          ("verify", str(saved)), ("restore", str(saved), "--output", str(restored))):
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.backup", *arguments],
                                    cwd=backup.ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["checks"], 3)
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.backup", "restore", str(saved),
                                 "--output", str(restored)], cwd=backup.ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_publication_failure_keeps_existing_file(self):
        output = self.root / "race.sqlite3"

        def raced_link(source, destination):
            destination.write_bytes(b"another file")
            raise FileExistsError("destination appeared")

        with patch.object(backup.os, "link", side_effect=raced_link), self.assertRaises(FileExistsError):
            backup.copy_snapshot(self.source, output)
        self.assertEqual(output.read_bytes(), b"another file")
        self.assertEqual(list(self.root.glob(".xun-backup-*")), [])

    def test_invalid_sequence_and_orphan_are_rejected(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("UPDATE target_id_sequence SET last_id=0")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "编号"):
            backup.verify(self.source)
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("UPDATE target_id_sequence SET last_id=2")
            connection.execute("UPDATE checks SET target_id=999")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "外键"):
            backup.verify(self.source)


if __name__ == "__main__":
    unittest.main()
