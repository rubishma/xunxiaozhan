"""同一 URL 的可控故障与恢复；所有数据放在临时目录。"""
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import database, main


class IncidentTarget(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.request_count += 1
        self.send_response(self.server.status_code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class IncidentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xun-incidents-test-")
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(database, "DB_PATH", Path(folder.name) / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        interval_patch = patch.object(main, "get_check_interval", return_value=60)
        interval_patch.start()
        self.addCleanup(interval_patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), IncidentTarget)
        self.server.status_code = 200
        self.server.request_count = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def configure(self, client, url=None, name="故障演练"):
        response = client.put("/api/targets/1", json={"name": name, "url": url or self.url})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def inspect(self, client, status, source="manual"):
        self.server.status_code = status
        if source == "scheduled":
            async def scheduled():
                main.app.state.scheduler.deadlines[1] = 0
                return await main.perform_check(1, source="scheduled")
            return client.portal.call(scheduled)
        response = client.post("/api/targets/1/check")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def incidents(self, client, target_id=1):
        response = client.get(f"/api/targets/{target_id}/incidents")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_continuous_failure_merges_recovery_closes_and_new_failure_starts_again(self):
        with TestClient(main.app) as client:
            self.configure(client)
            self.inspect(client, 200)
            self.assertEqual(self.incidents(client), [])
            first = self.inspect(client, 503)
            self.inspect(client, 500, "scheduled")
            last_failed = self.inspect(client, 503)
            opened = self.incidents(client)
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0]["failure_count"], 3)
            self.assertEqual(opened[0]["started_at"], first["checked_at"])
            self.assertEqual(opened[0]["last_failed_at"], last_failed["checked_at"])
            self.assertEqual(opened[0]["first_error"], "目标返回 HTTP 503")
            self.assertIsNone(opened[0]["recovered_at"])
            recovered = self.inspect(client, 200, "scheduled")
            closed = self.incidents(client)[0]
            self.assertEqual(closed["id"], opened[0]["id"])
            self.assertEqual(closed["failure_count"], 3)
            self.assertEqual(closed["end_reason"], "recovered")
            self.assertEqual(closed["recovered_at"], recovered["checked_at"])
            self.assertEqual(closed["ended_at"], recovered["checked_at"])
            self.inspect(client, 200)
            self.assertEqual(self.incidents(client), [closed])
            self.inspect(client, 503)
            rows = self.incidents(client)
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["id"], closed["id"])
            self.assertEqual(rows[0]["failure_count"], 1)
            self.assertIsNone(rows[0]["ended_at"])
            self.assertEqual(len(client.get("/api/targets/1/history").json()), 7)

    def test_pause_restart_resume_keeps_same_open_incident(self):
        with TestClient(main.app) as client:
            self.configure(client)
            self.inspect(client, 503)
            opened = self.incidents(client)[0]
            client.patch("/api/targets/1/state", json={"paused": True})
            before = self.server.request_count
            self.assertEqual(client.post("/api/targets/1/check").status_code, 409)
            self.assertEqual(self.server.request_count, before)
            self.assertEqual(self.incidents(client), [opened])
        with TestClient(main.app) as restarted:
            self.assertEqual(self.incidents(restarted), [opened])
            restarted.patch("/api/targets/1/state", json={"paused": False})
            self.inspect(restarted, 503)
            row = self.incidents(restarted)[0]
            self.assertEqual(row["id"], opened["id"])
            self.assertEqual(row["failure_count"], 2)
            self.assertEqual(row["started_at"], opened["started_at"])
            self.inspect(restarted, 200)
            self.assertEqual(self.incidents(restarted)[0]["end_reason"], "recovered")

    def test_rename_keeps_incident_url_change_ends_without_fake_recovery(self):
        with TestClient(main.app) as client:
            self.configure(client)
            self.inspect(client, 503)
            opened = self.incidents(client)[0]
            self.configure(client, name="只改名称")
            self.assertEqual(self.incidents(client), [opened])
            response = client.put("/api/targets/1", json={"name": "无效", "url": "ftp://localhost/"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.incidents(client), [opened])
            self.configure(client, url=self.url + "new", name="新网址")
            ended = self.incidents(client)[0]
            self.assertEqual(ended["end_reason"], "url_changed")
            self.assertIsNotNone(ended["ended_at"])
            self.assertIsNone(ended["recovered_at"])
            self.assertEqual(ended["target_url"], self.url)
            self.inspect(client, 200)
            self.assertEqual(self.incidents(client), [ended])
            self.inspect(client, 503)
            rows = self.incidents(client)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["target_url"], self.url + "new")
            self.assertEqual(rows[0]["failure_count"], 1)

    def test_incident_write_failure_rolls_back_history_and_configuration(self):
        with TestClient(main.app) as client:
            self.configure(client)
            self.server.status_code = 503
            with closing(sqlite3.connect(database.DB_PATH)) as connection:
                connection.execute("CREATE TRIGGER refuse_incident_insert BEFORE INSERT ON incidents BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            with self.assertLogs("app.main", level="ERROR"):
                self.assertEqual(client.post("/api/targets/1/check").status_code, 503)
            self.assertEqual(client.get("/api/targets/1/history").json(), [])
            self.assertEqual(self.incidents(client), [])
            with closing(sqlite3.connect(database.DB_PATH)) as connection:
                connection.execute("DROP TRIGGER refuse_incident_insert")
            first = self.inspect(client, 503)
            opened = self.incidents(client)[0]
            with closing(sqlite3.connect(database.DB_PATH)) as connection:
                connection.execute("CREATE TRIGGER refuse_incident_update BEFORE UPDATE ON incidents BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            self.server.status_code = 200
            with self.assertLogs("app.main", level="ERROR"):
                self.assertEqual(client.post("/api/targets/1/check").status_code, 503)
            self.assertEqual(client.get("/api/targets/1/history").json(), [first])
            self.assertEqual(self.incidents(client), [opened])
            with self.assertLogs("app.main", level="ERROR"):
                response = client.put("/api/targets/1", json={"name": "不能部分保存", "url": self.url + "changed"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(client.get("/api/targets/1").json()["url"], self.url)
            self.assertEqual(self.incidents(client), [opened])
            with closing(sqlite3.connect(database.DB_PATH)) as connection:
                connection.execute("DROP TRIGGER refuse_incident_update")
            self.inspect(client, 200)
            self.assertEqual(self.incidents(client)[0]["end_reason"], "recovered")

    def test_incidents_are_isolated_and_delete_cascades(self):
        with TestClient(main.app) as client:
            self.configure(client)
            self.inspect(client, 503)
            other = client.post("/api/targets", json={"name": "同名也独立", "url": self.url + "other"}).json()
            self.assertEqual(self.incidents(client, other["id"]), [])
            self.assertEqual(client.post(f"/api/targets/{other['id']}/check").status_code, 200)
            kept = self.incidents(client, other["id"])
            self.assertEqual(len(kept), 1)
            self.assertEqual(client.delete("/api/targets/1").status_code, 200)
            self.assertEqual(self.incidents(client, other["id"]), kept)
            for target_id in [1, -1, 999999999999999999999]:
                self.assertEqual(client.get(f"/api/targets/{target_id}/incidents").status_code, 404)
            with closing(sqlite3.connect(database.DB_PATH)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM incidents WHERE target_id = 1").fetchone()[0], 0)
            new = client.post("/api/targets", json={"name": "重新添加", "url": self.url}).json()
            self.assertEqual(self.incidents(client, new["id"]), [])

    def test_startup_does_not_backfill_old_history(self):
        database.initialize()
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            with connection:
                connection.execute("INSERT INTO checks (target_id, target_name, target_url, status, status_code, response_ms, checked_at, error, source) VALUES (1, 'old', ?, 'down', 503, 1, '2026-09-30T00:00:00+00:00', 'old failure', 'manual')", (self.url,))
        with TestClient(main.app) as client:
            self.assertEqual(len(client.get("/api/targets/1/history").json()), 1)
            self.assertEqual(self.incidents(client), [])
            self.configure(client)
            new_check = self.inspect(client, 503)
            self.assertEqual(self.incidents(client)[0]["started_at"], new_check["checked_at"])
            self.assertEqual(self.incidents(client)[0]["failure_count"], 1)


if __name__ == "__main__":
    unittest.main()
