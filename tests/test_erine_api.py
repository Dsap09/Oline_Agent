"""
Unit test suite untuk integrasi ERINE AI API (panggil_erine) di Oline.
Jalankan dengan: python -m unittest tests/test_erine_api.py
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Set path agar src bisa diimport
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import detect_intent
from src.tools import (
    TOOL_DECLARATIONS,
    TOOLS_BY_INTENT,
    get_tools_for_intent,
    panggil_erine,
)


class TestErineAPIIntegration(unittest.TestCase):

    def test_detect_akademik_intent(self):
        """Memastikan kata kunci akademik terdeteksi sebagai intent 'akademik'."""
        queries = [
            "carikan jurnal tentang AI agent",
            "buatkan sitasi APA 7 untuk paper ini",
            "rangkum docx laporan ini",
            "tanya pdf tentang metodologi penelitian",
            "literatur tentang machine learning",
            "arxiv paper deep learning",
        ]
        for query in queries:
            intent = detect_intent(query)
            self.assertEqual(intent, "akademik", f"Query '{query}' gagal terdeteksi sebagai intent 'akademik'")

    def test_panggil_erine_tool_declaration(self):
        """Memastikan panggil_erine terdaftar di TOOL_DECLARATIONS dan TOOLS_BY_INTENT."""
        decl = [t for t in TOOL_DECLARATIONS if t["name"] == "panggil_erine"]
        self.assertEqual(len(decl), 1)
        self.assertIn("jenis", decl[0]["parameters"]["properties"])

        self.assertIn("akademik", TOOLS_BY_INTENT)
        self.assertIn("panggil_erine", TOOLS_BY_INTENT["akademik"])

        tools_for_intent = get_tools_for_intent("akademik")
        self.assertEqual(len(tools_for_intent), 1)
        self.assertEqual(tools_for_intent[0]["name"], "panggil_erine")

    @patch("httpx.AsyncClient.post")
    def test_panggil_erine_cari_jurnal_success(self, mock_post):
        """Memastikan panggil_erine endpoint cari_jurnal berhasil mengirim request & menerima data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "data": [
                {"judul": "AI Agents 2026", "penulis": "John Doe", "tahun": "2026", "link": "https://example.com/pdf"}
            ]
        }
        mock_post.return_value = mock_response

        async def _run():
            res = await panggil_erine(jenis="cari_jurnal", data="AI agent")
            self.assertIn("AI Agents 2026", res)
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args.kwargs
            self.assertIn("headers", call_kwargs)
            self.assertIn("x-api-key", call_kwargs["headers"])

        asyncio.run(_run())

    @patch("httpx.AsyncClient.post")
    def test_panggil_erine_sitasi_success(self, mock_post):
        """Memastikan panggil_erine endpoint sitasi berhasil."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "data": {
                "apa": "Doe, J. (2026). AI Agents 2026.",
                "bibtex": "@article{doe2026, title={AI Agents 2026}}"
            }
        }
        mock_post.return_value = mock_response

        async def _run():
            res = await panggil_erine(jenis="sitasi", data="AI Agents 2026", penulis="John Doe", tahun="2026")
            self.assertIn("Doe, J.", res)
            self.assertIn("bibtex", res)

        asyncio.run(_run())

    @patch("httpx.AsyncClient.post")
    def test_panggil_erine_unauthorized(self, mock_post):
        """Memastikan penanganan 401 Unauthorized."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        async def _run():
            res = await panggil_erine(jenis="cari_jurnal", data="AI agent")
            self.assertIn("API Key tidak valid", res)

        asyncio.run(_run())

    @patch("httpx.AsyncClient.post")
    def test_panggil_erine_rate_limit(self, mock_post):
        """Memastikan penanganan 429 Rate Limit."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response

        async def _run():
            res = await panggil_erine(jenis="cari_jurnal", data="AI agent")
            self.assertIn("batas pemanggilan terlampaui", res)

        asyncio.run(_run())

    @patch("httpx.AsyncClient.post")
    def test_panggil_erine_timeout(self, mock_post):
        """Memastikan penanganan timeout 20 detik."""
        import httpx
        mock_post.side_effect = httpx.TimeoutException("Timeout after 20s")

        async def _run():
            res = await panggil_erine(jenis="cari_jurnal", data="AI agent")
            self.assertIn("lambat merespons", res)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
