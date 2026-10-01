"""可控时钟、本机临时服务和临时数据库验证调度；不访问运行数据。"""
import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import database, main
from app.scheduler import Scheduler
from test_manual_check import Target


class SchedulerLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_delay_after_completion_no_catchup_and_pause(self):
        now = [0]
        targets = [{"id": 1, "paused": False}, {"id": 2, "paused": True}]
        calls = []
        async def listing():
            return targets
        async def check(target_id):
            calls.append(target_id)
            now[0] += 5
        runner = Scheduler(60, listing, check, clock=lambda: now[0])
        await runner.run_once()
        self.assertEqual(calls, [])
        now[0] = 59
        await runner.run_once()
        self.assertEqual(calls, [])
        now[0] = 60
        await runner.run_once()
        self.assertEqual(calls, [1])
        self.assertEqual(runner.deadlines[1], 125)
        now[0] = 10000  # 模拟机器休眠后恢复，只执行一次，不补跑旧任务。
        await runner.run_once()
        self.assertEqual(calls, [1, 1])
        await runner.run_once()
        self.assertEqual(calls, [1, 1])
        targets.clear()
        await runner.run_once()
        self.assertEqual(runner.deadlines, {})

    async def test_one_failure_does_not_stop_other_targets_or_next_round(self):
        now = [0]
        calls = []
        async def listing():
            return [{"id": 1, "paused": False}, {"id": 2, "paused": False}]
        async def check(target_id):
            calls.append(target_id)
            if target_id == 1:
                raise sqlite3.OperationalError("test write failure")
        runner = Scheduler(60, listing, check, clock=lambda: now[0])
        await runner.run_once()
        now[0] = 60
        with self.assertLogs("app.scheduler", level="ERROR"):
            await runner.run_once()
        self.assertEqual(calls, [1, 2])
        now[0] = 120
        with self.assertLogs("app.scheduler", level="ERROR"):
            await runner.run_once()
        self.assertEqual(calls, [1, 2, 1, 2])

    async def test_shutdown_waits_for_inflight_check_and_starts_no_more(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def listing():
            return [{"id": 1, "paused": False}, {"id": 2, "paused": False}]
        async def check(target_id):
            calls.append(target_id)
            entered.set()
            await release.wait()
        runner = Scheduler(60, listing, check, clock=lambda: 100)
        runner.deadlines = {1: 0, 2: 0}
        task = asyncio.create_task(runner.run())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            runner.stop.set()
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(calls, [1])

    async def test_loop_recovers_after_list_read_failure(self):
        attempts = []
        async def listing():
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("temporary read failure")
            runner.stop.set()
            return []
        runner = Scheduler(60, listing, None, poll_seconds=0.01)
        with self.assertLogs("app.scheduler", level="ERROR"):
            await asyncio.wait_for(runner.run(), 2)
        self.assertEqual(len(attempts), 2)


class SchedulerIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xun-scheduler-test-")
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(database, "DB_PATH", Path(folder.name) / "data" / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.now = [0]
        factory_patch = patch.object(main, "Scheduler", side_effect=lambda interval, listing, check:
            Scheduler(interval, listing, check, clock=lambda: self.now[0], poll_seconds=0.02))
        factory_patch.start()
        self.addCleanup(factory_patch.stop)
        interval_patch = patch.object(main, "get_check_interval", return_value=60)
        interval_patch.start()
        self.addCleanup(interval_patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail("Timed out waiting for local scheduler")

    def test_loop_writes_history_manual_defers_and_pause_resume(self):
        with TestClient(main.app) as client:
            client.put("/api/targets/1", json={"name": "定时测试", "url": self.base + "/"})
            self.assertEqual(client.get("/api/scheduler").json(), {"running": True, "interval_seconds": 60})
            self.assertEqual(client.get("/api/targets/1/history").json(), [])
            self.now[0] = 61
            self.wait_until(lambda: len(client.get("/api/targets/1/history").json()) == 1)
            first = client.get("/api/targets/1/history").json()[0]
            self.assertEqual((first["source"], first["status_code"]), ("scheduled", 200))
            self.now[0] = 90
            self.assertEqual(client.post("/api/targets/1/check").json()["source"], "manual")
            self.now[0] = 122  # 原定时间已到，但手动检查将它推迟到 150。
            time.sleep(0.08)
            self.assertEqual(len(client.get("/api/targets/1/history").json()), 2)
            client.patch("/api/targets/1/state", json={"paused": True})
            self.now[0] = 1000
            time.sleep(0.08)
            self.assertEqual(len(client.get("/api/targets/1/history").json()), 2)
            client.patch("/api/targets/1/state", json={"paused": False})
            self.now[0] = 1059
            time.sleep(0.08)
            self.assertEqual(len(client.get("/api/targets/1/history").json()), 2)
            self.now[0] = 1061
            self.wait_until(lambda: len(client.get("/api/targets/1/history").json()) == 3)
            self.assertEqual(client.get("/api/targets/1/history").json()[0]["source"], "scheduled")
        self.assertTrue(main.app.state.scheduler_task.done())

    def test_manual_and_scheduled_checks_never_overlap(self):
        Target.held_entered.clear()
        Target.held_release.clear()
        with TestClient(main.app) as client, ThreadPoolExecutor() as pool:
            client.put("/api/targets/1", json={"name": "慢目标", "url": self.base + "/held"})
            pending = pool.submit(client.post, "/api/targets/1/check")
            try:
                self.assertTrue(Target.held_entered.wait(2))
                before = Target.request_count
                self.now[0] = 61
                self.wait_until(lambda: main.app.state.scheduler.deadlines[1] > 61)
                self.assertEqual(Target.request_count, before)
                self.assertEqual(client.get("/api/health").status_code, 200)
            finally:
                Target.held_release.set()
            self.assertEqual(pending.result(timeout=3).json()["source"], "manual")
            Target.held_entered.clear()
            Target.held_release.clear()
            try:
                self.now[0] = 122
                self.assertTrue(Target.held_entered.wait(2))
                self.assertEqual(client.post("/api/targets/1/check").status_code, 409)
                self.assertEqual(client.patch("/api/targets/1/state", json={"paused": True}).status_code, 409)
                self.assertEqual(client.get("/api/health").status_code, 200)
            finally:
                Target.held_release.set()
            self.wait_until(lambda: len(client.get("/api/targets/1/history").json()) == 2)
            self.assertEqual([row["source"] for row in client.get("/api/targets/1/history").json()], ["scheduled", "manual"])

    def test_upgrade_existing_history_marks_manual_without_overwriting(self):
        database.DB_PATH.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            with connection:
                connection.execute("CREATE TABLE targets (id INTEGER PRIMARY KEY, name TEXT, url TEXT, paused INTEGER DEFAULT 0)")
                connection.execute("INSERT INTO targets VALUES (1, 'original', ?, 1)", (self.base + "/",))
                connection.execute("CREATE TABLE checks (id INTEGER PRIMARY KEY, target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE, target_name TEXT, target_url TEXT, status TEXT, status_code INTEGER, response_ms REAL, checked_at TEXT, error TEXT)")
                connection.execute("INSERT INTO checks VALUES (8, 1, 'original', ?, 'up', 200, 12.3, '2026-09-30T15:00:00+00:00', NULL)", (self.base + "/",))
        with TestClient(main.app) as client:
            rows = client.get("/api/targets/1/history").json()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], 8)
            self.assertEqual(rows[0]["source"], "manual")
            self.assertEqual(rows[0]["response_ms"], 12.3)
            self.assertTrue(client.get("/api/targets/1").json()["paused"])


class ProcessAndConfigTests(unittest.TestCase):
    def test_interval_validation(self):
        for value in ["0", "29", "86401", "abc", "30.5"]:
            with self.subTest(value=value), patch.dict(os.environ, {"XUN_CHECK_INTERVAL_SECONDS": value}):
                with self.assertRaises(RuntimeError):
                    main.get_check_interval()
        with patch.dict(os.environ, {"XUN_CHECK_INTERVAL_SECONDS": "30"}):
            self.assertEqual(main.get_check_interval(), 30)

    def test_other_process_rejected_and_abrupt_exit_releases_lock(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with tempfile.TemporaryDirectory(prefix="xun-lock-test-") as directory:
            db = Path(directory) / "test.sqlite3"
            ready = Path(directory) / "ready"
            code = "from pathlib import Path; import sys,time; from app.process_lock import single_instance; "
            owner_code = code + "\nwith single_instance(Path(sys.argv[1])):\n Path(sys.argv[2]).write_text('ready'); time.sleep(30)"
            owner = subprocess.Popen([sys.executable, "-c", owner_code, str(db), str(ready)], creationflags=flags)
            contender = code + "\nwith single_instance(Path(sys.argv[1])):\n print('acquired')"
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                refused = subprocess.run([sys.executable, "-c", contender, str(db)], capture_output=True, creationflags=flags, timeout=5)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(b"RuntimeError", refused.stderr)
            finally:
                owner.terminate()
                owner.wait(timeout=5)
            succeeded = subprocess.run([sys.executable, "-c", contender, str(db)], capture_output=True, creationflags=flags, timeout=5)
            self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
            self.assertIn(b"acquired", succeeded.stdout)


if __name__ == "__main__":
    unittest.main()
