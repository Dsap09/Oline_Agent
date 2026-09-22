"""
Unit test suite untuk fitur Pending Task dengan Konfirmasi Pengguna.
Jalankan dengan: python -m unittest tests/test_pending_task_confirmation.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import _is_casual_or_command, is_skip_request, is_retry_request
from src.kv import save_pending_task, update_pending_task_retry_count


class TestPendingTaskConfirmation(unittest.IsolatedAsyncioTestCase):

    def test_is_skip_request(self):
        """Tes pendeteksian instruksi skip."""
        self.assertTrue(is_skip_request("skip"))
        self.assertTrue(is_skip_request("gak usah"))
        self.assertTrue(is_skip_request("batal"))
        self.assertTrue(is_skip_request("abaikan aja"))
        self.assertFalse(is_skip_request("coba lagi"))
        self.assertFalse(is_skip_request("buatkan website"))

    def test_is_skip_request_clear_stop(self):
        """Tes pendeteksian perintah clear/stop task & penolakan topik (anti loop saham)."""
        self.assertTrue(is_skip_request("clear task"))
        self.assertTrue(is_skip_request("stop task"))
        self.assertTrue(is_skip_request("reset"))
        self.assertTrue(is_skip_request("berhenti"))
        self.assertTrue(is_skip_request("aku ga bahas saham"))
        self.assertTrue(is_skip_request("aku nggak bahas saham"))

    def test_is_casual_or_command(self):
        """Sapaan/perintah kontrol tidak boleh diarahkan ke intent berat lewat riwayat."""
        self.assertTrue(_is_casual_or_command("hi"))
        self.assertTrue(_is_casual_or_command("clear task"))
        self.assertTrue(_is_casual_or_command("stop"))
        self.assertTrue(_is_casual_or_command("status"))
        self.assertFalse(_is_casual_or_command("bumi"))
        self.assertFalse(_is_casual_or_command("surabaya"))

    def test_is_retry_request(self):
        """Tes pendeteksian instruksi retry."""
        self.assertTrue(is_retry_request("coba lagi"))
        self.assertTrue(is_retry_request("ulang"))
        self.assertTrue(is_retry_request("retry"))
        self.assertTrue(is_retry_request("jalankan lagi"))
        self.assertFalse(is_retry_request("skip"))

    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_save_pending_task_structure(self, mock_kv):
        """Tes save_pending_task menyimpan max_retry: 1 dan retry_count: 0."""
        mock_kv.return_value = {"result": "OK"}
        res = await save_pending_task(
            chat_id=12345,
            user_message="buat landing page Sakura Brew",
            intent="preview",
            user_name="Doni",
            error_reason="Connection error",
        )
        self.assertTrue(res)
        mock_kv.assert_called_once()
        cmd = mock_kv.call_args[0][0]
        self.assertEqual(cmd[0], "SET")
        import json
        payload = json.loads(cmd[2])
        self.assertEqual(payload["perintah"], "buat landing page Sakura Brew")
        self.assertEqual(payload["retry_count"], 0)
        self.assertEqual(payload["max_retry"], 1)

    @patch("src.kv.clear_pending_task", new_callable=AsyncMock)
    @patch("src.kv._kv_request", new_callable=AsyncMock)
    async def test_update_pending_task_retry_count_max_limit(self, mock_kv, mock_clear):
        """Tes update_pending_task_retry_count tidak mengizinkan retry melebihi max_retry."""
        task = {
            "perintah": "test task",
            "retry_count": 1,
            "max_retry": 1,
        }
        res = await update_pending_task_retry_count(chat_id=12345, task=task)
        self.assertFalse(res)


if __name__ == "__main__":
    unittest.main()
