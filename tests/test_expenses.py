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
    RECEIPT_VISION_PROMPT,
    SUMBER_OPTIONS,
    apply_source_to_draft,
    build_confirm_text,
    build_recap_text,
    build_saved_text,
    build_source_prompt_text,
    categorize_local,
    categorize_receipt,
    compute_total_for_date,
    detect_expense_entry,
    format_rupiah,
    format_short_date,
    get_draft,
    is_duplicate,
    parse_amount,
    parse_periode,
    parse_receipt_result,
    parse_sumber,
    short_page_id,
    today_iso,
    total_label_for_date,
    validate_receipt_draft,
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


class TestPromptStruk(unittest.TestCase):

    def test_prompt_meminta_item_dan_total(self):
        self.assertIn("items", RECEIPT_VISION_PROMPT)
        self.assertIn("total_item", RECEIPT_VISION_PROMPT)
        self.assertIn("total_belanja", RECEIPT_VISION_PROMPT)
        self.assertIn("diskon", RECEIPT_VISION_PROMPT)


class TestParseReceiptStrukV2(unittest.TestCase):

    def test_json_lengkap_prioritas_total_belanja(self):
        raw = json.dumps({
            "toko": "Alfamidi Manyar Kartika",
            "tanggal": "2026-10-05",
            "jam": "00:54",
            "items": [
                {"nama": "AQUVIVA AIR 1.5L", "qty": 1, "harga": 6600, "subtotal": 6600},
                {"nama": "WLS F VNL65ML", "qty": 1, "harga": 7000, "subtotal": 7000},
            ],
            "diskon": 500,
            "total_item": 13600,
            "total_belanja": 13100,
            "metode_bayar": "qris",
        })
        result = parse_receipt_result(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["nominal"], 13100)
        self.assertEqual(result["subtotal"], 13600)
        self.assertEqual(result["diskon"], 500)
        self.assertEqual(result["jam"], "00:54")
        self.assertEqual(result["deskripsi"], "Alfamidi Manyar Kartika")
        self.assertEqual(len(result["detail_items"]), 2)
        self.assertIn("AQUVIVA", result["catatan"])
        self.assertIn("Diskon: -Rp500", result["catatan"])
        self.assertEqual(result["peringatan"], [])

    def test_hitung_total_belanja_dari_total_item_diskon(self):
        raw = json.dumps({
            "toko": "Toko A",
            "items": [{"nama": "aqua", "qty": 2, "harga": 3000}],
            "diskon": 1000,
            "total_item": 6000,
        })
        result = parse_receipt_result(raw)
        self.assertEqual(result["nominal"], 5000)
        self.assertEqual(result["subtotal"], 6000)

    def test_toko_numerik_ditolak_dan_fallback_item(self):
        raw = json.dumps({"toko": "13100", "total": 0, "items": ["aqua 6600"]})
        result = parse_receipt_result(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["toko"], "")
        self.assertEqual(result["nominal"], 6600)
        self.assertEqual(result["deskripsi"], "aqua")

    def test_tanggal_masa_depan_dikoreksi(self):
        raw = json.dumps({
            "toko": "Toko A",
            "tanggal": "2099-01-01",
            "total_belanja": 10000,
            "items": [{"nama": "susu", "harga": 10000}],
        })
        result = parse_receipt_result(raw)
        self.assertEqual(result["tanggal"], today_iso())
        self.assertTrue(any("masa depan" in w for w in result["peringatan"]))

    def test_format_tanggal_struk(self):
        for raw_date in ("05/10/2026", "05-10-2026", "05 Oktober 2026"):
            with self.subTest(raw_date=raw_date):
                raw = json.dumps({
                    "toko": "Toko A",
                    "tanggal": raw_date,
                    "total_belanja": 1000,
                    "items": [{"nama": "x", "harga": 1000}],
                })
                result = parse_receipt_result(raw)
                self.assertEqual(result["tanggal"], "2026-10-05")

    def test_total_belanja_tanpa_item_beri_peringatan(self):
        raw = json.dumps({"toko": "Toko A", "tanggal": "2026-10-05", "total_belanja": 9000})
        result = parse_receipt_result(raw)
        self.assertIsNotNone(result)
        self.assertTrue(any("item" in w.lower() for w in result["peringatan"]))


class TestKategoriStruk(unittest.TestCase):

    def test_item_minuman_dominan(self):
        items = [{"nama": "AQUVIVA AIR 1.5L"}, {"nama": "SUSU ULTRA 1L"}]
        self.assertEqual(categorize_receipt("Alfamidi Manyar", items), "Makan & Minum")

    def test_item_belanja(self):
        self.assertEqual(categorize_receipt("", [{"nama": "SABUN LIFEBUOY"}]), "Belanja")

    def test_fallback_toko(self):
        self.assertEqual(categorize_receipt("Indomaret", []), "Belanja")

    def test_ambigu_none(self):
        self.assertIsNone(categorize_receipt("", [{"nama": "XYZ ABC"}]))


