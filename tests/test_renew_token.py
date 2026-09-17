"""
Unit test untuk fitur Token Renewal Assistant (brief.md) & Vercel env helper.
Jalankan dengan: python -m unittest tests/test_renew_token.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import FITUR_LIST, RENEWABLE_TOKENS
from src.tools import TOOL_DECLARATIONS, TOOL_EXECUTORS, TOOLS_BY_INTENT, renew_token, resolve_token_service


class TestRenewableRegistry(unittest.TestCase):
    """Tes daftar token renewable di config.py."""

    def test_renewable_tokens_are_in_token_registry(self):
        from src.config import TOKEN_REGISTRY
        for env_key in RENEWABLE_TOKENS:
            self.assertIn(env_key, TOKEN_REGISTRY)

    def test_renew_token_in_fitur_list(self):
        self.assertIn("renew_token", FITUR_LIST)


class TestToolRegistration(unittest.TestCase):
    """Tes registrasi tool renew_token."""

    def test_declared_in_tool_declarations(self):
        names = [t.get("name") for t in TOOL_DECLARATIONS if isinstance(t, dict)]
        self.assertIn("renew_token", names)

    def test_mapped_in_tools_by_intent(self):
        names = [
            t.get("name") if isinstance(t, dict) else t
            for t in TOOLS_BY_INTENT.get("renew_token", [])
        ]
        self.assertIn("renew_token", names)

    def test_registered_in_executors(self):
        self.assertIn("renew_token", TOOL_EXECUTORS)
        self.assertIs(TOOL_EXECUTORS["renew_token"], renew_token)


class TestResolveService(unittest.TestCase):
    """Tes pemetaan nama layanan ke env key."""

    def test_resolve_drive(self):
        env_key, label = resolve_token_service("drive")
        self.assertEqual(env_key, "GOOGLE_DRIVE_REFRESH_TOKEN")
        self.assertIn("Drive", label)

    def test_resolve_calendar(self):
        env_key, _ = resolve_token_service("calendar")
        self.assertEqual(env_key, "GOOGLE_CALENDAR_REFRESH_TOKEN")

    def test_resolve_unknown(self):
        env_key, label = resolve_token_service("tidak-ada")
        self.assertIsNone(env_key)
        self.assertIsNone(label)

    def test_resolve_empty(self):
        env_key, label = resolve_token_service("")
        self.assertIsNone(env_key)
        self.assertIsNone(label)


class TestRenewToken(unittest.IsolatedAsyncioTestCase):
    """Tes perilaku tool renew_token."""

    async def test_guide_without_token_for_renewable(self):
        result = await renew_token(service="drive")
        self.assertIn("oauthplayground", result)
        self.assertIn("refresh_token", result)

    async def test_guide_for_static_key(self):
        result = await renew_token(service="vercel")
        self.assertIn("API key statis", result)

    async def test_unknown_service(self):
        result = await renew_token(service="bogus")
        self.assertIn("tidak dikenal", result)

    async def test_unknown_service_label(self):
        result = await renew_token(service="xxx")
        self.assertIn("tidak dikenal", result)

    @patch("src.kv.save_token", new_callable=AsyncMock)
    @patch("src.kv.reset_failure_count", new_callable=AsyncMock)
    @patch("src.kv.set_user_feature", new_callable=AsyncMock)
    async def test_with_new_token_saves_to_kv(self, mock_feat, mock_reset, mock_save):
        mock_save.return_value = True
        result = await renew_token(service="drive", new_token="1//abc123")
        mock_save.assert_awaited_once_with("drive", "1//abc123")
        self.assertIn("berhasil diperbarui", result)

    @patch("src.kv.save_token", new_callable=AsyncMock)
    async def test_with_new_token_save_fail(self, mock_save):
        mock_save.return_value = False
        result = await renew_token(service="drive", new_token="1//abc123")
        self.assertIn("Gagal menyimpan token", result)


if __name__ == "__main__":
    unittest.main()
