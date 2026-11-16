import asyncio
import unittest

from lanebridge.bridge import Bridge
from lanebridge.config import Config

from tests.fake_moonraker import FakeMoonraker, wait_for

SPOOLS = {
    6: {"id": 6, "archived": False,
        "filament": {"material": "PLA", "color_hex": "f0be02",
                     "settings_extruder_temp": 210, "settings_bed_temp": 50}},
    9: {"id": 9, "archived": False, "filament": {"material": "ASA", "color_hex": "404040"}},
}


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeMoonraker(tools=(0, 1), spools=SPOOLS)
        self.fake.spool_ids = {0: 9, 1: 6}
        # leftovers from an earlier, bigger setup
        self.fake.db["lane_data"] = {"lane5": {"lane": "4", "material": "PLA"}}
        await self.fake.start()
        self.cfg = Config(websocket_url=self.fake.url, refresh_interval=300,
                          reconnect_min=0.05, reconnect_max=0.2, debounce=0.05)
        self.bridge = Bridge(self.cfg)
        self.task = None

    async def asyncTearDown(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        await self.fake.stop()

    async def start(self):
        self.task = asyncio.create_task(self.bridge.run_forever())
        await wait_for(lambda: "lane2" in self.fake.db.get("lane_data", {}))

    def lanes(self):
        return self.fake.db["lane_data"]

    async def test_initial_sync(self):
        await self.start()
        self.assertEqual(self.lanes()["lane1"]["material"], "ASA")
        self.assertEqual(self.lanes()["lane1"]["lane"], "0")
        self.assertEqual(self.lanes()["lane2"]["color"], "#f0be02")
        self.assertNotIn("lane5", self.lanes())
        self.assertEqual(len(self.fake.writes()), 2)

    async def test_change_writes_once(self):
        await self.start()
        before = len(self.fake.writes())
        await self.fake.set_spool(0, 6)
        await wait_for(lambda: self.lanes()["lane1"]["spool_id"] == 6, timeout=2)
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.fake.writes()) - before, 1)
        self.assertEqual(self.fake.writes()[-1]["key"], "lane1")

    async def test_no_change_no_write(self):
        await self.start()
        before = len(self.fake.writes())
        await self.fake.set_spool(0, 9)  # same spool again
        await self.fake.notify("notify_status_update", [{"gcode_macro T1": {"color": "c44"}}, 2.0])
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.fake.writes()), before)

    async def test_clear_empties_lane(self):
        await self.start()
        await self.fake.set_spool(1, None)
        await wait_for(lambda: self.lanes()["lane2"]["material"] == "", timeout=2)
        self.assertIsNone(self.lanes()["lane2"]["spool_id"])
        self.assertEqual(self.lanes()["lane2"]["lane"], "1")

    async def test_missing_spool_empties_lane(self):
        await self.start()
        await self.fake.set_spool(0, 12345)
        await wait_for(lambda: self.lanes()["lane1"]["material"] == "", timeout=2)

    async def test_spoolman_down_keeps_lane(self):
        await self.start()
        self.fake.spoolman_up = False
        await self.fake.set_spool(0, 6)
        await asyncio.sleep(0.3)
        self.assertEqual(self.lanes()["lane1"]["spool_id"], 9)  # not blanked
        self.fake.spoolman_up = True
        await self.fake.notify("notify_spoolman_status_changed", [{"spoolman_connected": True}])
        await wait_for(lambda: self.lanes()["lane1"]["spool_id"] == 6, timeout=2)

    async def test_reconnect_full_resync(self):
        await self.start()
        subs = self.fake.count("printer.objects.subscribe")
        await self.fake.stop()
        # while Moonraker is down: spool changes and someone edits the db
        self.fake.spool_ids[1] = 9
        self.fake.db["lane_data"]["lane1"]["color"] = "#000000"
        writes_before = len(self.fake.writes())
        await asyncio.sleep(0.3)
        await self.fake.start()
        await wait_for(lambda: self.fake.count("printer.objects.subscribe") > subs, timeout=3)
        await wait_for(lambda: self.lanes()["lane2"]["spool_id"] == 9, timeout=2)
        self.assertEqual(self.lanes()["lane1"]["color"], "#404040")
        self.assertEqual(len(self.fake.writes()) - writes_before, 2)

    async def test_unchanged_after_restart_no_write(self):
        await self.start()
        await self.fake.stop()
        writes_before = len(self.fake.writes())
        subs = self.fake.count("printer.objects.subscribe")
        await self.fake.start()
        await wait_for(lambda: self.fake.count("printer.objects.subscribe") > subs, timeout=3)
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.fake.writes()), writes_before)

    async def test_klippy_restart_resubscribes(self):
        await self.start()
        subs = self.fake.count("printer.objects.subscribe")
        self.fake.klippy_state = "startup"
        await self.fake.notify("notify_klippy_disconnected", [])
        await asyncio.sleep(0.1)
        self.fake.spool_ids[0] = 6
        self.fake.klippy_state = "ready"
        await wait_for(lambda: self.fake.count("printer.objects.subscribe") > subs, timeout=4)
        await wait_for(lambda: self.lanes()["lane1"]["spool_id"] == 6, timeout=2)

    async def test_dry_run_writes_nothing(self):
        lines = []
        b = Bridge(self.cfg, dry_run=True, out=lines.append)
        await b.run_once()
        self.assertEqual(self.fake.writes(), [])
        self.assertIn("lane5", self.lanes())
        self.assertTrue(any(l.startswith("lane1 (would write)") for l in lines))
        self.assertTrue(any(l.startswith("lane5 (would delete)") for l in lines))

    async def test_purge(self):
        await Bridge(self.cfg).run_once()
        keys = await Bridge(self.cfg).purge()
        self.assertEqual(keys, ["lane1", "lane2"])
        self.assertEqual(self.lanes(), {})

    async def test_fixed_tool_list(self):
        self.cfg.tools = [0]
        await Bridge(self.cfg).run_once()
        self.assertEqual(sorted(self.lanes()), ["lane1"])


if __name__ == "__main__":
    unittest.main()
