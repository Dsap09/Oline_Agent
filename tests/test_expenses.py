"""
Unit test fitur pengeluaran Oline (src/expenses.py + integrasi registry).

Fokus pada logika murni (parser, deteksi, kategori, format) tanpa panggilan API.
Jalankan dengan: python -m unittest tests/test_expenses.py
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set token sebelum import src.bot agar create_application tidak error.
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"

from src.expenses import (  # noqa: E402
    build_confirm_text,
    build_recap_text,
    build_saved_text,
    categorize_local,
    detect_expense_entry,
    format_rupiah,
    format_short_date,
    get_draft,
    is_duplicate,
    parse_amount,
    parse_periode,
    parse_receipt_result,
    short_page_id,
)


class TestParseAmount(unittest.TestCase):

    def test_format_nominal_fleksibel(self):
        cases = {
            "25rb": 25000,
            "25 rb": 25000,
            "25K": 25000,
            "25k": 25000,
            "25ribu": 25000,
            "25.000": 25000,
            "25,000": 25000,
            "Rp 25.000": 25000,
            "Rp25.000": 25000,
            "25000": 25000,
            "1,5jt": 1500000,
            "1.5 juta": 1500000,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_nominal_tidak_valid(self):
        for raw in ("", "abc", "0", "Rp 0"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_amount(raw))


class TestDetectExpenseEntry(unittest.TestCase):

    def test_deteksi_pola_umum(self):
        detection = detect_expense_entry("kopi 25rb")
        self.assertIsNotNone(detection)
        item = detection["items"][0]
        self.assertEqual(item["nominal"], 25000)
        self.assertEqual(item["deskripsi"], "kopi")
        self.assertEqual(item["kategori"], "Makan & Minum")
        self.assertGreaterEqual(detection["confidence"], 0.85)

    def test_deteksi_transport(self):
        detection = detect_expense_entry("grab 15k")
        self.assertIsNotNone(detection)
        self.assertEqual(detection["items"][0]["kategori"], "Transport")
        self.assertEqual(detection["items"][0]["nominal"], 15000)

    def test_deteksi_dengan_kata_kerja(self):
        detection = detect_expense_entry("beli bensin 50rb")
        self.assertIsNotNone(detection)
        item = detection["items"][0]
        self.assertEqual(item["nominal"], 50000)
        self.assertEqual(item["deskripsi"], "bensin")

    def test_deteksi_kata_tadi(self):
        detection = detect_expense_entry("tadi makan siang 30rb")
        self.assertIsNotNone(detection)
        self.assertEqual(detection["items"][0]["deskripsi"], "makan siang")
        self.assertEqual(detection["items"][0]["nominal"], 30000)

    def test_kategori_rumah(self):
        detection = detect_expense_entry("bayar listrik 200rb")
        self.assertEqual(detection["items"][0]["kategori"], "Rumah")

    def test_multi_item_koma(self):
        detection = detect_expense_entry("kopi 25rb, makan 30rb")
        self.assertIsNotNone(detection)
        self.assertEqual(len(detection["items"]), 2)
        # Multi-item wajib dikonfirmasi (confidence dibatasi).
        self.assertLess(detection["confidence"], 0.85)

    def test_bare_number_dengan_kategori(self):
        detection = detect_expense_entry("makan 30")
        self.assertIsNotNone(detection)
        self.assertEqual(detection["items"][0]["nominal"], 30000)

    def test_false_positive_ditolak(self):
        for text in (
            "umur 25",
            "jam 10",
            "tahun 2024",
            "berapa pengeluaran bulan ini",
            "aku baca 1000 halaman",
        ):
            with self.subTest(text=text):
                self.assertIsNone(detect_expense_entry(text))


class TestKategoriLokal(unittest.TestCase):

    def test_map_keyword(self):
        cases = {
            "kopi": "Makan & Minum",
            "gojek ke kantor": "Transport",
            "belanja indomaret": "Belanja",
            "tagihan listrik": "Rumah",
            "beli obat": "Kesehatan",
            "nonton bioskop": "Hiburan",
            "buku kuliah": "Pendidikan",
        }
        for desc, expected in cases.items():
            with self.subTest(desc=desc):
                self.assertEqual(categorize_local(desc), expected)

    def test_tidak_dikenal(self):
        self.assertIsNone(categorize_local("xyz abc"))


class TestParseReceiptResult(unittest.TestCase):

    def test_json_valid(self):
        raw = json.dumps({
            "total": 87500,
            "toko": "Indomaret",
            "tanggal": "2026-09-30",
            "items": ["kopi", "susu"],
        })
        result = parse_receipt_result(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["nominal"], 87500)
        self.assertEqual(result["toko"], "Indomaret")
        self.assertEqual(result["deskripsi"], "Indomaret")
        self.assertEqual(result["tanggal"], "2026-09-30")
        self.assertIn("kopi", result["catatan"])

    def test_fallback_teks_regex(self):
        raw = "Total: Rp 87.500\nToko: Alfamart"
        result = parse_receipt_result(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["nominal"], 87500)
        self.assertEqual(result["toko"], "Alfamart")

    def test_bukan_struk(self):
        self.assertIsNone(parse_receipt_result("tidak ada struk di sini"))


class TestFormatDanRingkasan(unittest.TestCase):

    def test_format_rupiah(self):
        self.assertEqual(format_rupiah(25000), "Rp25.000")
        self.assertEqual(format_rupiah(1500000), "Rp1.500.000")
        self.assertEqual(format_rupiah(None), "Rp0")

    def test_format_short_date(self):
        self.assertEqual(format_short_date("2026-09-30"), "30 Sep 2026")

    def test_parse_periode(self):
        self.assertEqual(parse_periode("rekap hari ini")[2], "Hari Ini")
        self.assertEqual(parse_periode("rekap minggu ini")[2], "Minggu Ini")
        self.assertEqual(parse_periode("rekap bulan ini")[2], "Bulan Ini")

    def test_build_recap_text(self):
        entries = [
            {"nominal": 75000, "kategori": "Makan & Minum"},
            {"nominal": 25000, "kategori": "Transport"},
        ]
        text = build_recap_text(entries, "Bulan Ini")
        self.assertIn("Rp100.000", text)
        self.assertIn("Makan & Minum", text)
        self.assertIn("75%", text)

    def test_build_recap_kosong(self):
        self.assertIn("Belum ada", build_recap_text([], "Hari Ini"))

    def test_build_confirm_struk(self):
        draft = {"sumber": "struk", "items": [{
            "nominal": 87500,
            "deskripsi": "Indomaret",
            "toko": "Indomaret",
            "tanggal": "2026-09-30",
            "kategori": "Belanja",
        }]}
        text = build_confirm_text(draft)
        self.assertIn("Struk terbaca", text)
        self.assertIn("Rp87.500", text)

    def test_build_saved_text(self):
        text = build_saved_text([{
            "nominal": 25000,
            "deskripsi": "kopi",
            "kategori": "Makan & Minum",
        }], 25000)
        self.assertIn("Tercatat", text)
        self.assertIn("Total hari ini: Rp25.000", text)

    def test_short_page_id(self):
        self.assertEqual(short_page_id("1234abcd-ef567890"), "567890")


class TestStateKV(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv.del_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value=None)
    async def test_draft_roundtrip(self, mock_get, mock_set, mock_del):
        from src.expenses import clear_draft, save_draft

        draft = {"items": [{"nominal": 25000, "deskripsi": "kopi"}], "sumber": "chat"}
        self.assertTrue(await save_draft(123, draft))

        mock_get.return_value = json.dumps(draft)
        loaded = await get_draft(123)
        self.assertEqual(loaded["items"][0]["deskripsi"], "kopi")
        self.assertTrue(await clear_draft(123))

    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value="1")
    async def test_duplikat(self, mock_get):
        self.assertTrue(await is_duplicate(123, "kopi", 25000, "2026-10-02"))


class TestIntentDanRegistry(unittest.TestCase):

    def test_intent_pengeluaran_dan_alias_jurnal(self):
        from src.bot import detect_intent

        self.assertEqual(detect_intent("rekap pengeluaran bulan ini"), "pengeluaran")
        self.assertEqual(detect_intent("catat pengeluaran kopi 25rb"), "pengeluaran")
        # Alias deprecated tetap terdeteksi agar bisa diarahkan.
        self.assertEqual(detect_intent("catat jurnal hari ini"), "jurnal")

    def test_registry_tools_pengeluaran(self):
        from src.tools import TOOL_EXECUTORS, TOOLS_BY_INTENT, get_tools_for_intent

        self.assertIn("save_expense", TOOLS_BY_INTENT["pengeluaran"])
        self.assertIn("save_expense", TOOL_EXECUTORS)
        names = [t["name"] for t in get_tools_for_intent("pengeluaran")]
        self.assertIn("save_expense", names)
        self.assertIn("get_expense_recap", names)

    def test_help_detail_memuat_pengeluaran(self):
        from src.bot import COMMAND_HELP_DETAIL

        self.assertIn("pengeluaran", COMMAND_HELP_DETAIL)
        self.assertNotIn("jurnal", COMMAND_HELP_DETAIL)


if __name__ == "__main__":
    unittest.main()
