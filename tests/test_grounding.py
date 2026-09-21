"""
Unit test untuk mesin grounding Oline (anti-halu).
Jalankan dengan: python -m unittest tests/test_grounding.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.grounding import (
    GROUNDED, NEED_INFO, SKIP, UNAVAILABLE,
    build_grounding_augment, prepare_grounding,
)


class TestGrounding(unittest.TestCase):

    def setUp(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "mock"

    def test_build_augment_berisi_data(self):
        aug = build_grounding_augment("get_stock_price", {"price": 1000})
        self.assertIn("DATA NYATA", aug)
        self.assertIn("get_stock_price", aug)
        self.assertIn("1000", aug)

    def test_intent_tanpa_grounding_skip(self):
        status, _, _, _ = self._run(None, "halo")
        self.assertEqual(status, SKIP)

    def test_cuaca_grounded(self):
        with patch("src.tools.execute_tool", new=AsyncMock(return_value={"temp": 32, "desc": "cerah"})) as mock:
            status, tool, result, msg = self._run("cuaca", "cuaca di Jakarta")
            self.assertEqual(status, GROUNDED)
            self.assertEqual(tool, "get_weather_forecast")
            self.assertEqual(result["temp"], 32)
            mock.assert_called_once()

    def test_saham_tanpa_ticker_butuh_info(self):
        status, _, _, msg = self._run("saham", "berapa harga sahamnya")
        self.assertEqual(status, NEED_INFO)
        self.assertIn("ticker", msg)

    def test_saham_definisi_skip(self):
        status, _, _, _ = self._run("saham", "apa itu saham?")
        self.assertEqual(status, SKIP)

    def test_cuaca_error_unavailable(self):
        with patch("src.tools.execute_tool", new=AsyncMock(return_value={"error": "API key tidak dikonfigurasi"})):
            status, _, _, msg = self._run("cuaca", "cuaca di Jakarta")
            self.assertEqual(status, UNAVAILABLE)
            self.assertIn("API key", msg)

    def test_search_grounded(self):
        with patch("src.tools.execute_tool", new=AsyncMock(return_value={"hasil": "berita"})):
            status, tool, _, _ = self._run("search", "berita terbaru teknologi")
            self.assertEqual(status, GROUNDED)
            self.assertEqual(tool, "search_internet")

    def test_fast_path_faktual_grounded(self):
        with patch("src.tools.execute_tool", new=AsyncMock(return_value={"hasil": "info"})) as mock:
            status, tool, _, _ = self._run(None, "siapa pendiri JKT48?")
            self.assertEqual(status, GROUNDED)
            self.assertEqual(tool, "search_internet")
            mock.assert_called_once()

    def test_fast_path_smalltalk_skip(self):
        with patch("src.tools.execute_tool", new=AsyncMock()) as mock:
            status, _, _, _ = self._run(None, "halo apa kabar")
            self.assertEqual(status, SKIP)
            mock.assert_not_called()

    def test_fast_path_bantuan_skip(self):
        with patch("src.tools.execute_tool", new=AsyncMock()) as mock:
            status, _, _, _ = self._run(None, "bisa bantu aku ya")
            self.assertEqual(status, SKIP)
            mock.assert_not_called()

    def _run(self, intent, message):
        return _run_sync(prepare_grounding, 123, intent, message)


def _run_sync(coro_func, *args):
    import asyncio
    return asyncio.run(coro_func(*args))


if __name__ == "__main__":
    unittest.main()
