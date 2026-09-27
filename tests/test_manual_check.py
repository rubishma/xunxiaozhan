"""仅使用本机临时端口，不访问外部网站。"""
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main


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

    def inspect(self, path):
        with patch.object(main, "TARGET_URL", self.base + path):
            with TestClient(main.app) as client:
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
        self.assertEqual(self.inspect("/bad")["status"], "down")
        self.assertEqual(self.inspect("/bad")["status_code"], 503)
        self.assertEqual(self.inspect("/")["status"], "up")

    def test_redirect_and_redirect_limit(self):
        self.assertEqual(self.inspect("/redirect")["status_code"], 200)
        data = self.inspect("/loop")
        self.assertEqual(data["status"], "down")
        self.assertIn("重定向", data["error"])

    def test_connection_refused_has_no_status_code(self):
        # 占住端口但不 listen：系统拒绝连接，且避免挑空闲端口的竞争。
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}/"
            with patch.object(main, "TARGET_URL", url):
                with TestClient(main.app) as client:
                    data = client.post("/api/check").json()
        self.assertEqual(data["status"], "down")
        self.assertIsNone(data["status_code"])
        self.assertIn("连接", data["error"])

    def test_total_timeout_releases_lock(self):
        with TestClient(main.app) as client:
            with patch.object(main, "CHECK_TIMEOUT_SECONDS", 0.05):
                with patch.object(main, "TARGET_URL", self.base + "/slow"):
                    data = client.post("/api/check").json()
            self.assertEqual(data["error"], "请求超时")
            self.assertIsNone(data["status_code"])
            with patch.object(main, "TARGET_URL", self.base + "/"):
                self.assertEqual(client.post("/api/check").json()["status"], "up")

    def test_overlap_rejected_while_health_still_responds(self):
        Target.entered.clear()
        with patch.object(main, "TARGET_URL", self.base + "/slow"):
            with TestClient(main.app) as client, ThreadPoolExecutor() as pool:
                first = pool.submit(client.post, "/api/check")
                self.assertTrue(Target.entered.wait(2))
                self.assertEqual(client.post("/api/check").status_code, 409)
                self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(first.result(timeout=3).json()["status"], "up")


if __name__ == "__main__":
    unittest.main()
