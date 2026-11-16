import os
import tempfile
import unittest

from lanebridge.config import load
from lanebridge.spoolman import SpoolCache, SpoolmanUnavailable, make_moonraker_fetch


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_ttl_and_force(self):
        now = [0.0]
        calls = []

        async def fetch(sid):
            calls.append(sid)
            return {"id": sid} if sid != 404 else None

        c = SpoolCache(fetch, ttl=300, clock=lambda: now[0])
        await c.get(1)
        await c.get(1)
        self.assertEqual(calls, [1])
        self.assertIsNone(await c.get(404))
        self.assertIsNone(await c.get(404))
        self.assertEqual(calls, [1, 404])  # "not found" is cached too
        await c.get(1, force=True)
        self.assertEqual(calls, [1, 404, 1])
        now[0] = 301
        await c.get(1)
        self.assertEqual(calls, [1, 404, 1, 1])

    async def test_proxy_responses(self):
        replies = {
            1: {"response": {"id": 1}, "error": None},
            2: {"response": None, "error": {"status_code": 404, "message": "Not Found"}},
            3: {"response": None, "error": {"status_code": 503, "message": "down"}},
        }

        async def call(method, params):
            self.assertEqual(method, "server.spoolman.proxy")
            self.assertTrue(params["use_v2_response"])
            return replies[int(params["path"].rsplit("/", 1)[1])]

        fetch = make_moonraker_fetch(call)
        self.assertEqual(await fetch(1), {"id": 1})
        self.assertIsNone(await fetch(2))
        with self.assertRaises(SpoolmanUnavailable):
            await fetch(3)


class ConfigTests(unittest.TestCase):
    def test_defaults_without_file(self):
        cfg = load("/nonexistent/lanebridge.conf")
        self.assertIsNone(cfg.tools)
        self.assertEqual(cfg.object_template, "gcode_macro T{tool}")

    def test_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("[tools]\ntools: 1, 0 # comment\n[spoolman]\nrefresh_interval: 60\n")
        try:
            cfg = load(f.name)
            self.assertEqual(cfg.tools, [0, 1])
            self.assertEqual(cfg.refresh_interval, 60)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