class TestValidasiStruk(unittest.TestCase):

    def test_nominal_nol_tanggal_masa_depan(self):
        warnings = validate_receipt_draft({
            "nominal": 0,
            "tanggal": "2099-01-01",
            "detail_items": [],
        })
        self.assertTrue(any("Nominal" in w for w in warnings))
        self.assertTrue(any("masa depan" in w for w in warnings))
        self.assertTrue(any("Item" in w for w in warnings))

    def test_valid_tanpa_peringatan(self):
        warnings = validate_receipt_draft({
            "nominal": 13100,
            "tanggal": "2026-10-05",
            "detail_items": [{"nama": "aqua"}],
            "peringatan": [],
        })
        self.assertEqual(warnings, [])


class TestFormatStruk(unittest.TestCase):

    def test_confirm_struk_format_baru(self):
        draft = {"sumber": "struk", "items": [{
            "nominal": 13100,
            "deskripsi": "Alfamidi Manyar Kartika",
            "toko": "Alfamidi Manyar Kartika",
            "tanggal": "2026-10-05",
            "waktu": "00:54",
            "kategori": "Makan & Minum",
            "diskon": 500,
            "detail_items": [
                {"nama": "AQUVIVA AIR 1.5L", "qty": 1, "subtotal": 6600},
                {"nama": "WLS F VNL65ML", "qty": 1, "subtotal": 7000},
            ],
        }]}
        text = build_confirm_text(draft)
        self.assertIn("Struk terbaca", text)
        self.assertIn("🏪 Toko: Alfamidi Manyar Kartika", text)
        self.assertIn("00:54", text)
        self.assertIn("AQUVIVA", text)
        self.assertIn("Diskon: -Rp500", text)
        self.assertIn("Total: Rp13.100", text)

    def test_saved_struk_format_baru(self):
        saved = [{
            "sumber": "struk",
            "nominal": 13100,
            "kategori": "Makan & Minum",
            "toko": "Alfamidi Manyar Kartika",
            "tanggal": "2026-10-05",
            "waktu": "00:54",
            "detail_items": [{"nama": "aqua"}],
        }]
        text = build_saved_text(saved, 13100, total_label="Total hari ini")
        self.assertIn("✅ Tercatat!", text)
        self.assertIn("🏪 Alfamidi Manyar Kartika", text)
        self.assertIn("💰 Rp13.100", text)
        self.assertIn("Total hari ini: Rp13.100", text)

    def test_label_total_tanggal_struk(self):
        self.assertEqual(total_label_for_date(today_iso()), "Total hari ini")
        self.assertEqual(total_label_for_date("2026-10-04"), "Total 04 Okt 2026")


