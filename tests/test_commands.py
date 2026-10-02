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

from src.bot import COMMAND_HELP_DETAIL, detect_intent, detect_intent_async, is_fix_request
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
                    "cuaca", "saham", "cari", "gambar", "kuota",
                    "pengeluaran",
                    "list", "preview", "landing", "deploy", "tasks",
                    "fitur", "aktifkan", "matikan", "log", "persona", "models", "memory"):
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


class TestFixRequestDetection(unittest.TestCase):
    """Kata 'solusi'/'fix' di pesan biasa tidak boleh memicu alur perbaikan error."""

    def test_landing_prompt_with_solusi_not_fix(self):
        prompt = (
            "Buatkan landing page modern dan profesional untuk startup saya bernama Dministar, "
            "yang bergerak di bidang otomasi perusahaan berbasis AI Agent. Gunakan gaya clean "
            "dan tech-savvy. Tone profesional namun ramah, fokus pada solusi hemat waktu, "
            "efisiensi, dan skalabilitas."
        )
        self.assertFalse(is_fix_request(prompt))

    def test_casual_solusi_not_fix(self):
        self.assertFalse(is_fix_request("aku suka solusi yang kamu kasih, makasih ya"))
        self.assertFalse(is_fix_request("tolong beri solusi terbaik untuk bisnis saya"))

    def test_short_imperative_detected(self):
        self.assertTrue(is_fix_request("perbaiki"))
        self.assertTrue(is_fix_request("benerin dong"))
        self.assertTrue(is_fix_request("coba fix"))

    def test_explicit_error_phrase_detected(self):
        self.assertTrue(is_fix_request("tolong perbaiki error login di website"))
        self.assertTrue(is_fix_request("fix bug yang muncul tadi"))
        self.assertTrue(is_fix_request("perbaiki kode deploy landing page-nya"))


class TestSahamFalsePositive(unittest.IsolatedAsyncioTestCase):
    """Pesan yang TIDAK menyebut saham tidak boleh ke-deteksi sebagai intent saham."""

    def test_detect_intent_not_saham_for_casual(self):
        self.assertIsNone(detect_intent("hi"))
        self.assertIsNone(detect_intent("halo lin"))
        self.assertIsNone(detect_intent("selamat sore"))

    def test_detect_intent_ambiguous_word_no_longer_saham(self):
        # "film" dulunya ticker di daftar saham → false positive; sekarang bukan.
        self.assertNotEqual(detect_intent("film apa yang bagus"), "saham")

    async def test_detect_intent_async_ignores_saham_history(self):
        # Meskipun riwayat pernah membahas saham, pesan non-saham tetap fast path.
        intent = await detect_intent_async("ada yang bisa kubantu?", chat_id=999)
        self.assertIsNone(intent)

    async def test_detect_intent_async_saham_real(self):
        self.assertEqual(await detect_intent_async("cek saham BBCA", chat_id=999), "saham")
        self.assertEqual(await detect_intent_async("berapa harga saham BBCA", chat_id=999), "saham")


class TestKeywordWordBoundary(unittest.TestCase):
    """Keyword dicocokkan sebagai kata utuh, bukan substring (cegah salah intent)."""

    def test_sans_serif_tidak_jadi_rekomendasi(self):
        prompt = (
            "Buatkan landing page modern untuk startup Dministar dengan font sans-serif modern, "
            "warna biru-putih, testimoni klien, CTA, dan footer."
        )
        self.assertEqual(detect_intent(prompt), "preview")

    def test_kata_seri_tetap_rekomendasi(self):
        self.assertEqual(detect_intent("rekomendasi seri terbaru dong"), "rekomendasi")

    def test_profile_bukan_drive(self):
        self.assertIsNone(detect_intent("cek profile akun pengguna"))
        self.assertEqual(detect_intent("cari file profile akun"), "drive")

    def test_sufiks_indonesia_masih_terdeteksi(self):
        self.assertEqual(detect_intent("filmnya dong"), "rekomendasi")
        self.assertEqual(detect_intent("deploykan website ini"), "deploy")
        self.assertEqual(detect_intent("kuotanya tinggal berapa?"), "kuota")

    def test_kata_nyangkut_lain_tidak_memicu(self):
        # "atm" di dalam "atmosphere", "live" di dalam "deliver".
        self.assertNotEqual(detect_intent("suasana atmosphere kantor"), "lokasi")
        self.assertNotEqual(detect_intent("tolong deliver pesan ini"), "deploy")


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
                    "pengeluaran",
                    "list", "preview", "landing", "deploy", "tasks",
                    "fitur", "aktifkan", "matikan", "log", "persona", "models", "memory"):
            self.assertIn(cmd, registered)


if __name__ == "__main__":
    unittest.main()
