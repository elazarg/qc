"""What the player's guitar puts out, told to the designer: synthetic profiles only."""

import json
import unittest

from lab import profile


def setting(peak, lufs, centroid, lows=-6.):
    return {"takes": 1, "peak_db": peak, "lufs": lufs, "centroid_hz": centroid,
            "bands": {"lows": lows, "low mids": -4., "mids": -5., "presence": -12., "air": -20.}}


SUMMARY = {"Bridge Series": setting(-17., -30., 1800, lows=-6.),
           "Neck Parallel": setting(-33., -45., 2600, lows=-10.),
           "Middle Series": setting(-10., -24., 1100, lows=-3.)}


class ProfileFactsTest(unittest.TestCase):
    def test_nothing_profiled_says_nothing(self):
        self.assertIsNone(profile.facts({}))
        self.assertIsNone(profile.facts({}, "Bridge Series"))

    def test_every_setting_against_a_typical_humbucker_and_bridge_series(self):
        found = profile.facts(SUMMARY)
        json.dumps(found)
        self.assertEqual(found["reference"], "Bridge Series")
        self.assertEqual(found["typical_humbucker_peak_db"], profile.TYPICAL_PEAK_DB)
        self.assertNotIn("recorded_on", found)
        self.assertEqual(set(found["settings"]), set(SUMMARY))
        bridge = found["settings"]["Bridge Series"]
        self.assertEqual(bridge["peak_against_typical_humbucker_db"], 1.)
        self.assertNotIn("against_reference", bridge)
        self.assertIn("about as hot", bridge["implies"])
        neck = found["settings"]["Neck Parallel"]
        self.assertEqual(neck["peak_against_typical_humbucker_db"], -15.)
        self.assertEqual(neck["against_reference"]["loudness_db"], -15.)
        self.assertGreater(neck["against_reference"]["brightness_octaves"], .2)
        self.assertEqual(neck["against_reference"]["lows_db"], -4.)
        self.assertIn("about 15 dB weaker than presets assume", neck["implies"])
        self.assertIn("distort less", neck["implies"])
        self.assertIn("brighter than Bridge Series", neck["implies"])
        self.assertIn("thinner in the lows", neck["implies"])
        middle = found["settings"]["Middle Series"]
        self.assertIn("about 8 dB hotter", middle["implies"])
        self.assertIn("darker than Bridge Series", middle["implies"])
        self.assertIn("fuller in the lows", middle["implies"])

    def test_the_setting_recorded_on_in_full_and_the_others_in_a_word(self):
        found = profile.facts(SUMMARY, "neck parallel")
        self.assertEqual(found["recorded_on"], "Neck Parallel")
        self.assertEqual(list(found["settings"]), ["Neck Parallel"])
        self.assertEqual(set(found["other_settings"]), {"Bridge Series", "Middle Series"})
        self.assertIn("hotter", found["other_settings"]["Middle Series"])

    def test_an_unprofiled_setting_falls_back_to_all(self):
        found = profile.facts(SUMMARY, "Neck Series")
        self.assertEqual(found["recorded_on"], "Neck Series")
        self.assertIs(found["recorded_on_profiled"], False)
        self.assertEqual(set(found["settings"]), set(SUMMARY))

    def test_without_bridge_series_only_the_humbucker_is_compared(self):
        found = profile.facts({"Neck": setting(-24., None, 1500)})
        self.assertIsNone(found["reference"])
        self.assertIn("note", found)
        self.assertNotIn("against_reference", found["settings"]["Neck"])
        self.assertIn("about 6 dB weaker", found["settings"]["Neck"]["implies"])


if __name__ == "__main__":
    unittest.main()
