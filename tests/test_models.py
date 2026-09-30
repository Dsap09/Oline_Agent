"""
Unit test suite untuk fitur Switch Model AI Manual (command /models).
Jalankan dengan: python -m unittest tests/test_models.py
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set token sebelum import src.bot agar create_application bisa dibangun di tes
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"

from src.config import (
    MODEL_CATALOG,
    MODEL_PRESETS,
    PROVIDER_LIMITS,
    TOKEN_REGISTRY,
    get_model_entry,
    get_model_label,
    list_manual_models,
    parse_model_key,
)
from src.handlers import _is_quota_error, call_model_with_fallback
from src.kv import (
    get_model_audit_log,
    get_model_preference,
    get_model_preference_meta,
    log_model_change,
    set_model_preference,
)


class TestModelCatalog(unittest.TestCase):

    def test_catalog_has_expected_models(self):
        """Katalog memuat semua model final OpenCode Go & DeepInfra."""
        keys = list_manual_models()
        for key in [
            "ocg:deepseek-v4-flash",
            "ocg:deepseek-v4-pro",
            "ocg:glm-5.2",
            "ocg:kimi-k2.7-code",
            "ocg:mimo-v2.5",
            "di:deepseek-ai/DeepSeek-V3.1-Terminus",
            "di:Qwen/Qwen3-32B",
            "di:meta-llama/Llama-3.3-70B-Instruct-Turbo",
        ]:
            self.assertIn(key, keys)

    def test_mistral_cerebras_removed(self):
        """Mistral & Cerebras tidak lagi di config (limit/token/registry)."""
        self.assertNotIn("mistral", PROVIDER_LIMITS)
        self.assertNotIn("cerebras", PROVIDER_LIMITS)
        self.assertNotIn("MISTRAL_API_KEY", TOKEN_REGISTRY)
        self.assertNotIn("CEREBRAS_API_KEY", TOKEN_REGISTRY)
        self.assertIn("opencode_go", PROVIDER_LIMITS)
        self.assertIn("OPENCODE_GO_API_KEY", TOKEN_REGISTRY)

    def test_parse_model_key(self):
        self.assertEqual(parse_model_key("ocg:deepseek-v4-flash"), ("opencode_go", "deepseek-v4-flash"))
        self.assertEqual(parse_model_key("di:Qwen/Qwen3-32B"), ("deepinfra", "Qwen/Qwen3-32B"))
        self.assertEqual(parse_model_key("auto"), (None, None))
        self.assertEqual(parse_model_key("tidak-ada"), (None, None))

    def test_presets_point_to_known_models(self):
        for name, key in MODEL_PRESETS.items():
            if key != "auto":
                self.assertTrue(get_model_entry(key), f"preset {name} -> {key} tidak ada di katalog")

    def test_get_model_label(self):
        self.assertEqual(get_model_label("auto"), "AUTO")
        self.assertEqual(get_model_label("ocg:deepseek-v4-pro"), "DeepSeek V4 Pro")

    def test_callback_data_fits_telegram_limit(self):
        """callback_data Telegram maks 64 byte."""
        for key in list_manual_models():
            self.assertLess(len(f"model:{key}".encode("utf-8")), 64)

    def test_all_models_have_price_and_desc(self):
        for group in MODEL_CATALOG.values():
            for entry in group.get("models", []):
                self.assertTrue(entry.get("label"))
                self.assertTrue(entry.get("desc"))
                self.assertTrue(entry.get("price"))


class TestModelPreferenceKV(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_default_auto(self, mock_kv):
        """Preferensi default 'auto' bila belum diset."""
        mock_kv.return_value = {"result": None}
        self.assertEqual(await get_model_preference(123), "auto")

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_set_preference_without_ttl(self, mock_kv):
        """set_model_preference menyimpan tanpa TTL di key model_preference:<chat_id>."""
        mock_kv.return_value = {"result": "OK"}
        ok = await set_model_preference(123, "ocg:deepseek-v4-pro")
        self.assertTrue(ok)
        cmd = mock_kv.call_args[0][0]
        self.assertEqual(cmd[0], "SET")
        self.assertEqual(cmd[1], "model_preference:123")
        self.assertIn("ocg:deepseek-v4-pro", cmd[2])
        self.assertNotIn("EX", cmd)

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_get_preference_parses_json(self, mock_kv):
        payload = json.dumps({"model": "di:Qwen/Qwen3-32B", "updated": "2026-09-29 10:00:00"})
        mock_kv.return_value = {"result": payload}
        self.assertEqual(await get_model_preference(123), "di:Qwen/Qwen3-32B")
        meta = await get_model_preference_meta(123)
        self.assertEqual(meta["updated"], "2026-09-29 10:00:00")

    @patch("src.kv._kv_pipeline", new_callable=AsyncMock)
    async def test_log_model_change_uses_rpush_trim_expire(self, mock_pipeline):
        """Audit log: RPUSH + LTRIM 50 + EXPIRE 7 hari ke model_log:<chat_id>."""
        mock_pipeline.return_value = [{"result": 1}]
        ok = await log_model_change(999, "set_manual", "ocg:glm-5.2", "tombol")
        self.assertTrue(ok)
        commands = mock_pipeline.call_args[0][0]
        self.assertEqual(commands[0][0], "RPUSH")
        self.assertEqual(commands[0][1], "model_log:999")
        self.assertEqual(commands[1][0], "LTRIM")
        self.assertEqual(commands[2], ["EXPIRE", "model_log:999", "604800"])

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_get_audit_log(self, mock_kv):
        entry = json.dumps({"waktu": "2026-09-29 10:00:00", "action": "set_manual", "model": "ocg:glm-5.2"})
        mock_kv.return_value = {"result": [entry]}
        logs = await get_model_audit_log(999)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "set_manual")


class TestQuotaErrorDetection(unittest.TestCase):

    def test_detects_quota_markers(self):
        self.assertTrue(_is_quota_error(Exception("429 Too Many Requests")))
        self.assertTrue(_is_quota_error(Exception("insufficient_quota")))
        self.assertTrue(_is_quota_error(Exception("Rate limit reached for model")))
        self.assertTrue(_is_quota_error(Exception("You exceeded your current quota")))
        self.assertTrue(_is_quota_error(Exception("budget exceeded for this period")))

    def test_detects_status_code_429(self):
        err = Exception("boom")
        err.status_code = 429
        self.assertTrue(_is_quota_error(err))

    def test_non_quota_error(self):
        self.assertFalse(_is_quota_error(Exception("connection reset by peer")))


class TestManualModelRouting(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["GROQ_API_KEY"] = "mock-groq-key"
        os.environ["OPENCODE_GO_API_KEY"] = "mock-ocg-key"
        os.environ["DEEPINFRA_API_KEY"] = "mock-di-key"

    def tearDown(self):
        for key in ("GROQ_API_KEY", "OPENCODE_GO_API_KEY", "DEEPINFRA_API_KEY"):
            os.environ.pop(key, None)

    @patch("src.grounding.prepare_grounding", new_callable=AsyncMock)
    @patch("src.opencode_go.chat_opencode_go", new_callable=AsyncMock)
    async def test_routes_to_opencode_go_with_model(self, mock_ocg, mock_ground):
        """Preferensi ocg:* memaksa chat_opencode_go dengan model_id yang tepat."""
        mock_ground.return_value = ("skip", None, None, None)
        mock_ocg.return_value = "halo dari model manual"

        res = await call_model_with_fallback(
            jalur="fast",
            system_prompt="sistem",
            history=[],
            user_message="halo",
            tools=None,
            chat_id=1,
            model_preference="ocg:deepseek-v4-pro",
        )

        self.assertEqual(res, "halo dari model manual")
        self.assertEqual(mock_ocg.await_args.kwargs["model"], "deepseek-v4-pro")
        self.assertEqual(mock_ocg.await_args.kwargs["chat_id"], 1)

    @patch("src.grounding.prepare_grounding", new_callable=AsyncMock)
    @patch("src.deepinfra.chat_deepinfra", new_callable=AsyncMock)
    async def test_routes_to_deepinfra_with_model(self, mock_di, mock_ground):
        """Preferensi di:* memaksa chat_deepinfra dengan model_name yang tepat."""
        mock_ground.return_value = ("skip", None, None, None)
        mock_di.return_value = "halo dari deepinfra manual"

        res = await call_model_with_fallback(
            jalur="tools",
            system_prompt="sistem",
            history=[],
            user_message="cek",
            tools=[],
            chat_id=2,
            model_preference="di:Qwen/Qwen3-32B",
        )

        self.assertEqual(res, "halo dari deepinfra manual")
        self.assertEqual(mock_di.await_args.kwargs["model_name"], "Qwen/Qwen3-32B")

    @patch("src.handlers.send_telegram_message", new_callable=AsyncMock)
    @patch("src.kv.log_model_change", new_callable=AsyncMock)
    @patch("src.kv.set_model_preference", new_callable=AsyncMock)
    @patch("src.grounding.prepare_grounding", new_callable=AsyncMock)
    @patch("src.opencode_go.chat_opencode_go", new_callable=AsyncMock)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_manual_limit_resets_to_auto_and_notifies(
        self, mock_groq, mock_ocg, mock_ground, mock_set, mock_log, mock_send
    ):
        """Model manual kena limit -> preferensi auto, notifikasi dikirim, task lanjut rotasi."""
        mock_ground.return_value = ("skip", None, None, None)
        mock_ocg.side_effect = Exception("429 rate limit exceeded")
        mock_groq.return_value = "jawaban dari rotasi auto"

        res = await call_model_with_fallback(
            jalur="fast",
            system_prompt="sistem",
            history=[],
            user_message="halo",
            tools=None,
            chat_id=7,
            model_preference="ocg:deepseek-v4-flash",
        )

        self.assertEqual(res, "jawaban dari rotasi auto")
        mock_set.assert_awaited_with(7, "auto")
        mock_log.assert_awaited()
        self.assertEqual(mock_log.await_args.args[1], "fallback_auto")
        mock_send.assert_awaited()
        notice = mock_send.await_args.args[1]
        self.assertIn("AUTO", notice)
        self.assertIn("limit", notice)


class TestModelsViewAndCommands(unittest.TestCase):

    def test_help_has_models(self):
        from src.bot import COMMAND_HELP_DETAIL, COMMAND_HELP_TEXT
        self.assertIn("models", COMMAND_HELP_DETAIL)
        self.assertIn("/models", COMMAND_HELP_TEXT)

    def test_models_command_registered(self):
        from telegram.ext import CommandHandler
        from src.bot import create_application

        app = create_application()
        registered = set()
        for group in app.handlers.values():
            for handler in group:
                if isinstance(handler, CommandHandler):
                    registered.update(handler.commands)
        self.assertIn("models", registered)


class TestBuildModelsView(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_view_lists_models_and_auto(self, mock_kv):
        mock_kv.return_value = {"result": None}
        from src.bot import _build_models_view

        text, keyboard = await _build_models_view(123, info=True)

        self.assertIn("DeepSeek V4 Flash", text)
        self.assertIn("DeepSeek-V3.1-Terminus", text)
        self.assertIn("$0.27 / $0.95", text)

        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        self.assertTrue(any("Auto (Recommended)" in label for label in labels))
        self.assertIn("model:auto", callbacks)
        self.assertIn("model:ocg:deepseek-v4-flash", callbacks)
        self.assertIn("model:di:Qwen/Qwen3-32B", callbacks)


if __name__ == "__main__":
    unittest.main()
