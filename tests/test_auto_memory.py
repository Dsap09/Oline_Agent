"""
Unit test suite untuk fitur Auto-Save Memori proaktif ke Notion.
Jalankan dengan: python -m unittest tests/test_auto_memory.py
"""

import json
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set token sebelum import src.bot agar create_application bisa dibangun di tes
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"

from src.memory import (
    MEMORY_CATEGORIES,
    MAX_PER_DAY,
    _best_match,
    _extract_json_array,
    _has_new_info,
    _is_worth_detecting,
    _rate_limit_ok,
    _similarity,
    detect_memories,
    get_memory_settings,
    maybe_autosave,
    set_memory_settings,
)
from src.notion import query_memory_entries


class TestMemorySettings(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_defaults(self, mock_cache):
        """Default: aktif, semua kategori, notifikasi aktif."""
        mock_cache.return_value = None
        settings = await get_memory_settings(1)
        self.assertTrue(settings["enabled"])
        self.assertTrue(settings["notify"])
        self.assertEqual(settings["categories"], MEMORY_CATEGORIES)

    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_set_enabled_and_categories(self, mock_get, mock_set):
        mock_get.return_value = json.dumps(
            {"enabled": True, "categories": ["fakta"], "notify": True}
        )
        ok = await set_memory_settings(1, enabled=False, categories=["fakta", "aturan"])
        self.assertTrue(ok)
        payload = json.loads(mock_set.await_args.args[1])
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["categories"], ["fakta", "aturan"])

    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_invalid_categories_ignored(self, mock_cache):
        mock_cache.return_value = json.dumps(
            {"enabled": True, "categories": ["sampah"], "notify": False}
        )
        settings = await get_memory_settings(1)
        self.assertEqual(settings["categories"], MEMORY_CATEGORIES)
        self.assertFalse(settings["notify"])


class TestMemoryRateLimit(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_interval_blocks(self, mock_get):
        """Baru saja menyimpan -> ditolak sampai 10 menit."""
        mock_get.return_value = str(time.time())
        self.assertFalse(await _rate_limit_ok(1))

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_daily_quota_blocks(self, mock_get, mock_kv):
        mock_get.return_value = None
        mock_kv.return_value = {"result": str(MAX_PER_DAY)}
        self.assertFalse(await _rate_limit_ok(1))

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_allows_when_free(self, mock_get, mock_kv):
        mock_get.return_value = None
        mock_kv.return_value = {"result": "1"}
        self.assertTrue(await _rate_limit_ok(1))


class TestDetectorParsing(unittest.IsolatedAsyncioTestCase):

    def test_extract_json_array_with_fence(self):
        raw = '```json\n[{"kategori":"fakta","judul":"Kopi","isi":"Doni suka kopi.","confidence":0.9}]\n```'
        data = _extract_json_array(raw)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["judul"], "Kopi")

    def test_extract_json_array_empty(self):
        self.assertEqual(_extract_json_array("[]"), [])
        self.assertEqual(_extract_json_array("tidak ada"), [])

    @patch("src.memory._detect_with_gemini", new_callable=AsyncMock)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_detect_filters_confidence_and_category(self, mock_groq, mock_gemini):
        mock_groq.return_value = (
            '[{"kategori":"fakta","judul":"Kopi","isi":"Doni suka kopi hitam.","confidence":0.9},'
            '{"kategori":"fakta","judul":"Ragu","isi":"Mungkin suka teh.","confidence":0.2},'
            '{"kategori":"ngawur","judul":"X","isi":"Y","confidence":0.99}]'
        )
        res = await detect_memories("Aku suka kopi hitam tanpa gula", "Noted!", "Doni")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["judul"], "Kopi")
        mock_gemini.assert_not_called()

    @patch("src.memory._detect_with_gemini", new_callable=AsyncMock)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_detect_fallback_to_gemini(self, mock_groq, mock_gemini):
        mock_groq.side_effect = Exception("groq down")
        mock_gemini.return_value = (
            '[{"kategori":"aturan","judul":"Panggilan","isi":"Panggil user Doni.","confidence":0.8}]'
        )
        res = await detect_memories("panggil aku doni mulai sekarang", "Oke, Doni.", "Doni")
        self.assertEqual(res[0]["kategori"], "aturan")
        mock_gemini.assert_awaited()


