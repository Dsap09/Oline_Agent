"""
Unit test suite untuk DeepInfra intent routing & GitHub preview tool.
Jalankan dengan: python -m unittest tests/test_deepinfra_routing.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import detect_intent
from src.gemini import chat_with_oline
from src.tools import preview_with_codepen


class TestDeepInfraRouting(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["DEEPINFRA_API_KEY"] = "mock_deepinfra_key_123"
        os.environ["DEEPINFRA_MODEL"] = "deepseek-ai/DeepSeek-V4-Flash-0731"
        os.environ["GROQ_API_KEY"] = "mock_groq_key_123"
        os.environ["GEMINI_API_KEY"] = "mock_gemini_key_123"

    def test_detect_intent_preview_deploy_design(self):
        """Tes pendeteksian intent preview, deploy, dan design_reference."""
        self.assertEqual(detect_intent("buatkan website landing page"), "preview")
        self.assertEqual(detect_intent("deploy ke vercel sekarang"), "deploy")
        self.assertEqual(detect_intent("cari referensi desain website cafe"), "design_reference")

    @patch("src.deepinfra.chat_deepinfra", new_callable=AsyncMock)
    @patch("src.kv.get_memory", new_callable=AsyncMock)
    @patch("src.kv.get_history", new_callable=AsyncMock)
    @patch("src.kv.save_history", new_callable=AsyncMock)
    async def test_chat_with_oline_deepinfra_routing(
        self, mock_save_history, mock_get_history, mock_get_memory, mock_chat_deepinfra
    ):
        """Tes intent preview, deploy, dan design_reference memanggil chat_deepinfra."""
        mock_get_memory.return_value = ""
        mock_get_history.return_value = []
        mock_chat_deepinfra.return_value = "Ini hasil balasan dari DeepSeek V4 Flash!"

        # 1. Test intent preview
        res_preview = await chat_with_oline(chat_id=123, user_message="bikin website landing page", intent="preview")
        self.assertEqual(res_preview, "Ini hasil balasan dari DeepSeek V4 Flash!")
        mock_chat_deepinfra.assert_called()

        mock_chat_deepinfra.reset_mock()

        # 2. Test intent deploy
        res_deploy = await chat_with_oline(chat_id=123, user_message="deploy ke vercel", intent="deploy")
        self.assertEqual(res_deploy, "Ini hasil balasan dari DeepSeek V4 Flash!")
        mock_chat_deepinfra.assert_called()

        mock_chat_deepinfra.reset_mock()

        # 3. Test intent design_reference
        res_design = await chat_with_oline(chat_id=123, user_message="cari referensi desain", intent="design_reference")
        self.assertEqual(res_design, "Ini hasil balasan dari DeepSeek V4 Flash!")
        mock_chat_deepinfra.assert_called()

    @patch("src.groq.chat_groq_with_tools", new_callable=AsyncMock)
    @patch("src.gemini._generate_content_with_fallback", new_callable=AsyncMock)
    @patch("src.deepinfra.chat_deepinfra", new_callable=AsyncMock)
    @patch("src.kv.get_memory", new_callable=AsyncMock)
    @patch("src.kv.get_history", new_callable=AsyncMock)
    @patch("src.kv.save_pending_task", new_callable=AsyncMock)
    async def test_deepinfra_fallback_to_gemini_not_groq(
        self, mock_save_pending, mock_get_history, mock_get_memory, mock_chat_deepinfra, mock_gemini_gen, mock_groq_slow
    ):
        """Tes jika DeepInfra gagal, fallback ke Gemini dan TIDAK memanggil Groq untuk intent preview/deploy/design_reference."""
        mock_get_memory.return_value = ""
        mock_get_history.return_value = []
        
        # DeepInfra raises error
        mock_chat_deepinfra.side_effect = Exception("DeepInfra 429 Rate Limit")
        
        # Gemini raises error
        mock_gemini_gen.side_effect = Exception("Gemini Quota Exceeded")

        res = await chat_with_oline(chat_id=123, user_message="bikin website landing page", intent="preview")
        
        # Groq slow path should NEVER be called
        mock_groq_slow.assert_not_called()
        self.assertIn("perintah kamu udah Oline simpan", res)

    @patch("src.tools.create_github_preview", new_callable=AsyncMock)
    async def test_preview_with_codepen_github_success(self, mock_gh):
        """Tes preview_with_codepen menghasilkan preview URL GitHub (htmlpreview)."""
        mock_gh.return_value = {
            "status": "success",
            "result_code": "SUKSES",
            "url": "https://htmlpreview.github.io/?https://github.com/Dsap09/Oline_Agent/blob/preview/gym-123/index.html",
            "branch": "preview/gym-123",
            "message": "SUKSES: Preview siap!",
        }

        res = await preview_with_codepen(
            title="Landing Gym",
            html="<div class='hero'><h1>Gym Premium</h1></div>",
            css="h1 { color: red; }",
            js="console.log('hi');",
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["result_code"], "SUKSES")
        self.assertIn("htmlpreview.github.io", res["url"])
        mock_gh.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
