import unittest

from lanebridge.lanes import build_lane, empty_lane, lane_key, normalize_color, stale_keys, tool_for_key
from lanebridge.sources import MacroVariableSource, parse_spool_id

# Shapes taken from the live Spoolman on the printer.
PLA = {
    "id": 6, "archived": False,
    "filament": {"id": 7, "material": "PLA", "color_hex": "f0be02",
                 "settings_extruder_temp": 210, "settings_bed_temp": 50},
}
ASA_NO_TEMPS = {"id": 9, "archived": False,
                "filament": {"id": 11, "material": "ASA", "color_hex": "404040"}}


class LaneTests(unittest.TestCase):
    def test_normal(self):
        e = build_lane(1, PLA)
        self.assertEqual({k: e[k] for k in ("color", "material", "bed_temp", "nozzle_temp",
                                             "scan_time", "td", "lane", "spool_id")}, {
            "color": "#f0be02", "material": "PLA", "bed_temp": 50, "nozzle_temp": 210,
            "scan_time": "", "td": "", "lane": "1", "spool_id": 6,
        })
        self.assertNotIn("filament_id", e)  # reserved for Orca preset ids

    def test_full_spoolman_data(self):
        spool = {
            "id": 6, "registered": "2026-06-29T15:37:36Z", "first_used": "2026-06-29T18:42:55Z",
            "last_used": "2026-10-27T11:40:33Z", "price": 24.99,
            "remaining_weight": 412.345, "initial_weight": 1000.0, "spool_weight": 140.0,
            "used_weight": 587.655, "remaining_length": 138000.4, "used_length": 197000.6,
            "location": "Dry box 2", "lot_nr": "L123", "comment": "opened Oct", "archived": False,
            "extra": {"nfc_uid": "\"04A1B2\""},
            "filament": {
                "id": 7, "name": "Panchroma Matte Savannah Yellow", "material": "PLA",
                "vendor": {"id": 8, "name": "Polymaker"}, "density": 1.24, "diameter": 1.75,
                "weight": 1000.0, "color_hex": "f0be02", "external_id": "polymaker_pla_x",
                "settings_extruder_temp": 210, "settings_bed_temp": 50,
                "extra": {"td": "4.2", "notes": "not json"},
            },
        }
        e = build_lane(1, spool)
        self.assertEqual(e["name"], "Panchroma Matte Savannah Yellow")
        self.assertEqual(e["vendor"], "Polymaker")
        self.assertEqual(e["colors"], ["#f0be02"])
        self.assertEqual(e["spoolman_filament_id"], 7)
        self.assertEqual(e["external_id"], "polymaker_pla_x")
        self.assertEqual((e["diameter"], e["density"]), (1.75, 1.24))
        self.assertEqual(e["remaining_weight"], 412.3)
        self.assertEqual(e["remaining_percent"], 41)
        self.assertEqual(e["remaining_length"], 138000)
        self.assertEqual((e["used_weight"], e["initial_weight"], e["spool_weight"]), (587.7, 1000, 140))
        self.assertEqual(e["price"], 24.99)
        self.assertEqual((e["location"], e["lot_nr"], e["comment"]), ("Dry box 2", "L123", "opened Oct"))
        self.assertEqual(e["last_used"], "2026-10-27T11:40:33Z")
        self.assertEqual(e["td"], "4.2")
        self.assertEqual(e["spool_extra"], {"nfc_uid": "04A1B2"})
        self.assertEqual(e["filament_extra"], {"td": 4.2, "notes": "not json"})

    def test_same_keys_for_empty_and_full(self):
        self.assertEqual(set(build_lane(0, None)), set(build_lane(0, PLA)))

    def test_overused_spool_clamps_percent(self):
        s = dict(PLA, remaining_weight=0.0, initial_weight=750.0, used_weight=3577.5)
        self.assertEqual(build_lane(0, s)["remaining_percent"], 0)

    def test_lane_is_string(self):
        # Orca skips lanes whose "lane" is not a JSON string.
        self.assertIsInstance(build_lane(3, PLA)["lane"], str)
        self.assertIsInstance(build_lane(3, None)["lane"], str)

    def test_missing_temps(self):
        e = build_lane(0, ASA_NO_TEMPS)
        self.assertIsNone(e["nozzle_temp"])
        self.assertIsNone(e["bed_temp"])
        self.assertEqual((e["material"], e["color"], e["spool_id"]), ("ASA", "#404040", 9))

    def test_multi_colour_uses_first(self):
        s = {"id": 3, "filament": {"material": "PLA", "color_hex": None,
                                   "multi_color_hexes": "FF0000,00ff00,0000FF",
                                   "multi_color_direction": "coaxial"}}
        e = build_lane(0, s)
        self.assertEqual(e["color"], "#ff0000")
        self.assertEqual(e["colors"], ["#ff0000", "#00ff00", "#0000ff"])
        self.assertEqual(e["multi_color_direction"], "coaxial")

    def test_missing_colour(self):
        s = {"id": 4, "filament": {"material": "PETG"}}
        e = build_lane(0, s)
        self.assertEqual(e["color"], "")
        self.assertEqual(e["material"], "PETG")

    def test_archived_spool_is_empty(self):
        s = dict(PLA, archived=True)
        self.assertEqual(build_lane(1, s), empty_lane(1))
        self.assertEqual(build_lane(1, s, archived_as_empty=False)["spool_id"], 6)

    def test_spool_404_and_empty_tool(self):
        e = build_lane(0, None)
        self.assertEqual({k: e[k] for k in ("color", "material", "bed_temp", "nozzle_temp",
                                             "scan_time", "td", "lane", "spool_id",
                                             "remaining_weight", "colors")}, {
            "color": "", "material": "", "bed_temp": None, "nozzle_temp": None,
            "scan_time": "", "td": "", "lane": "0", "spool_id": None,
            "remaining_weight": None, "colors": [],
        })

    def test_bad_temps(self):
        s = {"id": 1, "filament": {"material": "PLA", "settings_extruder_temp": 0,
                                   "settings_bed_temp": "abc"}}
        e = build_lane(0, s)
        self.assertIsNone(e["nozzle_temp"])
        self.assertIsNone(e["bed_temp"])

    def test_colours(self):
        self.assertEqual(normalize_color("#ABCDEF"), "#abcdef")
        self.assertEqual(normalize_color("abcdef80"), "#abcdef")
        self.assertEqual(normalize_color("0x123456"), "#123456")
        self.assertEqual(normalize_color("#None"), "")
        self.assertEqual(normalize_color(None), "")

    def test_keys(self):
        self.assertEqual(lane_key(0), "lane1")
        self.assertEqual(tool_for_key("lane2"), 1)
        self.assertIsNone(tool_for_key("lane0"))
        self.assertIsNone(tool_for_key("other"))
        self.assertEqual(stale_keys(["lane1", "lane2", "lane3", "lane10", "x"], [0, 1]),
                         ["lane3", "lane10"])


class SourceTests(unittest.TestCase):
    def test_parse_spool_id(self):
        for raw, want in [(9, 9), ("9", 9), (9.0, 9), (None, None), ("None", None),
                          ("", None), (0, None), (-1, None), (True, None), ("'7'", 7)]:
            self.assertEqual(parse_spool_id(raw), want, raw)

    def test_extract(self):
        src = MacroVariableSource("gcode_macro T{tool}", "spool_id")
        self.assertEqual(src.subscription([0, 1]),
                         {"gcode_macro T0": ["spool_id"], "gcode_macro T1": ["spool_id"]})
        status = {"gcode_macro T0": {"spool_id": 9}, "gcode_macro T1": {"color": "c44"}}
        self.assertEqual(src.extract(status, [0, 1]), {0: 9})


if __name__ == "__main__":
    unittest.main()
