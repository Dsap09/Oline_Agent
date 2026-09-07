"""
Unit test untuk fungsi clean_tool_calls di src/utils.py.
Memastikan pembersihan pola tool call yang bocor (seperti [toggle_feature: enable], [tool: argument], dll).
"""

import unittest
from src.utils import clean_tool_calls


class TestCleanToolCalls(unittest.TestCase):

    def test_clean_toggle_feature_leak(self):
        raw = "[toggle_feature: enable]\nFitur saham telah diaktifkan."
        expected = "Fitur saham telah diaktifkan."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_clean_toggle_feature_with_args_leak(self):
        raw = "Baik, [toggle_feature: feature=saham, status=True] fitur saham sudah aktif."
        expected = "Baik, fitur saham sudah aktif."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_clean_generic_tool_leak(self):
        raw = "[tool: argument]\nPekerjaan telah selesai."
        expected = "Pekerjaan telah selesai."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_clean_search_internet_leak(self):
        raw = "[search_internet: query='berita terkini']\nBerikut berita terbaru hari ini."
        expected = "Berikut berita terbaru hari ini."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_clean_standalone_bracket_tool(self):
        raw = "[check_ai_quota]\nBerikut status kuota AI saat ini."
        expected = "Berikut status kuota AI saat ini."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_clean_function_parens_leak(self):
        raw = "[execute_code(language='python', code='print(1)')]\nHasil eksekusi telah selesai."
        expected = "Hasil eksekusi telah selesai."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_preserve_normal_brackets(self):
        raw = "Progres pembuatan landing page: [1/4] Menyusun HTML."
        expected = "Progres pembuatan landing page: [1/4] Menyusun HTML."
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_preserve_capital_brackets(self):
        raw = "Status deployment: [SUKSES]"
        expected = "Status deployment: [SUKSES]"
        self.assertEqual(clean_tool_calls(raw), expected)

    def test_empty_or_none_input(self):
        self.assertEqual(clean_tool_calls(""), "")
        self.assertEqual(clean_tool_calls(None), "")


if __name__ == "__main__":
    unittest.main()
