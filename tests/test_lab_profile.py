"""What the player's guitar puts out, told to the designer: synthetic profiles only."""

import json
import unittest

import numpy as np
from scipy import signal as dsp

from lab.hearing import profile
from lab.hearing.audio import RATE


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

    def test_what_is_said_is_said_of_the_takes(self):
        found = profile.facts(SUMMARY)
        for said in found["settings"].values():
            self.assertTrue(said["implies"].startswith("in these takes"))
            self.assertNotIn("harsher", said["implies"])
            self.assertNotIn("will distort", said["implies"])
            self.assertEqual(said["level_from"], "the largest single peak")       # all an old summary holds


LOW_RIFF = (82.4, 110., 146.8, 196., 146.8, 110.)
STRUM = "open strings strummed once"


def take(pitches=LOW_RIFF, level=.12, accent=1., hiss_db=None, tilt=None):
    """One made-up pickup playing `pitches`, half a second apart, then a second of silence: every note has the
    same spectral envelope (partials at 1/k up to 5 kHz). `accent` picks the fourth note that much harder;
    `tilt` is a filter standing for another pickup."""
    total = round((len(pitches) * .5 + 1.5) * RATE)
    out = np.zeros(total)
    for index, pitch in enumerate(pitches):
        begin = round((.25 + .5 * index) * RATE)
        time = np.arange(total - begin) / RATE
        tone = sum(np.sin(2 * np.pi * pitch * k * time + k) / k for k in range(1, 60) if pitch * k < 5000)
        out[begin:] += level * (accent if index == 3 else 1.) * tone * np.exp(-6 * time)
    if tilt is not None:
        out = tilt(out)
    if hiss_db is not None:
        out = out + np.random.default_rng(0).normal(0, 10 ** (hiss_db / 20), total)
    return out[:, None]


class ProfileTakesTest(unittest.TestCase):
    """A10: what the takes show, and no more: the playing is not the pickup."""

    def test_the_same_pickup_played_an_octave_up_is_not_called_a_brighter_pickup(self):
        # It read "+0.77 octave: sounds made for it come out brighter and harsher; thinner in the lows".
        summary, lines = profile.profile([("Bridge Series", take()),
                                          ("Neck Parallel", take(tuple(2 * pitch for pitch in LOW_RIFF)))])
        said = profile.facts(summary)["settings"]["Neck Parallel"]
        self.assertNotIn("against_reference", said)
        self.assertIn("not marked as the same gesture", said["not_compared"])
        self.assertNotIn("sounds made for it", said["implies"])
        self.assertNotIn("thinner", said["implies"])
        neck = next(line for line in lines if line.startswith("Neck Parallel"))
        self.assertIn("not compared: whether it is louder or brighter than Bridge Series cannot be told", neck)
        self.assertTrue(lines[-1].startswith("To compare settings, record the same gesture on each"))

    def test_one_hard_picked_note_does_not_make_the_pickup_hotter(self):
        # The largest single sample moved "6 dB hotter than presets assume" to 11.
        even, _ = profile.profile([("Bridge Series", take())])
        accented, lines = profile.profile([("Bridge Series", take(accent=2.))])
        self.assertGreater(accented["Bridge Series"]["peak_db"], even["Bridge Series"]["peak_db"] + 5)
        self.assertAlmostEqual(accented["Bridge Series"]["held_peak_db"], even["Bridge Series"]["held_peak_db"],
                               delta=1.5)
        level = lambda summary: profile.facts(summary)["settings"]["Bridge Series"]
        self.assertAlmostEqual(level(accented)["peak_against_typical_humbucker_db"],
                               level(even)["peak_against_typical_humbucker_db"], delta=1.5)
        self.assertIn("firmly held notes", level(accented)["level_from"])
        self.assertIn("in this take firmly held notes peak around", lines[0])
        self.assertIn("the largest single peak is", lines[0])

    def test_takes_of_the_same_gesture_are_compared_band_by_band(self):
        treble = dsp.butter(1, 2000, "high", fs=RATE, output="sos")
        brighter = lambda sound: sound + 2 * dsp.sosfilt(treble, sound)
        summary, lines = profile.profile([("Bridge Series", take(), STRUM), ("Bridge Series", take(), "a riff"),
                                          ("Neck Parallel", take(tilt=brighter), STRUM)])
        self.assertEqual(sorted(summary["Bridge Series"]["by_gesture"]), ["a riff", STRUM])
        self.assertEqual(summary["Bridge Series"]["takes"], 2)
        against = profile.facts(summary)["settings"]["Neck Parallel"]["against_reference"]
        self.assertEqual(against["gesture"], STRUM)
        self.assertEqual(list(against["bands_db"]), ["60-200 Hz", "200 Hz-1 kHz", "1-3 kHz", "3-6 kHz", "6-12 kHz"])
        self.assertGreater(against["bands_db"]["3-6 kHz"], against["bands_db"]["60-200 Hz"] + 5)
        self.assertGreater(against["brightness_octaves"], .2)
        said = profile.facts(summary)["settings"]["Neck Parallel"]["implies"]
        self.assertIn(f"brighter than Bridge Series on the same gesture ({STRUM})", said)
        neck = next(line for line in lines if line.startswith("Neck Parallel"))
        self.assertIn(f"against Bridge Series on the same gesture ({STRUM})", neck)
        json.dumps(profile.facts(summary))

    def test_the_noise_floor_is_read_in_the_silences(self):
        summary, lines = profile.profile([("Bridge Series", take(hiss_db=-60.))])
        self.assertAlmostEqual(summary["Bridge Series"]["noise_db"], -60., delta=2.)
        self.assertIn("noise in the silences at", lines[0])
        self.assertEqual(profile.facts(summary)["settings"]["Bridge Series"]["noise_db"],
                         summary["Bridge Series"]["noise_db"])
        steady = (.1 * np.sin(2 * np.pi * 196 * np.arange(3 * RATE) / RATE))[:, None]      # no silence in it
        self.assertIsNone(profile.profile([("Bridge Series", steady)])[0]["Bridge Series"]["noise_db"])

    def test_bands_are_named_by_the_one_table(self):
        found = profile.describe_take(take())
        self.assertEqual(list(found["bands"]), ["lows", "low mids", "mids", "presence", "air"])
        self.assertEqual(len(found["spectrum_db"]), 25)


if __name__ == "__main__":
    unittest.main()
