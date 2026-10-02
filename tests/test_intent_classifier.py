"""
Unit test suite untuk Intent Classifier context-aware (hybrid keyword + LLM gate).
Jalankan dengan: python -m unittest tests/test_intent_classifier.py
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set token sebelum import src.bot
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")

from src.intent_classifier import (
    CLARIFY_CONFIDENCE,
    EXECUTE_CONFIDENCE,
    _is_clear_directive,
    _is_meta_question,
    _parse_decision,
    classify_message,
    resolve_intent,
)
from src.bot import detect_intent_async, resolve_intent_decision
from src.handlers import call_model_with_fallback
from src.gemini import chat_with_oline
from src.kv import (
    clear_intent_clarify,
    get_intent_clarify,
    get_intent_log,
    log_intent_decision,
    log_intent_feedback,
    save_intent_clarify,
)


class TestParseDecision(unittest.TestCase):

    def test_parse_valid_with_fence(self):
        raw = '```json\n{"mode":"action","intent":"cuaca","confidence":0.92,"alasan":"minta cek"}\n```'
        data = _parse_decision(raw)
        self.assertEqual(data["mode"], "action")
        self.assertEqual(data["intent"], "cuaca")
        self.assertEqual(data["confidence"], 0.92)

    def test_invalid_mode_returns_none(self):
        self.assertIsNone(_parse_decision('{"mode":"ngawur","intent":"cuaca","confidence":0.9}'))

    def test_invalid_intent_becomes_none(self):
        data = _parse_decision('{"mode":"action","intent":"tidak_ada","confidence":0.8}')
        self.assertIsNone(data["intent"])

    def test_confidence_clamped(self):
        data = _parse_decision('{"mode":"chat","intent":"none","confidence":5}')
        self.assertEqual(data["confidence"], 1.0)
        data = _parse_decision('{"mode":"chat","intent":"none","confidence":-1}')
        self.assertEqual(data["confidence"], 0.0)


class TestClearDirective(unittest.TestCase):

    def test_direct_commands(self):
        self.assertTrue(_is_clear_directive("cek saham BBCA", "saham"))
        self.assertTrue(_is_clear_directive("berapa harga saham BBCA", "saham"))
        self.assertTrue(_is_clear_directive("buatkan landing page modern untuk cafe", "preview"))
        self.assertTrue(_is_clear_directive("kirim gambar ayam", "gambar"))

    def test_comments_and_stories_not_directive(self):
        self.assertFalse(_is_clear_directive("cuaca hari ini panas ya", "cuaca"))
        self.assertFalse(_is_clear_directive("aku lagi baca buku tentang saham", "saham"))
        self.assertFalse(_is_clear_directive("tadi aku cek cuaca", "cuaca"))

    def test_meta_question_not_directive(self):
        self.assertTrue(_is_meta_question("kamu tahu gak cara cek cuaca?"))
        self.assertFalse(_is_clear_directive("kamu tahu gak cara cek cuaca?", "cuaca"))
        self.assertFalse(_is_clear_directive("gimana cara pakai notion?", "notion"))

    def test_no_intent_never_directive(self):
        self.assertFalse(_is_clear_directive("cek cuaca surabaya", None))

    def test_directive_panjang_diverifikasi_llm(self):
        """Brief panjang (mis. landing page) tidak boleh lolos tanpa verifikasi LLM."""
        long_brief = "Buatkan landing page " + "dengan bagian lengkap " * 15
        self.assertFalse(_is_clear_directive(long_brief, "preview"))
        self.assertTrue(_is_clear_directive("cek cuaca surabaya", "cuaca"))


class TestClassifyMessage(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv.set_cache", new_callable=AsyncMock)
    @patch("src.kv.get_history", new_callable=AsyncMock, return_value=[])
    @patch("src.kv.get_cache", new_callable=AsyncMock)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_cache_hit_no_llm(self, mock_groq, mock_get_cache, mock_get_history, mock_set_cache):
        cached = json.dumps({"mode": "chat", "intent": None, "confidence": 0.9, "alasan": ""})
        mock_get_cache.return_value = cached
        res = await classify_message("cuaca hari ini panas ya", 1)
        self.assertEqual(res["mode"], "chat")
        mock_groq.assert_not_called()

    @patch("src.kv.set_cache", new_callable=AsyncMock)
    @patch("src.kv.get_history", new_callable=AsyncMock, return_value=[])
    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value=None)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_groq_success_caches(self, mock_groq, mock_get_cache, mock_get_history, mock_set_cache):
        mock_groq.return_value = '{"mode":"action","intent":"cuaca","confidence":0.95,"alasan":"minta"}'
        res = await classify_message("cek cuaca surabaya", 1)
        self.assertEqual(res["intent"], "cuaca")
        mock_set_cache.assert_awaited()

    @patch("src.intent_classifier._classify_with_gemini", new_callable=AsyncMock)
    @patch("src.kv.set_cache", new_callable=AsyncMock)
    @patch("src.kv.get_history", new_callable=AsyncMock, return_value=[])
    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value=None)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_fallback_gemini(self, mock_groq, mock_get_cache, mock_get_history, mock_set_cache, mock_gemini):
        mock_groq.side_effect = Exception("groq down")
        mock_gemini.return_value = '{"mode":"chat","intent":"none","confidence":0.8,"alasan":"komentar"}'
        res = await classify_message("saham itu menarik ya", 1)
        self.assertEqual(res["mode"], "chat")
        mock_gemini.assert_awaited()

    @patch("src.intent_classifier._classify_with_gemini", new_callable=AsyncMock)
    @patch("src.kv.get_history", new_callable=AsyncMock, return_value=[])
    @patch("src.kv.get_cache", new_callable=AsyncMock, return_value=None)
    @patch("src.groq.chat_groq", new_callable=AsyncMock)
    async def test_both_fail_returns_none(self, mock_groq, mock_get_cache, mock_get_history, mock_gemini):
        mock_groq.side_effect = Exception("groq down")
        mock_gemini.side_effect = Exception("gemini down")
        self.assertIsNone(await classify_message("saham itu menarik ya", 1))


class TestResolveIntent(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["GROQ_API_KEY"] = "mock-groq"

    def tearDown(self):
        os.environ.pop("GROQ_API_KEY", None)

    async def test_no_keyword_chat_without_llm(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls:
            res = await resolve_intent("halo apa kabar", 1, None)
            self.assertEqual(res["mode"], "chat")
            self.assertEqual(res["source"], "fast")
            mock_cls.assert_not_called()

    async def test_directive_skips_llm(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock), \
             patch("src.intent_classifier._remember_topic", new_callable=AsyncMock):
            res = await resolve_intent("cek saham BBCA", 1, "saham")
            self.assertEqual(res["mode"], "action")
            self.assertEqual(res["intent"], "saham")
            self.assertEqual(res["source"], "directive")
            mock_cls.assert_not_called()

    async def test_classifier_chat(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock):
            mock_cls.return_value = {"mode": "chat", "intent": None, "confidence": 0.9, "alasan": ""}
            res = await resolve_intent("cuaca hari ini panas ya", 1, "cuaca")
            self.assertEqual(res["mode"], "chat")
            self.assertIsNone(res["intent"])
            self.assertEqual(res["source"], "classifier")

    async def test_classifier_action_high_confidence(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock), \
             patch("src.intent_classifier._remember_topic", new_callable=AsyncMock):
            mock_cls.return_value = {"mode": "action", "intent": "cuaca", "confidence": EXECUTE_CONFIDENCE + 0.1, "alasan": ""}
            res = await resolve_intent("panas banget ya di surabaya", 1, "cuaca")
            self.assertEqual(res["mode"], "action")
            self.assertEqual(res["intent"], "cuaca")

    async def test_classifier_low_confidence_clarify(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock):
            mock_cls.return_value = {"mode": "action", "intent": "cuaca", "confidence": CLARIFY_CONFIDENCE + 0.1, "alasan": ""}
            res = await resolve_intent("kayaknya cuaca?", 1, "cuaca")
            self.assertEqual(res["mode"], "clarify")
            self.assertIn("cek cuaca", res["question"])

    async def test_very_low_confidence_chat(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock):
            mock_cls.return_value = {"mode": "action", "intent": "cuaca", "confidence": CLARIFY_CONFIDENCE - 0.1, "alasan": ""}
            res = await resolve_intent("ada awan", 1, "cuaca")
            self.assertEqual(res["mode"], "chat")

    async def test_llm_failure_falls_back_to_keyword(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock, return_value=None), \
             patch("src.intent_classifier._audit", new_callable=AsyncMock):
            res = await resolve_intent("panas banget ya di surabaya", 1, "cuaca")
            self.assertEqual(res["mode"], "action")
            self.assertEqual(res["intent"], "cuaca")
            self.assertEqual(res["source"], "fallback")

    async def test_long_directive_uses_classifier(self):
        """Brief landing panjang diverifikasi classifier (bukan langsung keyword)."""
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock), \
             patch("src.intent_classifier._remember_topic", new_callable=AsyncMock):
            mock_cls.return_value = {"mode": "action", "intent": "preview", "confidence": 0.95, "alasan": ""}
            long_brief = "Buatkan landing page " + "detail lengkap " * 25
            res = await resolve_intent(long_brief, 1, "preview")
            self.assertTrue(mock_cls.called)
            self.assertEqual(res["mode"], "action")
            self.assertEqual(res["intent"], "preview")


class TestResolveIntentDecision(unittest.IsolatedAsyncioTestCase):

    async def test_no_chat_id_backcompat(self):
        with patch("src.intent_classifier.resolve_intent", new_callable=AsyncMock) as mock_resolve:
            decision = await resolve_intent_decision("cek saham BBCA", None)
            self.assertEqual(decision["intent"], "saham")
            mock_resolve.assert_not_called()

    async def test_classifier_chat_disables_grounding(self):
        with patch("src.intent_classifier.resolve_intent", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = {"mode": "chat", "intent": None, "confidence": 0.9, "source": "classifier", "question": ""}
            decision = await resolve_intent_decision("cuaca hari ini panas ya", 1)
            self.assertFalse(decision["allow_grounding"])

    async def test_directive_allows_grounding(self):
        with patch("src.intent_classifier.resolve_intent", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = {"mode": "action", "intent": "cuaca", "confidence": 1.0, "source": "directive", "question": ""}
            decision = await resolve_intent_decision("cek cuaca surabaya", 1)
            self.assertTrue(decision["allow_grounding"])

    async def test_fast_chat_allows_grounding(self):
        decision = await resolve_intent_decision("halo apa kabar", 1)
        self.assertEqual(decision["mode"], "chat")
        self.assertTrue(decision["allow_grounding"])


class TestDetectIntentAsync(unittest.IsolatedAsyncioTestCase):

    async def test_saham_directive_no_llm(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls, \
             patch("src.intent_classifier._audit", new_callable=AsyncMock), \
             patch("src.intent_classifier._remember_topic", new_callable=AsyncMock):
            self.assertEqual(await detect_intent_async("cek saham BBCA", chat_id=999), "saham")
            self.assertEqual(await detect_intent_async("berapa harga saham BBCA", chat_id=999), "saham")
            mock_cls.assert_not_called()

    async def test_no_keyword_no_llm(self):
        with patch("src.intent_classifier.classify_message", new_callable=AsyncMock) as mock_cls:
            self.assertIsNone(await detect_intent_async("ada yang bisa kubantu?", chat_id=999))
            mock_cls.assert_not_called()


class TestIntentKV(unittest.IsolatedAsyncioTestCase):

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_save_get_clear_clarify(self, mock_kv):
        mock_kv.return_value = {"result": "OK"}
        ok = await save_intent_clarify(1, "cek cuaca", "cuaca", "Sepertinya kamu mau cek cuaca. Betul?")
        self.assertTrue(ok)
        cmd = mock_kv.call_args[0][0]
        self.assertEqual(cmd[0], "SET")
        self.assertEqual(cmd[1], "intent_clarify:1")
        self.assertIn("EX", cmd)

        payload = json.dumps({"perintah_asli": "cek cuaca", "intent": "cuaca", "question": "q"})
        mock_kv.return_value = {"result": payload}
        state = await get_intent_clarify(1)
        self.assertEqual(state["intent"], "cuaca")

        mock_kv.return_value = {"result": "1"}
        self.assertTrue(await clear_intent_clarify(1))

    @patch("src.kv._kv_pipeline", new_callable=AsyncMock)
    async def test_log_decision_and_feedback(self, mock_pipeline):
        mock_pipeline.return_value = [{"result": 1}]
        self.assertTrue(await log_intent_decision(1, "cek cuaca", "action", "cuaca", 0.9, "classifier"))
        self.assertTrue(await log_intent_feedback(1, "cek cuaca", "cuaca", "bukan"))
        commands = mock_pipeline.call_args[0][0]
        self.assertEqual(commands[0][0], "RPUSH")
        self.assertEqual(commands[2], ["EXPIRE", "intent_feedback:1", "2592000"])

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_get_intent_log(self, mock_kv):
        entry = json.dumps({"mode": "chat", "intent": ""})
        mock_kv.return_value = {"result": [entry]}
        logs = await get_intent_log(1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["mode"], "chat")


class TestIntentCallback(unittest.IsolatedAsyncioTestCase):

    def _query(self):
        query = MagicMock()
        query.message.chat = MagicMock()
        query.message.edit_text = AsyncMock()
        query.message.reply_text = AsyncMock()
        query.from_user.first_name = "Doni"
        return query

    async def test_yes_routes_with_candidate_intent(self):
        query = self._query()
        with patch("src.kv.get_intent_clarify", new_callable=AsyncMock) as mock_get, \
             patch("src.kv.clear_intent_clarify", new_callable=AsyncMock) as mock_clear, \
             patch("src.kv.log_intent_decision", new_callable=AsyncMock) as mock_log, \
             patch("src.bot._route_by_intent", new_callable=AsyncMock) as mock_route:
            mock_get.return_value = {"perintah_asli": "cek cuaca surabaya", "intent": "cuaca", "question": "q"}
            from src.bot import _handle_intent_callback
            await _handle_intent_callback(query, 1, "yes")
            mock_clear.assert_awaited()
            mock_log.assert_awaited()
            mock_route.assert_awaited()
            self.assertEqual(mock_route.await_args.args[3], "cuaca")

    async def test_no_logs_feedback_without_routing(self):
        query = self._query()
        with patch("src.kv.get_intent_clarify", new_callable=AsyncMock) as mock_get, \
             patch("src.kv.clear_intent_clarify", new_callable=AsyncMock), \
             patch("src.kv.log_intent_feedback", new_callable=AsyncMock) as mock_feedback, \
             patch("src.bot._route_by_intent", new_callable=AsyncMock) as mock_route:
            mock_get.return_value = {"perintah_asli": "cek cuaca surabaya", "intent": "cuaca", "question": "q"}
            from src.bot import _handle_intent_callback
            await _handle_intent_callback(query, 1, "no")
            mock_feedback.assert_awaited()
            mock_route.assert_not_awaited()

    async def test_expired_state(self):
        query = self._query()
        with patch("src.kv.get_intent_clarify", new_callable=AsyncMock, return_value=None), \
             patch("src.bot._route_by_intent", new_callable=AsyncMock) as mock_route:
            from src.bot import _handle_intent_callback
            await _handle_intent_callback(query, 1, "yes")
            query.message.reply_text.assert_awaited()
            mock_route.assert_not_awaited()


class TestAllowGroundingPlumbing(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["GROQ_API_KEY"] = "mock-groq"

    def tearDown(self):
        os.environ.pop("GROQ_API_KEY", None)

    @patch("src.grounding.prepare_grounding", new_callable=AsyncMock)
    @patch("src.groq.chat_groq", new_callable=AsyncMock, return_value="jawaban santai")
    async def test_skip_grounding_when_disabled(self, mock_groq, mock_ground):
        res = await call_model_with_fallback(
            jalur="fast",
            system_prompt="sistem",
            history=[],
            user_message="cuaca hari ini panas ya",
            tools=None,
            chat_id=1,
            allow_grounding=False,
        )
        self.assertEqual(res, "jawaban santai")
        mock_ground.assert_not_awaited()

    @patch("src.grounding.prepare_grounding", new_callable=AsyncMock)
    @patch("src.groq.chat_groq", new_callable=AsyncMock, return_value="jawaban")
    async def test_grounding_runs_when_enabled(self, mock_groq, mock_ground):
        mock_ground.return_value = ("skip", None, None, None)
        await call_model_with_fallback(
            jalur="fast",
            system_prompt="sistem",
            history=[],
            user_message="cek cuaca surabaya",
            tools=None,
            chat_id=1,
            allow_grounding=True,
        )
        mock_ground.assert_awaited()

    async def test_chat_with_oline_passes_flag(self):
        with patch("src.handlers.call_model_with_fallback", new_callable=AsyncMock) as mock_fallback, \
             patch("src.kv.get_model_preference", new_callable=AsyncMock, return_value="auto"), \
             patch("src.kv.get_memory", new_callable=AsyncMock, return_value=""), \
             patch("src.kv.get_history", new_callable=AsyncMock, return_value=[]), \
             patch("src.kv.save_history", new_callable=AsyncMock), \
             patch("src.kv.get_persona", new_callable=AsyncMock, return_value="profesional"), \
             patch("src.gemini._read_grounding_context", new_callable=AsyncMock, return_value=""), \
             patch("src.notion.read_memory_from_notion", new_callable=AsyncMock, return_value=""):
            mock_fallback.return_value = "ok"
            await chat_with_oline(chat_id=1, user_message="hai", allow_grounding=False)
            self.assertFalse(mock_fallback.await_args.kwargs["allow_grounding"])


if __name__ == "__main__":
    unittest.main()
