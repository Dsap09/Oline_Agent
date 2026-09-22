"""
Unit test suite untuk Slash Command Oline (Fase 1+2).
Jalankan dengan: python -m unittest tests/test_commands.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set token sebelum import src.bot agar create_application bisa dibangun di tes
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"

from src.bot import COMMAND_HELP_DETAIL
from src.kv import clear_history, get_persona, set_persona
from src.personas import PERSONA_STYLES


class TestClearHistory(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_clear_history_issues_del(self, mock_kv):
        """Tes clear_history mengirim perintah DEL dengan key yang benar."""
        mock_kv.return_value = {"result": "1"}
        res = await clear_history(chat_id=12345)
        self.assertTrue(res)
        mock_kv.assert_called_once()
        cmd = mock_kv.call_args[0][0]
        self.assertEqual(cmd[0], "DEL")
        self.assertEqual(cmd[1], "history:12345")


class TestCommandHelp(unittest.TestCase):

    def test_help_detail_contains_intended_commands(self):
        """Tes daftar bantuan memuat command yang diimplementasikan."""
        for cmd in ("start", "help", "menu", "clear", "batal", "status",
                    "cuaca", "saham", "cari", "gambar", "kuota", "jurnal",
                    "list", "preview", "landing", "deploy", "tasks",
                    "fitur", "aktifkan", "matikan", "log", "persona"):
            self.assertIn(cmd, COMMAND_HELP_DETAIL)


class TestPersona(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_get_persona_default(self, mock_kv):
        """Tes get_persona mengembalikan 'profesional' bila belum diset."""
        mock_kv.return_value = {"result": None}
        res = await get_persona(12345)
        self.assertEqual(res, "profesional")

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_set_persona_issues_set(self, mock_kv):
        """Tes set_persona menyimpan gaya dengan key persona:<chat_id>."""
        mock_kv.return_value = {"result": "OK"}
        res = await set_persona(12345, "genz")
        self.assertTrue(res)
        cmd = mock_kv.call_args[0][0]
        self.assertEqual(cmd[0], "SET")
        self.assertEqual(cmd[1], "persona:12345")
        self.assertEqual(cmd[2], "genz")

    def test_persona_styles_available(self):
        """Tes gaya persona yang didukung tersedia."""
        for style in ("profesional", "genz", "ramah", "ringkas"):
            self.assertIn(style, PERSONA_STYLES)


class TestPersonaInPrompt(unittest.IsolatedAsyncioTestCase):
    """Uji end-to-end: persona terpilih diterapkan ke system prompt yang dibangun."""

    @patch("src.kv.get_persona", new_callable=AsyncMock, return_value="genz")
    @patch("src.gemini._read_grounding_context", new_callable=AsyncMock, return_value="")
    @patch("src.notion.read_memory_from_notion", new_callable=AsyncMock, return_value="")
    async def test_persona_genz_appended(self, mock_notion, mock_ground, mock_persona):
        from src.gemini import _build_system_prompt_async
        prompt = await _build_system_prompt_async("", user_name="Teman", chat_id=12345)
        self.assertIn("Gaya Komunikasi Pilihan Pengguna", prompt)
        self.assertIn("Gen-Z", prompt)

    @patch("src.kv.get_persona", new_callable=AsyncMock, return_value="ringkas")
    @patch("src.gemini._read_grounding_context", new_callable=AsyncMock, return_value="")
    @patch("src.notion.read_memory_from_notion", new_callable=AsyncMock, return_value="")
    async def test_persona_ringkas_appended(self, mock_notion, mock_ground, mock_persona):
        from src.gemini import _build_system_prompt_async
        prompt = await _build_system_prompt_async("", user_name="Teman", chat_id=12345)
        self.assertIn("super ringkas", prompt)


class TestApplicationRegistration(unittest.TestCase):

    def test_create_application_registers_commands(self):
        """Tes create_application mendaftarkan semua CommandHandler baru."""
        from telegram.ext import CommandHandler

        from src.bot import create_application
        app = create_application()

        registered = set()
        for group in app.handlers.values():
            for handler in group:
                if isinstance(handler, CommandHandler):
                    registered.update(handler.commands)
        for cmd in ("start", "help", "menu", "clear", "batal", "status",
                    "cuaca", "saham", "cari", "gambar", "kuota", "jurnal",
                    "list", "preview", "landing", "deploy", "tasks",
                    "fitur", "aktifkan", "matikan", "log", "persona"):
            self.assertIn(cmd, registered)


if __name__ == "__main__":
    unittest.main()