class TestRekapTotal(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.notion.query_expenses", new_callable=AsyncMock)
    @patch("src.expenses.get_daily_total", new_callable=AsyncMock, return_value=0)
    async def test_total_dari_notion(self, mock_kv_total, mock_query, mock_set_cache):
        mock_query.return_value = [{"nominal": 13100}, {"nominal": 500}]
        total = await compute_total_for_date(123, "2026-10-05")
        self.assertEqual(total, 13600)

    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.notion.query_expenses", new_callable=AsyncMock, return_value=[])
    @patch("src.expenses.get_daily_total", new_callable=AsyncMock, return_value=25000)
    async def test_fallback_kv_bila_notion_kosong(self, mock_kv_total, mock_query, mock_set_cache):
        total = await compute_total_for_date(123, "2026-10-05")
        self.assertEqual(total, 25000)


class TestKoreksiManualStruk(unittest.IsolatedAsyncioTestCase):

    @patch("src.bot._show_expense_confirmation", new_callable=AsyncMock)
    @patch("src.kv.del_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_isi_manual_total_toko_item(
        self, mock_get, mock_set, mock_del, mock_show
    ):
        from src.bot import _handle_expense_correction

        draft = {
            "items": [{
                "nominal": 0,
                "deskripsi": "",
                "kategori": None,
                "tanggal": "2026-10-05",
                "toko": "",
                "detail_items": [],
            }],
            "sumber": "struk",
            "manual": True,
        }
        mock_get.return_value = json.dumps(draft)
        destination = AsyncMock()

        handled = await _handle_expense_correction(
            destination, 123,
            "total 13100 toko Alfamidi item aqua 6600, susu 7000",
        )

        self.assertTrue(handled)
        saved = json.loads(mock_set.call_args[0][1])
        item = saved["items"][0]
        self.assertEqual(item["nominal"], 13100)
        self.assertEqual(item["toko"], "Alfamidi")
        self.assertEqual(item["deskripsi"], "Alfamidi")
        self.assertEqual(len(item["detail_items"]), 2)
        self.assertIn("aqua", item["catatan"])


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


class TestParseSumber(unittest.TestCase):

    def test_keyword_umum(self):
        cases = {
            "kopi 25rb cash": "Cash",
            "bayar tunai 10rb": "Cash",
            "uang fisik 50rb": "Cash",
            "makan 30rb bni": "BNI",
            "bank bni": "BNI",
            "grab 15k bank jago": "Bank Jago",
            "grab 15k jago": "Bank Jago",
            "jago": "Bank Jago",
            "dana": "Dana",
            "beli pulsa 10rb gopay": "Gopay",
            "go pay": "Gopay",
            "kopi 25rb ovo": "Ovo",
            "kopi 25rb shopee pay": "Shopee Pay",
            "kopi 25rb shopeepay": "Shopee Pay",
            "kopi 25rb dana": "Dana",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_sumber(text), expected)

    def test_guard_false_positive(self):
        for text in (
            "dana darurat 500rb",
            "dana pensiun",
            "dia jago masak",
            "belanja di shopee",
            "makasih jago",
            "tolong dana",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_sumber(text))

    def test_semua_opsi_kanonik(self):
        self.assertEqual(
            SUMBER_OPTIONS,
            ["Cash", "BNI", "Bank Jago", "Shopee Pay", "Ovo", "Gopay", "Dana"],
        )


class TestDeteksiSumberDana(unittest.TestCase):

    def test_chat_dengan_sumber(self):
        detection = detect_expense_entry("kopi 25rb cash")
        self.assertIsNotNone(detection)
        item = detection["items"][0]
        self.assertEqual(item["nominal"], 25000)
        self.assertEqual(item["deskripsi"], "kopi")
        self.assertEqual(item["sumber_dana"], "Cash")

    def test_bank_jago_strip_dari_deskripsi(self):
        item = detect_expense_entry("grab 15k bank jago")["items"][0]
        self.assertEqual(item["deskripsi"], "grab")
        self.assertEqual(item["sumber_dana"], "Bank Jago")

    def test_gopay_pulsa(self):
        item = detect_expense_entry("beli pulsa 10rb gopay")["items"][0]
        self.assertEqual(item["deskripsi"], "pulsa")
        self.assertEqual(item["sumber_dana"], "Gopay")

    def test_multi_item_beda_sumber(self):
        detection = detect_expense_entry("kopi 25rb cash, makan 30rb bni")
        items = detection["items"]
        self.assertEqual([i["deskripsi"] for i in items], ["kopi", "makan"])
        self.assertEqual([i["sumber_dana"] for i in items], ["Cash", "BNI"])

    def test_tanpa_sumber_none(self):
        item = detect_expense_entry("kopi 25rb")["items"][0]
        self.assertIsNone(item["sumber_dana"])

    def test_dana_akhir_bagian(self):
        item = detect_expense_entry("kopi 25rb dana")["items"][0]
        self.assertEqual(item["deskripsi"], "kopi")
        self.assertEqual(item["sumber_dana"], "Dana")


class TestFormatSumberDana(unittest.TestCase):

    def test_saved_text_tampilkan_sumber(self):
        saved = [{
            "nominal": 25000,
            "deskripsi": "kopi",
            "kategori": "Makan & Minum",
            "sumber_dana": "Cash",
        }]
        text = build_saved_text(saved, 25000)
        self.assertIn("Tercatat", text)
        self.assertIn("Cash", text)

    def test_saved_struk_tampilkan_sumber(self):
        saved = [{
            "sumber": "struk",
            "nominal": 13100,
            "kategori": "Belanja",
            "toko": "Alfamidi",
            "tanggal": today_iso(),
            "sumber_dana": "BNI",
        }]
        text = build_saved_text(saved, 13100)
        self.assertIn("🏦 BNI", text)

    def test_confirm_text_tampilkan_sumber(self):
        draft = {"sumber": "chat", "items": [{
            "nominal": 25000,
            "deskripsi": "kopi",
            "kategori": "Makan & Minum",
            "sumber_dana": "BNI",
        }]}
        self.assertIn("BNI", build_confirm_text(draft))

    def test_confirm_struk_belum_dipilih(self):
        draft = {"sumber": "struk", "items": [{
            "nominal": 13100,
            "deskripsi": "Alfamidi",
            "toko": "Alfamidi",
            "tanggal": today_iso(),
            "kategori": "Belanja",
        }]}
        self.assertIn("belum dipilih", build_confirm_text(draft))

    def test_source_prompt(self):
        draft = {"sumber": "chat", "items": [{"nominal": 25000, "deskripsi": "kopi"}]}
        text = build_source_prompt_text(draft)
        self.assertIn("Sumber pengeluaran", text)
        self.assertIn("cash", text)

    def test_apply_source_to_draft(self):
        draft = {"items": [{"sumber_dana": None}, {"sumber_dana": "BNI"}]}
        filled = apply_source_to_draft(draft, "Cash")
        self.assertEqual(filled, 1)
        self.assertEqual(draft["items"][0]["sumber_dana"], "Cash")
        self.assertEqual(draft["items"][1]["sumber_dana"], "BNI")
        filled_all = apply_source_to_draft(draft, "Gopay", force=True)
        self.assertEqual(filled_all, 2)
        self.assertEqual(draft["items"][1]["sumber_dana"], "Gopay")


class TestRekapPerSumber(unittest.TestCase):

    def test_breakdown_dan_bucket_unknown(self):
        entries = [
            {"nominal": 25000, "kategori": "Makan & Minum", "sumber_dana": "Cash"},
            {"nominal": 30000, "kategori": "Makan & Minum", "sumber_dana": "BNI"},
            {"nominal": 5000, "kategori": "Transport", "sumber_dana": ""},
        ]
        text = build_recap_text(entries, "Bulan Ini")
        self.assertIn("Per sumber", text)
        self.assertIn("Cash", text)
        self.assertIn("BNI", text)
        self.assertIn("Tidak diketahui", text)

    def test_filter_sumber(self):
        entries = [
            {"nominal": 25000, "kategori": "Makan & Minum", "sumber_dana": "Cash"},
            {"nominal": 30000, "kategori": "Makan & Minum", "sumber_dana": "BNI"},
        ]
        text = build_recap_text(entries, "Bulan Ini", sumber_dana="BNI")
        self.assertIn("Rp30.000", text)
        self.assertNotIn("Rp25.000", text)

    def test_breakdown_only(self):
        entries = [{"nominal": 10000, "kategori": "Transport", "sumber_dana": "Gopay"}]
        text = build_recap_text(entries, "Hari Ini", breakdown_only=True)
        self.assertIn("Per sumber", text)
        self.assertNotIn("Kategori terbesar", text)

    def test_tanpa_sumber_tetap_format_lama(self):
        entries = [
            {"nominal": 75000, "kategori": "Makan & Minum"},
            {"nominal": 25000, "kategori": "Transport"},
        ]
        text = build_recap_text(entries, "Bulan Ini")
        self.assertNotIn("Per sumber", text)
        self.assertIn("Kategori terbesar", text)


class TestSettingsSumber(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value=None)
    async def test_set_default_source(self, mock_get, mock_set):
        from src.expenses import set_expense_settings

        self.assertTrue(await set_expense_settings(123, default_source="BNI"))
        payload = json.loads(mock_set.call_args[0][1])
        self.assertEqual(payload["default_source"], "BNI")

    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value=None)
    async def test_reset_default_source(self, mock_get, mock_set):
        from src.expenses import set_expense_settings

        await set_expense_settings(123, default_source=None)
        payload = json.loads(mock_set.call_args[0][1])
        self.assertIsNone(payload["default_source"])

    @patch(
        "src.kv.get_cache",
        new_callable=AsyncMock,
        return_value=json.dumps({"default_source": "Gopay"}),
    )
    async def test_get_default_source(self, mock_get):
        from src.expenses import get_expense_settings

        settings = await get_expense_settings(123)
        self.assertEqual(settings["default_source"], "Gopay")


class TestKoreksiSumber(unittest.IsolatedAsyncioTestCase):

    @patch("src.bot._show_expense_confirmation", new_callable=AsyncMock)
    @patch("src.kv.del_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.set_cache", new_callable=AsyncMock, return_value=True)
    @patch("src.kv.get_cache", new_callable=AsyncMock)
    async def test_koreksi_sumber_bni(self, mock_get, mock_set, mock_del, mock_show):
        from src.bot import _handle_expense_correction

        draft = {
            "items": [{
                "nominal": 13100,
                "deskripsi": "Alfamidi",
                "kategori": "Belanja",
                "tanggal": "2026-10-05",
                "toko": "Alfamidi",
            }],
            "sumber": "struk",
        }
        mock_get.return_value = json.dumps(draft)
        destination = AsyncMock()

        handled = await _handle_expense_correction(destination, 123, "sumber bni")

        self.assertTrue(handled)
        saved = json.loads(mock_set.call_args[0][1])
        self.assertEqual(saved["items"][0]["sumber_dana"], "BNI")


if __name__ == "__main__":
    unittest.main()
