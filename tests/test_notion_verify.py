"""
Unit test untuk verifikasi database Notion (Catatan & Memori) & registrasi tool.
Jalankan dengan: python -m unittest tests/test_notion_verify.py
"""

import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "")

from src.notion import verify_notion_databases
from src.tools import TOOL_DECLARATIONS, TOOL_EXECUTORS, TOOLS_BY_INTENT


class TestToolRegistration(unittest.TestCase):
    def test_check_notion_databases_declared(self):
        names = [t.get("name") for t in TOOL_DECLARATIONS if isinstance(t, dict)]
        self.assertIn("check_notion_databases", names)

    def test_check_notion_databases_in_intent(self):
        names = [t if isinstance(t, str) else t.get("name") for t in TOOLS_BY_INTENT.get("notion", [])]
        self.assertIn("check_notion_databases", names)

    def test_check_notion_databases_executor(self):
        self.assertIn("check_notion_databases", TOOL_EXECUTORS)


class TestVerifyNotion(unittest.IsolatedAsyncioTestCase):
    @patch("httpx.AsyncClient.get")
    async def test_verify_both_databases_ok(self, mock_get):
        os_patch = patch.dict(
            "os.environ",
            {
                "NOTION_API_KEY": "ntn_test",
                "NOTION_DATABASE_ID": "3ceec30101df806fa6ddf65ab5aa6e40",
                "NOTION_MEMORY_DATABASE_ID": "3cfec30101df80ab97d8ea53747e5bcd",
            },
        )
        os_patch.start()
        self.addCleanup(os_patch.stop)

        async def fake_get(url, headers=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"title": [{"plain_text": "DB"}], "properties": {}}
            return resp

        mock_get.side_effect = fake_get

        result = await verify_notion_databases()
        self.assertNotIn("error", result)
        self.assertEqual(result["results"]["catatan"]["status"], "ok")
        self.assertEqual(result["results"]["memori"]["status"], "ok")

    @patch("httpx.AsyncClient.get")
    async def test_verify_not_shared_404(self, mock_get):
        os_patch = patch.dict(
            "os.environ",
            {
                "NOTION_API_KEY": "ntn_test",
                "NOTION_DATABASE_ID": "3ceec30101df806fa6ddf65ab5aa6e40",
            },
        )
        os_patch.start()
        self.addCleanup(os_patch.stop)

        async def fake_get(url, headers=None):
            resp = MagicMock()
            resp.status_code = 404
            return resp

        mock_get.side_effect = fake_get

        result = await verify_notion_databases()
        self.assertEqual(result["results"]["catatan"]["status"], "not_shared")


if __name__ == "__main__":
    unittest.main()
