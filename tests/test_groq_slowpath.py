"""
Unit test suite untuk Groq Slow Path Fallback (Function Calling).
Jalankan dengan: python -m unittest tests/test_groq_slowpath.py
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gemini import chat_with_oline
from src.groq import chat_groq_with_tools
from src.tools import convert_tools_to_openai_format, execute_tool


class TestGroqSlowPath(unittest.IsolatedAsyncioTestCase):

    def test_convert_tools_to_openai_format(self):
        """Tes konversi deklarasi tool Gemini ke format OpenAI/Groq."""
        gemini_tools = [
            {
                "name": "get_weather_forecast",
                "description": "Cek cuaca",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ]

        converted = convert_tools_to_openai_format(gemini_tools)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]["type"], "function")
        self.assertEqual(converted[0]["function"]["name"], "get_weather_forecast")
        self.assertEqual(converted[0]["function"]["description"], "Cek cuaca")
        self.assertIn("city", converted[0]["function"]["parameters"]["properties"])

    @patch("src.tools.TOOL_EXECUTORS")
    async def test_execute_tool(self, mock_executors):
        """Tes execute_tool helper function."""
        mock_weather_fn = AsyncMock(return_value={"temp": 28, "condition": "cerah"})
        mock_executors.get.return_value = mock_weather_fn

        result = await execute_tool("get_weather_forecast", {"city": "Bandung"})
        self.assertEqual(result, {"temp": 28, "condition": "cerah"})
        mock_weather_fn.assert_called_once_with(city="Bandung")

    @patch("groq.AsyncGroq")
    async def test_chat_groq_with_tools_no_tool_call(self, mock_async_groq):
        """Tes chat_groq_with_tools jika Groq langsung menjawab tanpa memanggil tool."""
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Cuaca di Bandung cerah hari ini."
        mock_choice.message.tool_calls = None
        mock_completion.choices = [mock_choice]
        mock_completion.usage = {"total_tokens": 150}

        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_async_groq.return_value = mock_client

        tools = [
            {
                "name": "get_weather_forecast",
                "description": "Cek cuaca",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            response = await chat_groq_with_tools(
                system_prompt="Kamu Oline",
                history=[],
                user_message="Cuaca Bandung",
                tools=tools,
            )
            self.assertEqual(response, "Cuaca di Bandung cerah hari ini.")
            mock_client.chat.completions.create.assert_called_once()

    @patch("src.tools.execute_tool", new_callable=AsyncMock)
    @patch("groq.AsyncGroq")
    async def test_chat_groq_with_tools_with_tool_call(self, mock_async_groq, mock_execute_tool):
        """Tes chat_groq_with_tools dengan alur 2 tahap function calling."""
        mock_client = MagicMock()

        # Respon 1: Groq minta panggil get_weather_forecast
        mock_tc = MagicMock()
        mock_tc.id = "call_abc123"
        mock_tc.function.name = "get_weather_forecast"
        mock_tc.function.arguments = '{"city": "Bandung"}'

        mock_msg1 = MagicMock()
        mock_msg1.content = None
        mock_msg1.tool_calls = [mock_tc]

        mock_comp1 = MagicMock()
        mock_comp1.choices = [MagicMock(message=mock_msg1)]
        mock_comp1.usage = {"total_tokens": 80}

        # Respon 2: Groq menyusun respons akhir berdasar hasil tool
        mock_msg2 = MagicMock()
        mock_msg2.content = "Cuaca di Bandung hari ini cerah dengan suhu 28°C."
        mock_msg2.tool_calls = None

        mock_comp2 = MagicMock()
        mock_comp2.choices = [MagicMock(message=mock_msg2)]
        mock_comp2.usage = {"total_tokens": 120}

        mock_client.chat.completions.create = AsyncMock(side_effect=[mock_comp1, mock_comp2])
        mock_async_groq.return_value = mock_client

        mock_execute_tool.return_value = {"city": "Bandung", "temp": 28, "condition": "cerah"}

        tools = [
            {
                "name": "get_weather_forecast",
                "description": "Cek cuaca",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ]

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            response = await chat_groq_with_tools(
                system_prompt="Kamu Oline",
                history=[],
                user_message="Cuaca Bandung gimana?",
                tools=tools,
                chat_id=12345,
            )

            self.assertEqual(response, "Cuaca di Bandung hari ini cerah dengan suhu 28°C.")
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)
            mock_execute_tool.assert_called_once_with("get_weather_forecast", {"city": "Bandung"}, chat_id=12345)

    @patch("src.handlers.call_model_with_fallback", new_callable=AsyncMock, side_effect=Exception("chain down"))
    @patch("src.groq.chat_groq_with_tools", new_callable=AsyncMock)
    @patch("src.gemini._generate_content_with_fallback", new_callable=AsyncMock)
    @patch("src.gemini.get_memory", new_callable=AsyncMock)
    @patch("src.gemini.get_history", new_callable=AsyncMock)
    @patch("src.gemini.save_history", new_callable=AsyncMock)
    async def test_chat_with_oline_gemini_fails_triggers_groq_slow_path(
        self,
        mock_save_hist,
        mock_get_hist,
        mock_get_mem,
        mock_gen_gemini,
        mock_chat_groq_tools,
        mock_chain,
    ):
        """Tes Slow Path: Saat Gemini melempar exception, fallback ke Groq Slow Path."""
        mock_get_mem.return_value = ""
        mock_get_hist.return_value = []
        mock_gen_gemini.side_effect = RuntimeError("Gemini Quota Exceeded 429")

        mock_chat_groq_tools.return_value = "Cuaca di Tuban sekarang berawan, 26°C."

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            response = await chat_with_oline(
                chat_id=999,
                user_message="Cuaca di Tuban gimana?",
                intent="cuaca",  # Slow Path
            )

            mock_gen_gemini.assert_called_once()
            mock_chat_groq_tools.assert_called_once()
            self.assertIn("Cuaca di Tuban sekarang berawan, 26°C.", response)
            self.assertIn("⚠️ Oline pakai otak cadangan nih, Gemini lagi istirahat~", response)

    @patch("src.handlers.call_model_with_fallback", new_callable=AsyncMock, side_effect=Exception("chain down"))
    @patch("src.groq.chat_groq_with_tools", new_callable=AsyncMock)
    @patch("src.gemini._generate_content_with_fallback", new_callable=AsyncMock)
    @patch("src.gemini.get_memory", new_callable=AsyncMock)
    @patch("src.gemini.get_history", new_callable=AsyncMock)
    async def test_chat_with_oline_both_gemini_and_groq_fail(
        self,
        mock_get_hist,
        mock_get_mem,
        mock_gen_gemini,
        mock_chat_groq_tools,
        mock_chain,
    ):
        """Tes Slow Path: Saat Gemini DAN Groq dua-duanya gagal."""
        mock_get_mem.return_value = ""
        mock_get_hist.return_value = []
        mock_gen_gemini.side_effect = RuntimeError("Gemini 500 Server Error")
        mock_chat_groq_tools.side_effect = RuntimeError("Groq 503 Service Unavailable")

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test123"}):
            response = await chat_with_oline(
                chat_id=999,
                user_message="Cuaca di Tuban gimana?",
                intent="cuaca",
            )

            self.assertIn("aduh, Oline lagi error dua-duanya nih", response)
            self.assertIn("perintah kamu udah Oline simpan", response)


if __name__ == "__main__":
    unittest.main()
