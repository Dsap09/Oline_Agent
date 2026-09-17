"""
Unit test untuk fungsi clean_tool_calls di src/utils.py.
Memastikan pembersihan pola tool call yang bocor (seperti [toggle_feature: enable], [tool: argument], dll).
"""

import unittest
from src.utils import clean_tool_call_text, clean_tool_calls


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


class TestCleanToolCallText(unittest.TestCase):

    def test_clean_tool_call_block(self):
        raw = "<tool_call>\n{'name': 'get_weather_forecast'}\n</tool_call>\nCuaca hari ini cerah."
        expected = "Cuaca hari ini cerah."
        self.assertEqual(clean_tool_call_text(raw), expected)

    def test_clean_function_call_block(self):
        raw = "<function_call>get_stock_price(BBCA)</function_call>\nBBCA sekarang 10.250."
        expected = "BBCA sekarang 10.250."
        self.assertEqual(clean_tool_call_text(raw), expected)

    def test_clean_invoke_block(self):
        raw = '<invoke name="check_ai_quota">\n{"x": 1}\n</invoke>\nKuota sisa 80%.'
        expected = "Kuota sisa 80%."
        self.assertEqual(clean_tool_call_text(raw), expected)

    def test_clean_unpaired_open_close_tags(self):
        raw = "<tool_calls>\n<tool>search_internet</tool>\nHasil pencarian tersedia."
        expected = "Hasil pencarian tersedia."
        self.assertEqual(clean_tool_call_text(raw), expected)

    def test_clean_tool_calls_wraps_tool_call_block(self):
        raw = "<tool_call>get_weather_forecast(city='Jakarta')</tool_call>\nCuaca Jakarta hujan ringan."
        self.assertEqual(clean_tool_calls(raw), "Cuaca Jakarta hujan ringan.")

    def test_empty_or_none_input(self):
        self.assertEqual(clean_tool_call_text(""), "")
        self.assertEqual(clean_tool_call_text(None), "")


if __name__ == "__main__":
    unittest.main()
