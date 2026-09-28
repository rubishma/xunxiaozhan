"""仅使用本机临时端口与临时数据库，不修改用户数据。"""
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import database, main


class Target(BaseHTTPRequestHandler):
    entered = threading.Event()

    def do_GET(self):
        if self.path == "/slow":
            self.entered.set()
            time.sleep(0.4)
        code = 503 if self.path == "/bad" else 200
        if self.path in ("/redirect", "/loop"):
            self.send_response(302)
            self.send_header("Location", "/" if self.path == "/redirect" else "/loop")
        else:
            self.send_response(code)
        self.send_header("Content-Length", "0")
        try:
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


class ManualCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xunxiaozhan-test-")
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(database, "DB_PATH", Path(folder.name) / "data" / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)

    def save(self, client, url, name="本机测试"):
        response = client.put("/api/target", json={"name": name, "url": url})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def inspect(self, path):
        with TestClient(main.app) as client:
            self.save(client, self.base + path)
            response = client.post("/api/check")
            self.assertEqual(response.status_code, 200)
            return response.json()

    def test_page_health_and_success(self):
        with TestClient(main.app) as client:
            self.assertIn("巡小站", client.get("/").text)
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
        data = self.inspect("/")
        self.assertEqual((data["status"], data["status_code"]), ("up", 200))
        self.assertIsNone(data["error"])
        self.assertGreaterEqual(data["response_ms"], 0)
        self.assertTrue(data["checked_at"].endswith("+00:00"))

    def test_http_failure_and_recovery(self):
        failed = self.inspect("/bad")
        self.assertEqual((failed["status"], failed["status_code"]), ("down", 503))
        self.assertEqual(self.inspect("/")["status"], "up")

    def test_redirect_and_redirect_limit(self):
        self.assertEqual(self.inspect("/redirect")["status_code"], 200)
        data = self.inspect("/loop")
        self.assertEqual(data["status"], "down")
        self.assertIn("重定向", data["error"])

    def test_connection_refused_has_no_status_code(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}/"
            with TestClient(main.app) as client:
                self.save(client, url)
                data = client.post("/api/check").json()
        self.assertEqual(data["status"], "down")
        self.assertIsNone(data["status_code"])
        self.assertIn("连接", data["error"])

    def test_total_timeout_releases_lock(self):
        with TestClient(main.app) as client:
            self.save(client, self.base + "/slow")
            with patch.object(main, "CHECK_TIMEOUT_SECONDS", 0.05):
                data = client.post("/api/check").json()
            self.assertEqual(data["error"], "请求超时")
            self.assertIsNone(data["status_code"])
            self.save(client, self.base + "/")
            self.assertEqual(client.post("/api/check").json()["status"], "up")

    def test_overlap_and_edit_rejected_while_health_still_responds(self):
        Target.entered.clear()
        with TestClient(main.app) as client, ThreadPoolExecutor() as pool:
            self.save(client, self.base + "/slow")
            first = pool.submit(client.post, "/api/check")
            self.assertTrue(Target.entered.wait(2))
            self.assertEqual(client.post("/api/check").status_code, 409)
            edit = client.put("/api/target", json={"name": "不能中途改", "url": self.base + "/"})
            self.assertEqual(edit.status_code, 409)
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(first.result(timeout=3).json()["status"], "up")
            self.assertEqual(client.get("/api/target").json()["url"], self.base + "/slow")

    def test_saved_target_survives_restart_and_is_actually_checked(self):
        with TestClient(main.app) as client:
            saved = self.save(client, self.base + "/bad", "重启保留 ' 测试")
        # 重新执行应用启动流程，并从同一个 SQLite 文件中读取。
        with TestClient(main.app) as restarted:
            self.assertEqual(restarted.get("/api/target").json(), saved)
            result = restarted.post("/api/check").json()
            self.assertEqual(result["target_url"], saved["url"])
            self.assertEqual(result["target_name"], saved["name"])
            self.assertEqual(result["status_code"], 503)

    def test_invalid_edits_preserve_previous_configuration(self):
        with TestClient(main.app) as client:
            before = self.save(client, self.base + "/")
            invalid = [
                {"name": " ", "url": self.base + "/"},
                {"name": "x" * 81, "url": self.base + "/"},
                {"name": "目标", "url": "ftp://localhost/"},
                {"name": "目标", "url": "localhost:8765"},
                {"name": "目标", "url": "http://"},
                {"name": "目标", "url": "http://localhost:99999/"},
                {"name": "目标", "url": "http://user:password@localhost/"},
                {"name": "目标", "url": self.base + "/#section"},
                {"name": "目标", "url": self.base + "/space here"},
            ]
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(client.put("/api/target", json=payload).status_code, 422)
                    self.assertEqual(client.get("/api/target").json(), before)

    def test_database_failure_has_clear_error_and_does_not_replace_target(self):
        with TestClient(main.app) as client:
            before = self.save(client, self.base + "/")
            with patch.object(database, "save_target", side_effect=sqlite3.OperationalError("test failure")):
                with self.assertLogs("app.main", level="ERROR"):
                    response = client.put("/api/target", json={"name": "新名称", "url": self.base + "/bad"})
            self.assertEqual(response.status_code, 503)
            self.assertIn("数据库", response.json()["detail"])
            self.assertEqual(client.get("/api/target").json(), before)


if __name__ == "__main__":
    unittest.main()
