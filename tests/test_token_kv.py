"""
Unit test untuk penyimpanan token di Vercel KV (Kelola Token via KV) & pembacaan di drive.
Jalankan dengan: python -m unittest tests/test_token_kv.py
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "")

import src.drive as drive
import src.kv as kv


class _FakeKV:
    def __init__(self):
        self.store = {}

    async def __call__(self, command):
        op = command[0].upper()
        key = command[1] if len(command) > 1 else None
        if op == "GET":
            return {"result": self.store.get(key)}
        if op == "SET":
            self.store[key] = command[2]
            return {"result": "OK"}
        if op == "DEL":
            self.store.pop(key, None)
            return {"result": 1}
        return {"result": None}


class TestTokenKv(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.fake = _FakeKV()
        self.patcher = patch.object(kv, "_kv_request", new_callable=AsyncMock, side_effect=self.fake.__call__)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_save_and_get_token(self):
        self.assertTrue(await kv.save_token("drive", "1//abc123"))
        self.assertEqual(await kv.get_token("drive"), "1//abc123")

    async def test_get_token_missing(self):
        self.assertIsNone(await kv.get_token("drive"))

    async def test_clear_token(self):
        await kv.save_token("drive", "1//abc123")
        self.assertTrue(await kv.clear_token("drive"))
        self.assertIsNone(await kv.get_token("drive"))

    async def test_save_empty(self):
        self.assertFalse(await kv.save_token("", "x"))
        self.assertFalse(await kv.save_token("drive", ""))


class TestDriveTokenFromKv(unittest.TestCase):
    """Tes get_drive_service membaca refresh token dari KV dulu, fallback ke env."""

    def test_uses_kv_token_first(self):
        with patch.object(kv, "get_token_sync", return_value="1//kv-token") as mock_kv:
            result = drive._get_refresh_token()
            mock_kv.assert_called_once_with("drive")
            self.assertEqual(result, "1//kv-token")

    def test_falls_back_to_env(self):
        os.environ["GOOGLE_DRIVE_REFRESH_TOKEN"] = "env-token"
        with patch.object(kv, "get_token_sync", return_value=None):
            self.assertEqual(drive._get_refresh_token(), "env-token")
        os.environ.pop("GOOGLE_DRIVE_REFRESH_TOKEN", None)

    def test_kv_empty_falls_back_to_env(self):
        os.environ["GOOGLE_DRIVE_REFRESH_TOKEN"] = "env-token"
        with patch.object(kv, "get_token_sync", return_value="   "):
            self.assertEqual(drive._get_refresh_token(), "env-token")
        os.environ.pop("GOOGLE_DRIVE_REFRESH_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
