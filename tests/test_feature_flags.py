"""
Unit test untuk pemisahan flag fitur user_disabled vs system_disabled dan sinkronisasi status.
Jalankan dengan: python -m unittest tests/test_feature_flags.py
"""

import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "")

import src.kv as kv


class _FakeKV:
    """Simulasi Vercel KV dalam memori untuk menguji feature flags."""

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
        if op == "INCR":
            self.store[key] = int(self.store.get(key, 0)) + 1
            return {"result": self.store[key]}
        if op == "EXPIRE":
            return {"result": 1}
        return {"result": None}


class TestFeatureFlags(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.fake = _FakeKV()
        self.patcher = patch.object(kv, "_kv_request", new_callable=AsyncMock, side_effect=self.fake.__call__)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_default_active(self):
        self.assertTrue(await kv.is_feature_active("drive"))
        self.assertTrue(await kv.get_feature_status("drive"))

    async def test_user_disable(self):
        await kv.set_user_feature("drive", False)
        self.assertFalse(await kv.is_feature_active("drive"))
        self.assertFalse(await kv.is_feature_enabled("drive"))

    async def test_user_enable_overrides_system(self):
        await kv.set_system_feature("drive", False)
        self.assertFalse(await kv.is_feature_active("drive"))
        # User mengaktifkan -> hapus flag sistem juga
        await kv.set_user_feature("drive", True)
        self.assertTrue(await kv.is_feature_active("drive"))

    async def test_system_disable_separate_from_user(self):
        await kv.set_system_feature("drive", False)
        self.assertFalse(await kv.is_feature_active("drive"))
        flags = await kv.get_all_feature_flags()
        self.assertFalse(flags["drive"]["system_disabled"] is False)  # system_disabled True

    async def test_toggle_feature_is_user_level(self):
        await kv.toggle_feature("drive", False)
        self.assertFalse(await kv.is_feature_active("drive"))
        await kv.toggle_feature("drive", True)
        self.assertTrue(await kv.is_feature_active("drive"))

    async def test_legacy_bool_migration(self):
        # Data lama berbentuk bool: drive False = dinonaktifkan user
        self.fake.store["feature_flags"] = '{"drive": false}'
        self.assertFalse(await kv.is_feature_active("drive"))
        flags = await kv.get_all_feature_flags()
        self.assertTrue(flags["drive"]["user_disabled"])
        self.assertFalse(flags["drive"]["system_disabled"])

    async def test_unknown_feature_defaults_active(self):
        self.assertTrue(await kv.is_feature_active("saham"))


if __name__ == "__main__":
    unittest.main()
