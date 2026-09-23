"""
Unit test suite untuk fitur List & Hapus Deployment Vercel.
Jalankan dengan: python -m unittest tests/test_vercel_manage.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import detect_intent
from src.tools import (
    delete_vercel_deployment,
    get_tools_for_intent,
    list_vercel_deployments,
)


class TestVercelManagement(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["VERCEL_API_TOKEN"] = "vcp_mock_token_12345"

    def test_detect_intent_vercel_management(self):
        """Tes pendeteksian intent deploy untuk list & delete di bot.py."""
        self.assertEqual(detect_intent("list landing page"), "deploy")
        self.assertEqual(detect_intent("daftar deployment yang pernah dibuat"), "deploy")
        self.assertEqual(detect_intent("hapus landing page nomor 1"), "deploy")
        self.assertEqual(detect_intent("hapus deployment vercel"), "deploy")

    def test_get_tools_for_intent_deploy(self):
        """Tes deklarasi tool yang dikembalikan untuk intent deploy."""
        tools = get_tools_for_intent("deploy")
        tool_names = [t["name"] for t in tools]
        self.assertIn("deploy_to_vercel", tool_names)
        self.assertIn("list_vercel_deployments", tool_names)
        self.assertIn("delete_vercel_deployment", tool_names)

    @patch("httpx.AsyncClient.get", new_callable=AsyncMock)
    async def test_list_vercel_deployments_success(self, mock_get):
        """Tes list_vercel_deployments mengembalikan status success dan daftar deployment buatan Oline."""
        deploy_response = MagicMock()
        deploy_response.status_code = 200
        deploy_response.json.return_value = {
            "deployments": [
                {
                    "uid": "dpl_1111",
                    "name": "landing-page-minuman",
                    "url": "landing-page-minuman.vercel.app",
                    "created": 1700000000,
                    "meta": {},
                    "state": "READY",
                },
                {
                    "uid": "dpl_2222",
                    "name": "landing-page-fashion",
                    "url": "landing-page-fashion.vercel.app",
                    "created": 1700001000,
                    "meta": {},
                    "state": "READY",
                },
                {
                    "uid": "dpl_3333",
                    "name": "d-reads",
                    "url": "d-reads.vercel.app",
                    "created": 1700002000,
                    "meta": {"githubDeployment": "1", "githubCommitSha": "abc"},
                    "state": "READY",
                },
                {
                    "uid": "dpl_4444",
                    "name": "oline-personal",
                    "url": "oline-personal.vercel.app",
                    "created": 1700003000,
                    "meta": {},
                    "state": "READY",
                },
            ]
        }
        proj_response = MagicMock()
        proj_response.status_code = 200
        proj_response.json.return_value = {
            "projects": [
                {
                    "name": "landing-page-minuman",
                    "targets": {"production": {"alias": ["landing-page-minuman.vercel.app"]}},
                },
                {
                    "name": "landing-page-fashion",
                    "targets": {"production": {"alias": ["landing-page-fashion.vercel.app"]}},
                },
            ]
        }
        mock_get.side_effect = [deploy_response, proj_response]

        res = await list_vercel_deployments()
        self.assertEqual(res["status"], "success")
        # Hanya project buatan Oline (tanpa meta github, bukan oline-personal).
        self.assertEqual(res["total"], 2)
        self.assertEqual(len(res["deployments"]), 2)
        # Diurutkan terbaru dulu.
        self.assertEqual(res["deployments"][0]["id"], "dpl_2222")
        self.assertEqual(res["deployments"][0]["name"], "landing-page-fashion")
        self.assertEqual(res["deployments"][0]["url"], "https://landing-page-fashion.vercel.app")
        self.assertEqual(res["deployments"][1]["id"], "dpl_1111")

    @patch("httpx.AsyncClient.delete", new_callable=AsyncMock)
    async def test_delete_vercel_deployment_success(self, mock_delete):
        """Tes delete_vercel_deployment mengembalikan status success saat status HTTP 200/204."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"state": "DELETED"}
        mock_delete.return_value = mock_response

        res = await delete_vercel_deployment("dpl_1111")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["deployment_id"], "dpl_1111")
        self.assertIn("berhasil dihapus", res["message"])

    @patch("httpx.AsyncClient.delete", new_callable=AsyncMock)
    async def test_delete_vercel_deployment_error(self, mock_delete):
        """Tes delete_vercel_deployment mengembalikan error saat status HTTP != 200/204."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Deployment Not Found"
        mock_delete.return_value = mock_response

        res = await delete_vercel_deployment("dpl_invalid")
        self.assertIn("error", res)
        self.assertIn("Status 404", res["error"])


if __name__ == "__main__":
    unittest.main()