class TestDedup(unittest.TestCase):

    def test_similarity(self):
        self.assertGreater(
            _similarity("Doni suka kopi hitam", "Doni suka kopi hitam tanpa gula"), 0.5
        )
        self.assertLess(_similarity("Doni suka kopi", "Cuaca Jakarta cerah"), 0.3)

    def test_best_match_and_new_info(self):
        entries = [{"id": "p1", "title": "Kopi", "isi": "Doni suka kopi hitam."}]
        cand = {
            "kategori": "fakta",
            "judul": "Kopi",
            "isi": "Doni suka kopi hitam tanpa gula.",
        }
        match, score = _best_match(cand, entries)
        self.assertEqual(match["id"], "p1")
        self.assertGreaterEqual(score, 0.85)
        self.assertTrue(_has_new_info(cand, match))

    def test_no_new_info_for_identical(self):
        entry = {"id": "p1", "title": "Kopi", "isi": "Doni suka kopi hitam tanpa gula."}
        cand = {"kategori": "fakta", "judul": "Kopi", "isi": "Doni suka kopi hitam tanpa gula."}
        self.assertFalse(_has_new_info(cand, entry))

    def test_worth_detecting_prefilter(self):
        self.assertFalse(_is_worth_detecting("halo"))
        self.assertFalse(_is_worth_detecting("/memory on"))
        self.assertFalse(_is_worth_detecting("makasih"))
        self.assertTrue(_is_worth_detecting("aku suka kopi hitam tanpa gula"))


