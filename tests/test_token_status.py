"""
Unit test untuk tool check_token_status (Token Health Check, brief.md).
Jalankan dengan: python -m unittest tests/test_token_status.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import TOKEN_REGISTRY
from src.tools import (
    TOOL_DECLARATIONS,
    TOOL_EXECUTORS,
    TOOLS_BY_INTENT,
    check_token_status,
    check_token_status_report,
)


class TestTokenRegistry(unittest.TestCase):
    """Tes struktur TOKEN_REGISTRY di config.py."""

    def test_registry_has_expected_keys(self):
        for key in [
            "GROQ_API_KEY",
            "GEMINI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "VERCEL_API_TOKEN",
            "NOTION_API_KEY",
            "GITHUB_TOKEN",
            "GOOGLE_DRIVE_REFRESH_TOKEN",
            "ERINE_API_KEY",
        ]:
            self.assertIn(key, TOKEN_REGISTRY)

    def test_registry_entries_have_service_and_test(self):
        for env_key, info in TOKEN_REGISTRY.items():
            self.assertIn("service", info)
            self.assertIn("test_url", info)
            self.assertTrue(info["service"])


class TestToolRegistration(unittest.TestCase):
    """Tes registrasi tool check_token_status di tools.py."""

    def test_declared_in_tool_declarations(self):
        names = [t.get("name") for t in TOOL_DECLARATIONS if isinstance(t, dict)]
        self.assertIn("check_token_status", names)

    def test_mapped_in_tools_by_intent(self):
        names = [
            t.get("name") if isinstance(t, dict) else t
            for t in TOOLS_BY_INTENT.get("cek_token", [])
        ]
        self.assertIn("check_token_status", names)

    def test_registered_in_executors(self):
        self.assertIn("check_token_status", TOOL_EXECUTORS)
        self.assertIs(TOOL_EXECUTORS["check_token_status"], check_token_status)


class TestCheckTokenStatus(unittest.IsolatedAsyncioTestCase):
    """Tes eksekusi check_token_status dengan network dimock."""

    def setUp(self):
        for key in TOKEN_REGISTRY:
            os.environ.pop(key, None)

    @patch("src.tools.set_cache", new_callable=AsyncMock)
    @patch("src.tools.get_cache", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.get")
    async def test_reports_valid_and_unconfigured(self, mock_get, mock_cache, mock_set_cache):
        mock_cache.return_value = None
        os.environ["GROQ_API_KEY"] = "sk-test-groq"
        os.environ["GEMINI_API_KEY"] = "sk-test-gemini"

        async def fake_get(url, headers=None):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_get.side_effect = fake_get

        result = await check_token_status()

        self.assertIn("✅ Groq: valid", result)
        self.assertIn("✅ Gemini: valid", result)
        self.assertIn("⚠️ Mistral: tidak dikonfigurasi", result)
        # Tidak boleh membocorkan isi token
        self.assertNotIn("sk-test-groq", result)
        self.assertNotIn("sk-test-gemini", result)

    @patch("src.tools.set_cache", new_callable=AsyncMock)
    @patch("src.tools.get_cache", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.get")
    async def test_reports_unauthorized(self, mock_get, mock_cache, mock_set_cache):
        mock_cache.return_value = None
        os.environ["GROQ_API_KEY"] = "sk-bad"

        async def fake_get(url, headers=None):
            resp = MagicMock()
            resp.status_code = 401
            return resp

        mock_get.side_effect = fake_get

        result = await check_token_status()
        self.assertIn("❌ Groq: unauthorized", result)

    @patch("src.tools.set_cache", new_callable=AsyncMock)
    @patch("src.tools.get_cache", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.get")
    async def test_filter_service(self, mock_get, mock_cache, mock_set_cache):
        mock_cache.return_value = None
        os.environ["GROQ_API_KEY"] = "sk-test-groq"

        async def fake_get(url, headers=None):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_get.side_effect = fake_get

        result = await check_token_status(service_filter="Groq")
        self.assertIn("✅ Groq: valid", result)
        self.assertNotIn("Gemini", result)


class TestCheckTokenStatusReport(unittest.IsolatedAsyncioTestCase):
    """Tes check_token_status_report (output terstruktur untuk endpoint JSON)."""

    def setUp(self):
        for key in TOKEN_REGISTRY:
            os.environ.pop(key, None)

    @patch("src.kv.get_token", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.get")
    async def test_report_structure(self, mock_get, mock_token):
        mock_token.return_value = None
        os.environ["GROQ_API_KEY"] = "sk-groq"

        async def fake_get(url, headers=None):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_get.side_effect = fake_get

        report = await check_token_status_report()
        self.assertIsInstance(report, list)
        by_service = {r["service"]: r for r in report}
        self.assertEqual(by_service["Groq"]["status"], "valid")
        self.assertIn("env_key", by_service["Groq"])
        self.assertEqual(by_service["Mistral"]["status"], "unconfigured")
        # Tidak membocorkan token
        for r in report:
            self.assertNotIn("sk-groq", str(r))

    @patch("src.kv.get_token", new_callable=AsyncMock)
    async def test_report_unconfigured_oauth(self, mock_token):
        mock_token.return_value = None
        report = await check_token_status_report()
        by_service = {r["service"]: r for r in report}
        self.assertEqual(by_service["Google Drive"]["status"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
