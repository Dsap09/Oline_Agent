"""
Unit test untuk logika klarifikasi Ask-Before-Act dan status task (brief.md).
Jalankan dengan: python -m unittest tests/test_clarify.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import (
    build_task_status_message,
    get_clarify_question,
    is_ambiguous,
    is_status_request,
)


class TestClarify(unittest.TestCase):
    """Tes deteksi ambiguitas Ask-Before-Act."""

    def test_ambiguous_bare_cek(self):
        self.assertTrue(is_ambiguous("cek notion"))
        self.assertTrue(is_ambiguous("cek AI"))
        self.assertTrue(is_ambiguous("lihat drive"))

    def test_clear_command_not_ambiguous(self):
        self.assertFalse(is_ambiguous("cek koneksi notion"))
        self.assertFalse(is_ambiguous("cek kuota ai"))
        self.assertFalse(is_ambiguous("cek cuaca bandung"))
        self.assertFalse(is_ambiguous("cek saham BBCA"))
        self.assertFalse(is_ambiguous("status task saya"))

    def test_non_ambiguous_normal_text(self):
        self.assertFalse(is_ambiguous("rekomendasi film horor dong"))
        self.assertFalse(is_ambiguous("buatkan landing page kopi"))

    def test_question_per_target(self):
        self.assertIn("Notion", get_clarify_question("cek notion"))
        self.assertIn("kuota pemakaian AI", get_clarify_question("cek AI"))
        self.assertIn("Drive", get_clarify_question("cek drive"))
        self.assertEqual(get_clarify_question("cek sesuatu"), "Maksud kamu apa ya? Bisa lebih spesifik?")


class TestStatusTask(unittest.TestCase):
    """Tes pesan status task (brief.md langkah 6)."""

    def test_status_keywords(self):
        self.assertTrue(is_status_request("status task saya"))
        self.assertTrue(is_status_request("progresnya gimana"))
        self.assertFalse(is_status_request("cari jurnal digital forensik"))

    def test_build_message_includes_perintah(self):
        msg = build_task_status_message({"perintah": "cari jurnal AI", "message": "cari jurnal AI"})
        self.assertIn("cari jurnal AI", msg)
        self.assertIn("diproses", msg)

    def test_build_message_no_task_fields(self):
        msg = build_task_status_message({})
        self.assertIn("task", msg)


if __name__ == "__main__":
    unittest.main()