class TestAutoSaveOrchestration(unittest.IsolatedAsyncioTestCase):

    SETTINGS = {
        "enabled": True,
        "categories": list(MEMORY_CATEGORIES),
        "notify": True,
    }

    async def test_disabled_does_nothing(self):
        with patch("src.memory.get_memory_settings", new_callable=AsyncMock) as mock_get, \
             patch("src.memory.detect_memories", new_callable=AsyncMock) as mock_detect:
            mock_get.return_value = {**self.SETTINGS, "enabled": False}
            res = await maybe_autosave(1, "aku suka kopi hitam tanpa gula", "Noted!", "Doni")
            self.assertEqual(res, [])
            mock_detect.assert_not_called()

    async def test_saves_new_memory(self):
        with patch("src.memory.get_memory_settings", new_callable=AsyncMock) as mock_get, \
             patch("src.memory.detect_memories", new_callable=AsyncMock) as mock_detect, \
             patch("src.memory._rate_limit_ok", new_callable=AsyncMock, return_value=True), \
             patch("src.memory._register_save", new_callable=AsyncMock) as mock_register, \
             patch("src.memory._notify", new_callable=AsyncMock) as mock_notify, \
             patch("src.memory.log_memory_action", new_callable=AsyncMock) as mock_audit, \
             patch("src.notion.query_memory_entries", new_callable=AsyncMock, return_value=[]) as mock_query, \
             patch("src.notion.save_memory_entry", new_callable=AsyncMock) as mock_save, \
             patch("src.notion.update_memory_page", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = dict(self.SETTINGS)
            mock_detect.return_value = [{
                "kategori": "fakta",
                "judul": "Suka kopi hitam",
                "isi": "Doni suka kopi hitam tanpa gula.",
                "confidence": 0.9,
            }]
            mock_save.return_value = {"status": "success", "page_id": "p1"}

            res = await maybe_autosave(1, "Aku suka kopi hitam tanpa gula ya", "Noted!", "Doni")

            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["aksi"], "save")
            mock_save.assert_awaited()
            mock_update.assert_not_called()
            mock_register.assert_awaited()
            mock_notify.assert_awaited()
            mock_audit.assert_awaited()
            mock_query.assert_awaited()

    async def test_duplicate_is_skipped(self):
        existing = [{"id": "p1", "title": "Suka kopi hitam", "isi": "Doni suka kopi hitam tanpa gula."}]
        with patch("src.memory.get_memory_settings", new_callable=AsyncMock) as mock_get, \
             patch("src.memory.detect_memories", new_callable=AsyncMock) as mock_detect, \
             patch("src.memory._rate_limit_ok", new_callable=AsyncMock, return_value=True), \
             patch("src.memory._register_save", new_callable=AsyncMock) as mock_register, \
             patch("src.memory._notify", new_callable=AsyncMock) as mock_notify, \
             patch("src.memory.log_memory_action", new_callable=AsyncMock), \
             patch("src.notion.query_memory_entries", new_callable=AsyncMock, return_value=existing), \
             patch("src.notion.save_memory_entry", new_callable=AsyncMock) as mock_save, \
             patch("src.notion.update_memory_page", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = dict(self.SETTINGS)
            mock_detect.return_value = [{
                "kategori": "fakta",
                "judul": "Suka kopi hitam",
                "isi": "Doni suka kopi hitam tanpa gula.",
                "confidence": 0.9,
            }]
            res = await maybe_autosave(1, "Aku suka kopi hitam tanpa gula ya", "Noted!", "Doni")
            self.assertEqual(res[0]["aksi"], "skip")
            mock_save.assert_not_called()
            mock_update.assert_not_called()
            mock_register.assert_not_called()
            mock_notify.assert_not_called()

    async def test_near_duplicate_with_new_info_updates(self):
        existing = [{"id": "p1", "title": "Suka kopi hitam", "isi": "Doni suka kopi hitam."}]
        with patch("src.memory.get_memory_settings", new_callable=AsyncMock) as mock_get, \
             patch("src.memory.detect_memories", new_callable=AsyncMock) as mock_detect, \
             patch("src.memory._rate_limit_ok", new_callable=AsyncMock, return_value=True), \
             patch("src.memory._register_save", new_callable=AsyncMock) as mock_register, \
             patch("src.memory._notify", new_callable=AsyncMock), \
             patch("src.memory.log_memory_action", new_callable=AsyncMock), \
             patch("src.notion.query_memory_entries", new_callable=AsyncMock, return_value=existing), \
             patch("src.notion.save_memory_entry", new_callable=AsyncMock) as mock_save, \
             patch("src.notion.update_memory_page", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = dict(self.SETTINGS)
            mock_detect.return_value = [{
                "kategori": "fakta",
                "judul": "Suka kopi hitam",
                "isi": "Doni suka kopi hitam tanpa gula.",
                "confidence": 0.9,
            }]
            mock_update.return_value = {"status": "success", "page_id": "p1"}
            res = await maybe_autosave(1, "Aku suka kopi hitam tanpa gula ya", "Noted!", "Doni")
            self.assertEqual(res[0]["aksi"], "update")
            mock_update.assert_awaited()
            mock_save.assert_not_called()
            mock_register.assert_awaited()


class TestNotionMemoryHelpers(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["NOTION_API_KEY"] = "ntn_mock_token_12345"
        os.environ["NOTION_MEMORY_DATABASE_ID"] = "9fffc30101df806fa6ddf65ab5aa9999"

    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.get", new_callable=AsyncMock)
    async def test_query_memory_entries(self, mock_get, mock_post):
        schema_resp = MagicMock()
        schema_resp.status_code = 200
        schema_resp.json.return_value = {
            "properties": {
                "Title": {"type": "title"},
                "Jenis": {"type": "select"},
                "Tanggal": {"type": "date"},
                "Isi": {"type": "rich_text"},
                "Sumber": {"type": "rich_text"},
                "Confidence": {"type": "number"},
            }
        }
        mock_get.return_value = schema_resp

        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {
            "results": [
                {
                    "id": "p1",
                    "url": "https://notion.so/p1",
                    "properties": {
                        "Title": {"title": [{"text": {"content": "Suka kopi"}}]},
                        "Jenis": {"select": {"name": "Fakta"}},
                        "Isi": {"rich_text": [{"text": {"content": "Doni suka kopi."}}]},
                        "Sumber": {"rich_text": [{"text": {"content": "chat"}}]},
                        "Confidence": {"number": 0.9},
                    },
                }
            ]
        }
        mock_post.return_value = query_resp

        entries = await query_memory_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Suka kopi")
        self.assertEqual(entries[0]["jenis"], "Fakta")
        self.assertEqual(entries[0]["isi"], "Doni suka kopi.")
        self.assertEqual(entries[0]["confidence"], 0.9)


class TestMemoryCommand(unittest.TestCase):

    def test_help_has_memory(self):
        from src.bot import COMMAND_HELP_DETAIL, COMMAND_HELP_TEXT
        self.assertIn("memory", COMMAND_HELP_DETAIL)
        self.assertIn("/memory", COMMAND_HELP_TEXT)

    def test_memory_command_registered(self):
        from telegram.ext import CommandHandler
        from src.bot import create_application

        app = create_application()
        registered = set()
        for group in app.handlers.values():
            for handler in group:
                if isinstance(handler, CommandHandler):
                    registered.update(handler.commands)
        self.assertIn("memory", registered)


if __name__ == "__main__":
    unittest.main()
