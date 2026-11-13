import os
import re
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

os.environ["SECRET_KEY"] = "test-only-secret-that-is-not-used-in-production"
os.environ["GEMINI_API_KEY"] = ""

import app as web_app
import chat_logger
import config


class FakeBot:
    def ask(self, question):
        return f"Answer to: {question}", 0.25

    def reset(self):
        return None

    def list_models(self):
        return ["models/test-model"]


class ChatbotWebTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config.DATA_DIR = str(Path(self.temp_dir.name) / "data")
        config.LOG_FILE = str(Path(config.DATA_DIR) / "chat_history.csv")
        config.CHARTS_DIR = str(Path(self.temp_dir.name) / "charts")
        config.GEMINI_API_KEY = ""
        web_app._bots.clear()
        web_app._rate_limits.clear()
        self.client = web_app.app.test_client()
        self.csrf_token = self._open_home(self.client)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _open_home(client):
        response = client.get("/")
        match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            response.get_data(as_text=True),
        )
        if response.status_code != 200 or match is None:
            raise AssertionError("Home page did not initialize the browser session.")
        return match.group(1)

    def _post(self, client, token, path, body):
        return client.post(
            path,
            json=body,
            headers={"X-CSRF-Token": token},
        )

    def test_home_page_assets_and_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("csrf-token", response.get_data(as_text=True))
        favicon = self.client.get("/static/favicon.svg")
        try:
            self.assertEqual(favicon.status_code, 200)
        finally:
            favicon.close()

    def test_render_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})

    def test_session_cookie_uses_http_only_same_site_and_secure_options(self):
        original_secure_setting = web_app.app.config["SESSION_COOKIE_SECURE"]
        web_app.app.config["SESSION_COOKIE_SECURE"] = True
        try:
            response = web_app.app.test_client().get("/")
            cookie = response.headers["Set-Cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)
            self.assertIn("Secure", cookie)
        finally:
            web_app.app.config["SESSION_COOKIE_SECURE"] = original_secure_setting

    def test_missing_api_key_returns_safe_configuration_error(self):
        response = self._post(
            self.client,
            self.csrf_token,
            "/api/ask",
            {"question": "hello"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("GEMINI_API_KEY", response.get_data(as_text=True))

    def test_invalid_questions_are_rejected(self):
        for value in (None, "", 123, "x" * 4001):
            response = self._post(
                self.client,
                self.csrf_token,
                "/api/ask",
                {"question": value},
            )
            self.assertEqual(response.status_code, 400)

    def test_write_routes_require_csrf_token(self):
        response = self.client.post("/api/reset", json={})
        self.assertEqual(response.status_code, 403)

    def test_oversized_request_is_rejected(self):
        response = self.client.post(
            "/api/ask",
            data="{" + (" " * (17 * 1024)) + "}",
            content_type="application/json",
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(response.status_code, 413)

    def test_successful_chat_uses_a_session_specific_bot_and_history(self):
        config.GEMINI_API_KEY = "unit-test-placeholder"
        other_client = web_app.app.test_client()
        other_csrf_token = self._open_home(other_client)
        with patch.object(web_app, "GeminiChatbot", FakeBot):
            response = self._post(
                self.client,
                self.csrf_token,
                "/api/ask",
                {"question": "hello"},
            )
            other_response = self._post(
                other_client,
                other_csrf_token,
                "/api/ask",
                {"question": "hello"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(response.get_json()["answer"], "Answer to: hello")
        self.assertEqual(other_response.get_json()["answer"], "Answer to: hello")
        with self.client.session_transaction() as browser_session:
            first_workspace = browser_session["workspace_id"]
        with other_client.session_transaction() as browser_session:
            second_workspace = browser_session["workspace_id"]
        self.assertIsNot(web_app._bots[first_workspace], web_app._bots[second_workspace])
        history = self.client.get("/api/history").get_json()["rows"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question"], "hello")

    def test_history_and_chart_images_are_isolated_between_sessions(self):
        other_client = web_app.app.test_client()
        other_csrf_token = self._open_home(other_client)
        with self.client.session_transaction() as browser_session:
            first_workspace = browser_session["workspace_id"]
        with other_client.session_transaction() as browser_session:
            second_workspace = browser_session["workspace_id"]
        self.assertNotEqual(first_workspace, second_workspace)

        chat_logger.save_chat("private first question", "answer", 0.1, first_workspace)
        chat_logger.save_chat("another first question", "answer", 0.2, first_workspace)
        first_rows = self.client.get("/api/history").get_json()["rows"]
        second_rows = other_client.get("/api/history").get_json()["rows"]
        self.assertEqual(
            [row["question"] for row in first_rows],
            ["private first question", "another first question"],
        )
        self.assertEqual(second_rows, [])

        first_stats = self.client.get("/api/stats").get_json()["text"]
        second_stats = other_client.get("/api/stats").get_json()["text"]
        self.assertIn("Total chats           : 2", first_stats)
        self.assertIn("No chats yet", second_stats)

        first_charts = self._post(self.client, self.csrf_token, "/api/charts", {}).get_json()
        second_charts = self._post(other_client, other_csrf_token, "/api/charts", {}).get_json()
        self.assertIn("1_response_time.png", first_charts["files"])
        self.assertEqual(second_charts["files"], [])

        chart_response = self.client.get("/charts/1_response_time.png")
        other_chart_response = other_client.get("/charts/1_response_time.png")
        try:
            self.assertEqual(chart_response.status_code, 200)
            self.assertEqual(chart_response.headers["Cache-Control"], "no-store")
            self.assertEqual(other_chart_response.status_code, 404)
        finally:
            chart_response.close()
            other_chart_response.close()
        self.assertEqual(self.client.get("/charts/../../app.py").status_code, 404)

    def test_api_rate_limit_is_applied_per_session(self):
        for _ in range(web_app.ASK_API_LIMIT):
            response = self._post(
                self.client,
                self.csrf_token,
                "/api/ask",
                {"question": ""},
            )
            self.assertEqual(response.status_code, 400)
        limited = self._post(
            self.client,
            self.csrf_token,
            "/api/ask",
            {"question": ""},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["Retry-After"], "60")

    def test_rate_limit_storage_is_bounded(self):
        now = time.monotonic()
        for index in range(web_app.MAX_RATE_LIMIT_BUCKETS):
            web_app._rate_limits[(f"{index:032x}", "ask")] = deque([now])

        response = self._post(
            self.client,
            self.csrf_token,
            "/api/ask",
            {"question": "hello"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertLessEqual(len(web_app._rate_limits), web_app.MAX_RATE_LIMIT_BUCKETS)

    def test_global_chat_limit_cannot_be_bypassed_with_new_sessions(self):
        for _ in range(web_app.GLOBAL_ASK_API_LIMIT):
            client = web_app.app.test_client()
            token = self._open_home(client)
            response = self._post(client, token, "/api/ask", {"question": ""})
            self.assertEqual(response.status_code, 400)

        new_client = web_app.app.test_client()
        new_token = self._open_home(new_client)
        limited = self._post(new_client, new_token, "/api/ask", {"question": ""})
        self.assertEqual(limited.status_code, 429)

    def test_gemini_failures_do_not_return_exception_text(self):
        class FailingBot(FakeBot):
            def ask(self, question):
                raise RuntimeError("sensitive-test-value")

        config.GEMINI_API_KEY = "unit-test-placeholder"
        with patch.object(web_app, "GeminiChatbot", FailingBot):
            response = self._post(
                self.client,
                self.csrf_token,
                "/api/ask",
                {"question": "hello"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive-test-value", response.get_data(as_text=True))

    def test_gemini_api_errors_do_not_return_provider_details_or_key(self):
        class FakeAPIError(Exception):
            code = 403

        class FailingBot(FakeBot):
            def ask(self, question):
                raise FakeAPIError("unit-test-private-provider-detail")

        config.GEMINI_API_KEY = "unit-test-private-api-key"
        with (
            patch.object(web_app, "GeminiChatbot", FailingBot),
            patch.object(web_app.errors, "APIError", FakeAPIError),
        ):
            response = self._post(
                self.client,
                self.csrf_token,
                "/api/ask",
                {"question": "hello"},
            )
        self.assertEqual(response.status_code, 502)
        body = response.get_data(as_text=True)
        self.assertNotIn("unit-test-private-provider-detail", body)
        self.assertNotIn("unit-test-private-api-key", body)

    def test_available_models_route_remains_working(self):
        config.GEMINI_API_KEY = "unit-test-placeholder"
        with patch.object(web_app, "GeminiChatbot", FakeBot):
            response = self.client.get("/api/models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["models"], ["models/test-model"])

    def test_reset_route_remains_available_for_a_session(self):
        config.GEMINI_API_KEY = "unit-test-placeholder"
        with patch.object(web_app, "GeminiChatbot", FakeBot):
            response = self._post(
                self.client,
                self.csrf_token,
                "/api/reset",
                {},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
