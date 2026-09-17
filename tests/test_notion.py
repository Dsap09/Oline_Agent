"""
Unit test suite untuk fitur Integrasi Notion (Catatan vs Memori).
Jalankan dengan: python -m unittest tests/test_notion.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import _extract_notion_note, detect_intent, is_rule_message
from src.notion import (
    add_notion_property,
    extract_database_id,
    save_memory_to_notion,
    save_note_to_notion,
)
from src.tools import get_tools_for_intent


class TestNotionIntegration(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["NOTION_API_KEY"] = "ntn_mock_token_12345"
        os.environ["NOTION_DATABASE_ID"] = "3ceec30101df806fa6ddf65ab5aa6e40"
        os.environ["NOTION_MEMORY_DATABASE_ID"] = "9fffc30101df806fa6ddf65ab5aa9999"

    def test_extract_database_id(self):
        """Tes fungsi extract_database_id dari berbagai format input Notion."""
        url = "https://app.notion.com/p/3ceec30101df806fa6ddf65ab5aa6e40?v=3ceec30101df80a7be75000cbcebfb19&source=copy_link"
        self.assertEqual(extract_database_id(url), "3ceec30101df806fa6ddf65ab5aa6e40")

        raw_hex = "3ceec30101df806fa6ddf65ab5aa6e40"
        self.assertEqual(extract_database_id(raw_hex), "3ceec30101df806fa6ddf65ab5aa6e40")

        uuid_str = "3ceec301-01df-806f-a6dd-f65ab5aa6e40"
        self.assertEqual(extract_database_id(uuid_str), "3ceec30101df806fa6ddf65ab5aa6e40")

    def test_detect_intent_notion(self):
        """Tes pendeteksi intent notion pada bot.py."""
        self.assertEqual(detect_intent("catat ke notion ide riset"), "notion")
        self.assertEqual(detect_intent("simpan ke notion: halo"), "notion")
        self.assertEqual(detect_intent("buat catatan di notion"), "notion")
        self.assertEqual(detect_intent("tambah kolom file di notion"), "notion")
        self.assertEqual(detect_intent("simpan aturan ke notion"), "notion")

    def test_is_rule_message_differentiation(self):
        """Memastikan is_rule_message membedakan antara aturan/preferensi vs catatan umum."""
        self.assertTrue(is_rule_message("ingat bahwa saya suka kopi espresso"))
        self.assertTrue(is_rule_message("mulai sekarang panggil saya Budi"))
        self.assertTrue(is_rule_message("simpan aturan ini ke notion"))

        # Catatan umum tidak boleh dianggap sebagai aturan sistem
        self.assertFalse(is_rule_message("catat ke notion ide artikel AI"))
        self.assertFalse(is_rule_message("simpan catatan rapat hari ini ke notion"))

    def test_get_tools_for_intent_notion(self):
        """Tes pengambilan deklarasi tool untuk intent notion (harus berisi save_note_to_notion dan save_memory_to_notion)."""
        tools = get_tools_for_intent("notion")
        tool_names = [t["name"] for t in tools]
        self.assertIn("save_note_to_notion", tool_names)
        self.assertIn("save_memory_to_notion", tool_names)
        self.assertIn("add_notion_property", tool_names)

    @patch("httpx.AsyncClient.patch", new_callable=AsyncMock)
    async def test_add_notion_property_success(self, mock_patch):
        """Tes add_notion_property berhasil menambahkan kolom ke Notion database."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_patch.return_value = mock_resp

        res = await add_notion_property(name="File", property_type="files")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["property_name"], "File")
        self.assertIn("berhasil ditambahkan", res["message"])

    @patch("httpx.AsyncClient.get", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_save_note_to_notion_targets_notes_db(self, mock_post, mock_get):
        """Skenario 1: Tes save_note_to_notion menargetkan NOTION_DATABASE_ID (Database Catatan)."""
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {
            "properties": {
                "Title": {"type": "title"},
                "Kategori": {"type": "select"},
            }
        }
        mock_get.return_value = mock_get_resp

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "id": "page-12345",
            "url": "https://www.notion.so/Ide-riset-AI-3ceec30101df806fa6ddf65ab5aa6e40",
        }
        mock_post.return_value = mock_post_resp

        res = await save_note_to_notion(
            title="Ide riset AI agent",
            content="Membahas autonomous agent untuk skripsi.",
            category="Riset",
        )
        self.assertEqual(res["status"], "success")
        mock_post.assert_called_once()
        post_payload = mock_post.call_args[1]["json"]
        # Memastikan parent database_id adalah NOTION_DATABASE_ID
        self.assertEqual(post_payload["parent"]["database_id"], "3ceec30101df806fa6ddf65ab5aa6e40")

    @patch("src.notion.read_memory_from_notion", new_callable=AsyncMock)
    @patch("src.notion._inspect_and_ensure_memory_schema", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_save_memory_to_notion_targets_memory_db(self, mock_post, mock_inspect, mock_read):
        """Skenario 2: Tes save_memory_to_notion menargetkan NOTION_MEMORY_DATABASE_ID (Database Memori)."""
        mock_inspect.return_value = ("Title", "Jenis", "Tanggal", "Isi")
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"id": "page-memory-999"}
        mock_post.return_value = mock_post_resp

        res = await save_memory_to_notion(
            title="Preferensi Bahasa",
            content="Selalu gunakan bahasa formal yang profesional.",
            memory_type="Aturan",
        )
        self.assertEqual(res, "Memori berhasil disimpan.")
        mock_post.assert_called_once()
        post_payload = mock_post.call_args[1]["json"]
        # Memastikan parent database_id adalah NOTION_MEMORY_DATABASE_ID
        self.assertEqual(post_payload["parent"]["database_id"], "9fffc30101df806fa6ddf65ab5aa9999")


class TestExtractNotionNote(unittest.TestCase):
    """Tes ekstraksi title/content untuk simpan catatan Notion."""

    def test_extract_simpan_ke_notion(self):
        extracted = _extract_notion_note("simpan ke catatan Notion: besok kuliah pagi jam 7")
        self.assertIsNotNone(extracted)
        title, content = extracted
        self.assertIn("besok kuliah pagi jam 7", content)
        self.assertTrue(title)

    def test_extract_catat_ke_notion(self):
        extracted = _extract_notion_note("catat ke notion: beli telur dan roti")
        self.assertIsNotNone(extracted)
        _, content = extracted
        self.assertIn("beli telur dan roti", content)

    def test_memory_request_not_note(self):
        # Permintaan aturan/preferensi → bukan simpan catatan umum
        self.assertIsNone(_extract_notion_note("simpan aturan: selalu panggil saya kak"))
        self.assertIsNone(_extract_notion_note("ingat bahwa saya suka kopi"))

    def test_non_save_request(self):
        self.assertIsNone(_extract_notion_note("apa isi database notion?"))


if __name__ == "__main__":
    unittest.main()

